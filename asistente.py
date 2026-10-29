import re
from pathlib import Path

import discord

import config
import db
import hoja
import ia
from util import normalizar

# El asistente de #general: responde preguntas de seguros, resume clientes y arma respuestas para mandarles.
# Sabe lo que hay en conocimiento/ (la biblioteca que escribe el equipo) y, si se habla de un cliente,
# lo que hay en la base y en el hilo de su solicitud
CARPETA_CONOCIMIENTO = Path(__file__).parent / "conocimiento"
MENSAJES_DEL_HILO = 40
LARGO_POR_MENSAJE = 800

# Palabras que indican que no quieren solo el link a la solicitud sino que Cobi piense algo
PEDIDOS = re.compile(
    r"\b(resumen|resumi\w*|como (viene|va|anda|quedo)|en que (quedo|esta)|que paso|cubre|incluye|tiene|armame|arma|armar|"
    r"respuesta|responde\w*|contesta\w*|explica\w*|que es|cuant[oa]s?|cuando|por que|porque|sirve|puede|conviene|"
    r"diferencia|grua|granizo|franquicia|siniestro|choc\w*|ayuda\w*)\b"
)
# Datos que no hace falta mandarle a la IA
DATOS_QUE_NO_VAN = {"cbu", "tarjeta_numero", "tarjeta_ultimos", "tarjeta_vencimiento", "cuil"}


def es_pedido(texto: str) -> bool:
    return "?" in texto or PEDIDOS.search(normalizar(texto)) is not None


def biblioteca() -> str:
    # Se lee cada vez, así si suman o corrigen un .md no hace falta reiniciar a Cobi
    partes = [f"## Archivo: {archivo.name}\n\n{archivo.read_text(encoding='utf-8').strip()}" for archivo in sorted(CARPETA_CONOCIMIENTO.glob("*.md"))]
    return "\n\n".join(partes) or "(Todavía no hay nada en la biblioteca.)"


def instrucciones() -> str:
    return f"{ia.leer_prompt('asistente')}\n\n# BIBLIOTECA\n\n{biblioteca()}"


async def contexto_de_la_solicitud(bot: discord.Client, alta) -> str:
    datos = {k: v for k, v in db.datos_alta(alta).items() if k not in DATOS_QUE_NO_VAN}
    estado = {
        "pendiente_emision": f"pendiente de emisión desde {alta['pendiente_desde']}",
        "emitida": f"emitida el {alta['emitida_en']}",
        "rechazada": f"rechazada el {alta['rechazada_en']}" + (f" (motivo: {alta['motivo_rechazo']})" if alta["motivo_rechazo"] else ""),
    }.get(alta["estado"], "en curso (todavía juntando datos)")
    renglones = [
        f"### Solicitud de {hoja.nombre_del_asegurado(alta, db.datos_alta(alta))} (escrito al aceptar: {alta['cliente']})",
        f"- Compañía: {alta['compania']} · Cobertura elegida: {alta['cobertura']}",
        f"- Vehículo: {alta['vehiculo']} {alta['anio']} · Tipo: {alta['tipo']} · GNC: {'sí' if alta['gnc'] else 'no'}",
        f"- Estado: {estado} · Creada: {alta['creada']}",
        f"- N° de solicitud: {alta['n_solicitud'] or '—'} · N° de póliza: {alta['n_poliza'] or '—'} · Vigencia: {alta['vigencia'] or '—'}",
        f"- Datos cargados: {datos}",
    ]

    # Lo que se habló en el hilo: ahí están la cobertura elegida completa, los avisos y lo que fueron pasando
    try:
        hilo = bot.get_channel(alta["hilo_id"]) or await bot.fetch_channel(alta["hilo_id"])
        mensajes = [m async for m in hilo.history(limit=MENSAJES_DEL_HILO)]
        primero = await hilo.fetch_message(hilo.id) if isinstance(hilo.parent, discord.ForumChannel) else None
        if primero and all(m.id != primero.id for m in mensajes):
            mensajes.append(primero)
        renglones.append("\nLo que se habló en el hilo (del más viejo al más nuevo):")
        for m in sorted(mensajes, key=lambda m: m.created_at):
            adjuntos = f" [{len(m.attachments)} adjunto(s)]" if m.attachments else ""
            texto = (m.content or "").strip()[:LARGO_POR_MENSAJE]
            if texto or adjuntos:
                renglones.append(f"[{m.created_at:%d/%m %H:%M}] {m.author.display_name}: {texto}{adjuntos}")
    except discord.HTTPException as e:
        renglones.append(f"(No pude leer el hilo: {e})")
    return "\n".join(renglones)


async def responder(bot: discord.Client, message: discord.Message, altas: list) -> str:
    # altas: las solicitudes de las que se está hablando (las que encontró el buscador), puede ser ninguna
    partes = []
    for alta in altas[:3]:
        partes.append(await contexto_de_la_solicitud(bot, alta))

    # Si le están respondiendo a un mensaje de Cobi ("armale la respuesta"), le pasamos ese mensaje para que siga el hilo
    anterior = message.reference.resolved if message.reference else None
    if isinstance(anterior, discord.Message) and anterior.author == bot.user:
        partes.append(f"### Mensaje anterior de Cobi al que le están respondiendo\n{anterior.content[:1500]}")

    quien = config.EQUIPO.get(message.author.id, [message.author.display_name])[0]
    pregunta = re.sub(r"<@[!&]?\d+>", " ", message.content).strip()
    datos = "\n\n".join(partes) if partes else "(No se habló de ningún cliente en particular.)"
    return await ia.generar([f"# DATOS DEL CLIENTE\n\n{datos}\n\n# PREGUNTA DE {quien.upper()}\n\n{pregunta}"], sistema=instrucciones(), tarea="asistente")


def en_pedazos(texto: str, largo: int = 1900) -> list[str]:
    # Discord no deja mandar más de 2000 caracteres: cortamos por párrafos, sin romper los bloques de código
    pedazos, actual = [], ""
    for parrafo in texto.split("\n\n"):
        if actual and len(actual) + len(parrafo) + 2 > largo and actual.count("```") % 2 == 0:
            pedazos.append(actual)
            actual = parrafo
        else:
            actual = f"{actual}\n\n{parrafo}" if actual else parrafo
    pedazos.append(actual)
    return [p[i:i + 2000] for p in pedazos for i in range(0, len(p), 2000)]
