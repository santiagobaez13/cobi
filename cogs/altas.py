import asyncio
import re
from collections import defaultdict
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
import db
import documentos
import hoja
from cogs import polizas
from cogs.plantillas import imagen_plantilla, leer_plantilla
from util import ahora, buscar_etiqueta, clave_compania, mismo_nombre, normalizar, slug

# Cómo guardamos las fechas en la base
FORMATO_FECHA = "%Y-%m-%d %H:%M:%S"

# Los tildes que se llenan con documentos: mientras falte alguno, Cobi lee las imágenes que le pasen
TAREAS_DE_DOCUMENTOS = {"dni_frente", "dni_dorso", "cedula", "gnc_cedula", "gnc_oblea", "pago"}

# Qué muestra cada botón: (texto, emoji, color)
BOTONES = {
    "mano": ("Cargar a mano", "✍️", discord.ButtonStyle.secondary),
    "solicitud": ("N° de solicitud", "🔢", discord.ButtonStyle.secondary),
    "fotos": ("Fotos listas", "📸", discord.ButtonStyle.secondary),
    "mail": ("Mail de las fotos", "📧", discord.ButtonStyle.secondary),
    "omitir": ("Omitir", "⏭️", discord.ButtonStyle.secondary),
    "pendiente": ("Pendiente de emisión", "⏳", discord.ButtonStyle.secondary),
    "emitida": ("Emitida", "✅", discord.ButtonStyle.success),
    "rechazada": ("Rechazada", "❌", discord.ButtonStyle.danger),
    "deshacer": ("Volver atrás", "↩️", discord.ButtonStyle.secondary),
}

# Un candado por alta, así si suben varias fotos juntas no se pisan las actualizaciones de la lista
_candados: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


def fecha_linda(fecha: str | None) -> str:
    # De "2026-09-30 18:40:00" a "30/09/2026"
    return datetime.strptime(fecha, FORMATO_FECHA).strftime("%d/%m/%Y") if fecha else "?"


def ficha_compania(alta) -> dict:
    # Cómo trabaja la compañía de esta alta (ver COMPANIAS_ALTA en config.py). Si no la conocemos, vacía
    return config.COMPANIAS_ALTA.get(clave_compania(alta["compania"]), {})


def texto_mail_fotos(alta) -> str:
    # El mail de siempre para mandarle las fotos a la compañía, con el N° de solicitud ya puesto (solo lo ve quien lo pidió)
    mail = ficha_compania(alta).get("mail_fotos")
    if not mail:
        return "Esta compañía no recibe las fotos por mail 🤔"
    if not alta["n_solicitud"]:
        return "Primero cargá el **N° de solicitud** (🔢), así lo pongo en el asunto 😉"
    asunto = mail["asunto"].replace("{solicitud}", alta["n_solicitud"])
    return (
        f"📧 **Mail para {alta['compania']}** (adjuntale las fotos)\n"
        f"**Asunto**\n```\n{asunto}\n```\n**Cuerpo**\n```\n{mail['cuerpo']}\n```"
    )


def plantilla_del_alta(prefijo: str, alta) -> tuple[str | None, str]:
    # Primero busca la de la compañía ("requisitos_alta_auto_atm"), si no la general ("requisitos_alta_auto").
    # Devuelve el texto y el nombre del archivo que usó (o el general, para avisar qué falta)
    tipo = slug(alta["tipo"] or "Auto")
    compania = clave_compania(alta["compania"])
    general = f"{prefijo}_{tipo}"
    for nombre in (f"{general}_{compania}", general):
        texto = leer_plantilla(nombre, alta["compania"])
        if texto is not None:
            return texto, nombre
    return None, general


def tareas(alta) -> list[dict]:
    # Cada tarea: clave, texto, si está hecha y un detalle opcional ("falta N° de motor").
    # Se calcula siempre de los datos guardados, así no hay forma de que la lista y los datos no coincidan
    d = db.datos_alta(alta)
    es_moto = normalizar(alta["tipo"] or "") == "moto"

    faltan_cedula = [documentos.NOMBRES[c] for c in ("patente", "motor", "chasis") if not d.get(c)]
    lista = [
        {"clave": "dni_frente", "texto": "DNI frente", "hecho": bool(d.get("dni"))},
        {"clave": "dni_dorso", "texto": "DNI dorso", "hecho": bool(d.get("domicilio") or d.get("cuil"))},
        {
            "clave": "cedula", "texto": "Cédula de la moto" if es_moto else "Cédula del vehículo",
            "hecho": not faltan_cedula,
            # Si leyó algo pero no todo, decimos qué falta
            "detalle": f"falta {', '.join(faltan_cedula)}" if faltan_cedula and len(faltan_cedula) < 3 else "",
        },
    ]
    if alta["gnc"]:
        caras = int(d.get("gnc_cedula_caras", 0))
        lista += [
            {"clave": "gnc_cedula", "texto": "Cédula de GNC (ambos lados)", "hecho": caras >= 2, "detalle": "tengo 1 de 2 lados" if caras == 1 else ""},
            {"clave": "gnc_oblea", "texto": "Oblea de GNC", "hecho": bool(d.get("gnc_oblea_leida"))},
        ]
    lista += [
        {"clave": "pago", "texto": "Forma de pago (CBU o tarjeta)", "hecho": bool(d.get("cbu") or d.get("tarjeta_ultimos"))},
        {"clave": "mail", "texto": "Mail", "hecho": bool(d.get("mail"))},
        {"clave": "telefono", "texto": "Teléfono", "hecho": bool(d.get("telefono"))},
        {"clave": "solicitud", "texto": "N° de solicitud", "hecho": bool(alta["n_solicitud"]),
         "detalle": alta["n_solicitud"] or ""},
        {"clave": "fotos", "texto": ficha_compania(alta).get("tarea_fotos", "Fotos"), "hecho": bool(d.get("fotos"))},
    ]

    omitidos = db.omitidos_alta(alta)
    for tarea in lista:
        tarea.setdefault("detalle", "")
        # Si después llegó el dato, gana el dato: la omisión queda sin efecto
        tarea["omitida"] = not tarea["hecho"] and tarea["clave"] in omitidos
    return lista


def pendientes(lista: list[dict], claves: set[str] | None = None) -> list[dict]:
    return [t for t in lista if not t["hecho"] and not t["omitida"] and (claves is None or t["clave"] in claves)]


def enumerar(textos: list[str]) -> str:
    # Con ["a", "b", "c"] arma "a, b y c"
    return textos[0] if len(textos) == 1 else f"{', '.join(textos[:-1])} y {textos[-1]}"


DATOS_DEL_CLIENTE = {"dni_frente", "dni_dorso", "cedula", "gnc_cedula", "gnc_oblea", "pago", "mail", "telefono"}


def siguiente_paso(alta, lista: list[dict]) -> str:
    # Lo que Cobi te dice que hagas ahora: primero los datos del cliente, después las fotos, después el cierre.
    # Lo que falta del cliente nunca te frena: si ya marcaste las fotos, seguimos y te lo recordamos como nota
    compania = alta["compania"] or "la compañía"
    faltan = [t["texto"] if not t["detalle"] else f"{t['texto']} ({t['detalle']})" for t in pendientes(lista, DATOS_DEL_CLIENTE)]
    sin_numero = "" if alta["n_solicitud"] else "\n🔢 Y pasame el N° de solicitud cuando lo tengas (con el botón o escribiendo \"solicitud 123…\" acá)."

    if pendientes(lista, {"fotos"}):
        if faltan:
            return (
                f"👉 Me falta: {enumerar(faltan)}. Pasámelo acá cuando te llegue, en el orden que sea, o ⏭️ omitilo si no hace falta.\n"
                f"Mientras, ya podés ir cargando en la web de {compania} lo que está arriba.{sin_numero}"
            )
        fotos = ficha_compania(alta).get("fotos", "Mandale las fotos a la compañía con el N° de solicitud {solicitud}. Cuando termines, marcá 📸 Fotos listas.")
        solicitud = f"**{alta['n_solicitud']}**" if alta["n_solicitud"] else "(todavía no lo tengo)"
        return f"👉 ¡Datos completos! Terminá de cargarlos en la web de {compania}.\n📸 {fotos.replace('{solicitud}', solicitud)}{sin_numero}"

    if ficha_compania(alta).get("emision_inmediata"):
        paso = "👉 Si ya tocaste **Continuar inspección**, la póliza está emitida: marcá ✅ **Emitida**."
    else:
        paso = f"👉 ¿Cómo quedó? Si {compania} todavía no la emitió, marcá ⏳ **Pendiente de emisión** y te aviso si se demora."
    if faltan:
        paso += f"\n📝 Quedó sin cargar: {enumerar(faltan)}. Si te llega, pasámelo igual."
    return paso + sin_numero


def puede_cerrar(alta, lista: list[dict]) -> bool:
    # Se cierra cuando las fotos están (o se omitieron)
    return not pendientes(lista, {"fotos"})


def encabezado(alta) -> str:
    return f"📋 **Solicitud · {alta['cliente']}** · {alta['compania'] or '?'} · {alta['tipo'] or '?'}"


def tildes(lista: list[dict]) -> str:
    # Los tildes compactos, de a 4 por renglón
    marcas = []
    for t in lista:
        if t["hecho"]:
            marcas.append(f"✅ {t['texto']}")
        elif t["omitida"]:
            marcas.append(f"➖ ~~{t['texto']}~~")
        else:
            marcas.append(f"⬜ {t['texto']}" + (f" ({t['detalle']})" if t["detalle"] else ""))
    return "\n".join("  ·  ".join(marcas[i:i + 4]) for i in range(0, len(marcas), 4))


def contenido_panel(alta) -> str:
    # Arriba la solicitud en texto plano (se va llenando sola), abajo los tildes y qué sigue.
    # Si no entra en los 2000 caracteres de Discord, sacamos de la solicitud los renglones vacíos
    def armar(solo_con_datos: bool) -> str:
        bloque = f"```\n{solicitud_completa(alta, solo_con_datos)}\n```"
        if alta["estado"] == "emitida":
            return f"{encabezado(alta)}\n✅ **Póliza emitida** el {fecha_linda(alta['emitida_en'])} 🎉\n{bloque}"
        if alta["estado"] == "rechazada":
            motivo = f": {alta['motivo_rechazo']}" if alta["motivo_rechazo"] else ""
            return f"{encabezado(alta)}\n❌ **Rechazada** el {fecha_linda(alta['rechazada_en'])}{motivo}\n{bloque}"
        if alta["estado"] == "pendiente_emision":
            return (
                f"{encabezado(alta)}\n⏳ **Pendiente de emisión** desde el {fecha_linda(alta['pendiente_desde'])}\n{bloque}\n"
                f"Cuando salga la póliza, marcá ✅ **Emitida**. Si a los {config.DIAS_ALERTA_CERTIFICADO} días sigue pendiente, te aviso en #{config.CANAL_ALERTAS}."
            )
        lista = tareas(alta)
        return f"{encabezado(alta)}\n{bloque}\n{tildes(lista)}\n\n{siguiente_paso(alta, lista)}"

    texto = armar(False)
    return texto if len(texto) <= 2000 else armar(True)


def vista_panel(alta) -> discord.ui.View:
    vista = discord.ui.View(timeout=None)
    # "Volver atrás" por si tocaron Pendiente, Emitida o Rechazada sin querer
    if alta["estado"] in ("emitida", "rechazada"):
        acciones = ["deshacer"]
    elif alta["estado"] == "pendiente_emision":
        acciones = ["emitida", "rechazada", "deshacer"]
    else:
        lista = tareas(alta)
        acciones = ["mano", "solicitud"]
        if pendientes(lista, {"fotos"}):
            acciones.append("fotos")
            # Si la compañía recibe las fotos por mail (Nación), el botón con el asunto y el cuerpo listos
            if ficha_compania(alta).get("mail_fotos"):
                acciones.append("mail")
        if any(t["clave"] != "solicitud" and not t["hecho"] for t in lista):
            acciones.append("omitir")
        if puede_cerrar(alta, lista):
            acciones += ["emitida"] if ficha_compania(alta).get("emision_inmediata") else ["pendiente", "emitida"]
            acciones.append("rechazada")
    for accion in acciones:
        vista.add_item(BotonAlta(accion, alta["id"]))
    return vista


def solicitud_completa(alta, solo_con_datos: bool = False) -> str:
    # Todo en texto plano y en orden, para ir cargando en la web de la compañía. Lo que falta va con "—"
    d = db.datos_alta(alta)

    def renglones(pares: list[tuple[str, str | None]]) -> str:
        return "\n".join(f"{nombre}: {valor or '—'}" for nombre, valor in pares if valor or not solo_con_datos)

    def campos(claves: list[str]) -> str:
        return renglones([(documentos.NOMBRES[c], d.get(c)) for c in claves])

    # Si todavía no llegó la cédula, usamos lo que sabemos de la cotización
    marca_modelo = " ".join(filter(None, [d.get("marca"), d.get("modelo")])) or f"{alta['vehiculo'] or '—'} (de la cotización)"
    partes_vehiculo = [
        renglones([("Patente", d.get("patente")), ("Marca y modelo", marca_modelo), ("Año", d.get("anio") or alta["anio"])]),
        campos(["motor", "chasis"]),
        "GNC: Sí" if alta["gnc"] else "GNC: No",
    ]
    if alta["gnc"]:
        partes_vehiculo.append(campos(["gnc_equipo", "gnc_cilindro", "gnc_vencimiento", "gnc_oblea_numero", "gnc_oblea_vencimiento"]))
    vehiculo = "\n".join(parte for parte in partes_vehiculo if parte)

    if d.get("cbu"):
        pago = "\n".join(["Débito en CBU", campos(["cbu", "banco", "titular_cuenta"])])
    elif d.get("tarjeta_ultimos"):
        pago = "\n".join(["Tarjeta de crédito", campos(["tarjeta_marca", "tarjeta_banco", "tarjeta_ultimos", "tarjeta_vencimiento"])])
    else:
        pago = "—"

    titular = campos(["apellido", "nombre", "dni", "cuil", "sexo", "fecha_nacimiento", "nacionalidad", "domicilio", "mail", "telefono"])
    texto = (
        f"SOLICITUD · {alta['compania'] or '—'}\nCobertura: {alta['cobertura'] or '—'}\n\n"
        f"TITULAR\n{titular or '—'}\n\n"
        f"VEHÍCULO\n{vehiculo}\n\n"
        f"PAGO\n{pago}"
    )
    if alta["n_solicitud"]:
        texto += f"\n\nN° de solicitud: {alta['n_solicitud']}"
    return texto


async def refrescar_panel(hilo: discord.Thread, alta_id: int, *, abajo: bool = True):
    # abajo=True: borra la lista vieja y la manda de nuevo al final del hilo (cuando llegó algo nuevo).
    # abajo=False: la edita donde está (cuando tocaste un botón de la lista)
    async with _candados[alta_id]:
        alta = db.obtener_alta(alta_id)
        contenido, vista = contenido_panel(alta), vista_panel(alta)
        anterior = alta["panel_id"]
        # En un hilo archivado no se puede editar nada: lo reabrimos (si corresponde, se vuelve a archivar después)
        if getattr(hilo, "archived", False):
            await hilo.edit(archived=False)

        if not abajo and anterior:
            try:
                await hilo.get_partial_message(anterior).edit(content=contenido, view=vista)
                await actualizar_etiqueta(hilo, alta)
                return
            except discord.NotFound:
                pass  # la borraron: la mandamos de nuevo

        if anterior:
            try:
                await hilo.get_partial_message(anterior).delete()
            except discord.HTTPException:
                pass
        nuevo = await hilo.send(contenido, view=vista)
        db.actualizar_alta(alta_id, panel_id=nuevo.id)
        await actualizar_etiqueta(hilo, alta)


async def adoptar_panel(interaction: discord.Interaction, alta):
    # Si tocaron una lista vieja (por ejemplo de un /panel), esa pasa a ser la oficial y borramos la otra
    if interaction.message and alta["panel_id"] != interaction.message.id:
        if alta["panel_id"]:
            try:
                await interaction.channel.get_partial_message(alta["panel_id"]).delete()
            except discord.HTTPException:
                pass
        db.actualizar_alta(alta["id"], panel_id=interaction.message.id)


def etiqueta_de_estado(alta) -> str:
    # La etiqueta del foro sale sola de en qué anda el alta
    if alta["estado"] in ("emitida", "rechazada"):
        return alta["estado"]
    if alta["estado"] == "pendiente_emision" or db.datos_alta(alta).get("fotos"):
        return "fotos"
    if not pendientes(tareas(alta), DATOS_DEL_CLIENTE):
        return "cargada"
    return "esperando_docs"


async def actualizar_etiqueta(hilo: discord.Thread, alta):
    # Cambiamos solo la etiqueta de estado; la de tipo (Auto, Moto...) queda como está
    foro = hilo.parent
    if not isinstance(foro, discord.ForumChannel):
        return
    nombres_estado = {normalizar(n) for n in config.ETIQUETAS_ALTA.values()}
    nueva = buscar_etiqueta(foro, config.ETIQUETAS_ALTA[etiqueta_de_estado(alta)])
    etiquetas = [e for e in hilo.applied_tags if not any(n in normalizar(e.name) for n in nombres_estado)]
    if nueva:
        etiquetas.append(nueva)
    if set(etiquetas) != set(hilo.applied_tags):
        try:
            await hilo.edit(applied_tags=etiquetas)
        except discord.HTTPException as e:
            print(f"No pude cambiar la etiqueta del hilo {hilo.id}: {e}")


async def iniciar_alta(hilo: discord.Thread, alta_id: int):
    # Lo que va apenas se crea el hilo: los requisitos para el cliente y la lista de tareas
    alta = db.obtener_alta(alta_id)
    texto, archivo = plantilla_del_alta("requisitos_alta", alta)
    if texto is None:
        await hilo.send(f"Todavía no tengo los requisitos para **{alta['tipo'] or 'este tipo'}** 😕 Se agregan creando `plantillas/{archivo}.md`.")
    else:
        imagen = imagen_plantilla(archivo)
        await hilo.send(f"📲 **Para mandarle al cliente** (la imagen y este texto) 👇\n```\n{texto}\n```", files=[imagen] if imagen else [])
    await refrescar_panel(hilo, alta_id)


async def leer_imagenes(message: discord.Message, alta) -> tuple[dict, list[str]]:
    # Lee las imágenes del mensaje y devuelve (datos nuevos, avisos)
    imagenes = [(a, tipo) for a in message.attachments if (tipo := documentos.tipo_imagen(a))]
    if not imagenes:
        return {}, []

    async with message.channel.typing():
        try:
            leidos = await documentos.leer([(await a.read(), tipo) for a, tipo in imagenes])
        except Exception as e:
            print(f"Error leyendo documentos del alta {alta['id']}: {e}")
            return {}, ["⚠️ La IA no respondió y no pude leer esas imágenes. Pasámelas de nuevo en un rato; lo demás de la lista anda igual."]

    nuevos, avisos, otros = {}, [], []
    caras_gnc = int(db.datos_alta(alta).get("gnc_cedula_caras", 0))
    for doc in leidos:
        if doc["tipo"] not in documentos.CLAVES or doc["tipo"] == "otro":
            # No es un documento: nos guardamos qué es para contárselo al productor
            otros.append(doc["datos"].get("que_es") or "una foto que no es un documento")
            continue
        if doc["tipo"] == "gnc_cedula":
            caras_gnc += 1
            nuevos["gnc_cedula_caras"] = str(caras_gnc)
        elif doc["tipo"] == "gnc_oblea":
            nuevos["gnc_oblea_leida"] = "sí"
        elif not doc["datos"]:
            continue
        datos, avisos_doc = documentos.datos_para_guardar(doc)
        nuevos.update(datos)
        avisos += avisos_doc
        # ¿La cédula es del auto que se cotizó?
        if doc["tipo"] == "cedula" and (aviso := documentos.comparar_vehiculo(alta["vehiculo"], alta["anio"], doc["datos"])):
            avisos.append(aviso)
        # El número de la tarjeta no lo guardamos: te lo pasamos una vez para que lo copies
        if numero := re.sub(r"\D", "", doc["datos"].get("tarjeta_numero", "")):
            avisos.append(f"💳 Número de la tarjeta para copiar (no lo guardo, por seguridad):\n```\n{numero}\n```")

    # Si no había ningún documento, le decimos qué vimos en vez de dejarlo con la duda
    if otros and not nuevos:
        aviso = f"🤔 Eso no es un documento que yo lea: parece {enumerar(otros)}."
        if any("gnc" in normalizar(o) for o in otros):
            aviso += " La **oblea de GNC** es la calcomanía pegada en el parabrisas, y la **cédula de GNC** es la tarjeta del equipo (de los dos lados)."
        else:
            aviso += " Si son fotos del vehículo, van para la compañía; para la solicitud no las necesito."
        avisos.append(aviso)
    return nuevos, avisos


async def procesar_mensaje(message: discord.Message, alta):
    nuevos, avisos, reacciones = {}, [], []
    # Si lo copiaron de WhatsApp, sacamos los "[hora, fecha] Nombre:" así no se confunden con datos
    texto = documentos.limpiar_whatsapp(message.content)
    guardados = db.datos_alta(alta)

    # Datos escritos en el mensaje: mail, N° de solicitud, CBU (con o sin banco) o teléfono
    # (solicitud y CBU primero, así esos números no se toman por teléfono)
    if mail := documentos.buscar_mail(texto):
        nuevos["mail"] = mail
        reacciones.append("📧")
    banco_escrito = documentos.buscar_banco(texto)
    if n_solicitud := documentos.buscar_n_solicitud(texto):
        db.actualizar_alta(alta["id"], n_solicitud=n_solicitud)
        reacciones.append("🔢")
    elif cbu := documentos.buscar_cbu(texto):
        # Si el banco vino antes en otro mensaje, lo usamos
        banco_previo = guardados.get("banco") if not guardados.get("cbu") else None
        datos_cbu, avisos_cbu = documentos.revisar_cbu(cbu, banco_escrito or banco_previo)
        nuevos.update(datos_cbu)
        avisos += avisos_cbu
        reacciones.append("🏦")
        banco_escrito = None
    elif telefono := documentos.buscar_telefono(texto):
        nuevos["telefono"] = telefono
        reacciones.append("📞")

    if banco_escrito:
        # Vino el banco solo ("Banco frances"): si ya teníamos el CBU, lo sumamos; si no, queda guardado para cuando llegue
        if guardados.get("cbu"):
            datos_cbu, avisos_cbu = documentos.revisar_cbu(guardados["cbu"], banco_escrito)
            nuevos["banco"] = datos_cbu["banco"]
            avisos += [a for a in avisos_cbu if a.startswith("⚠️ Por el número")]
        else:
            nuevos["banco"] = banco_escrito
        reacciones.append("🏦")

    if not guardados.get("telefono") and "telefono" not in nuevos and (telefono := documentos.telefono_del_encabezado(message.content)):
        nuevos["telefono"] = telefono
        reacciones.append("📞")

    # Las imágenes las leemos solo si todavía falta algún documento; si no, son fotos del auto y no gastamos IA
    if pendientes(tareas(alta), TAREAS_DE_DOCUMENTOS) and any(documentos.tipo_imagen(a) for a in message.attachments):
        datos_img, avisos_img = await leer_imagenes(message, alta)
        nuevos.update(datos_img)
        avisos += avisos_img
        reacciones.append("✅" if datos_img else "❔")

    if not nuevos and not avisos and not reacciones:
        return

    if nuevos:
        db.sumar_datos_alta(alta["id"], nuevos)
    for emoji in reacciones:
        try:
            await message.add_reaction(emoji)
        except discord.HTTPException:
            pass
    # Solo hablamos si hay algo que tengas que saber; lo demás se ve en la lista
    if avisos:
        await message.reply("\n".join(avisos))
    await refrescar_panel(message.channel, alta["id"])


class ModalSolicitud(discord.ui.Modal, title="N° de solicitud"):
    def __init__(self, alta):
        super().__init__()
        self.alta_id = alta["id"]
        self.numero = discord.ui.TextInput(label="N° de solicitud", placeholder="El que te dio la web de la compañía",
                                           default=alta["n_solicitud"] or None, max_length=30)
        self.add_item(self.numero)

    async def on_submit(self, interaction: discord.Interaction):
        db.actualizar_alta(self.alta_id, n_solicitud=self.numero.value.strip())
        await interaction.response.defer()
        await refrescar_panel(interaction.channel, self.alta_id, abajo=False)


class ModalCargarAMano(discord.ui.Modal, title="Cargar o corregir datos"):
    def __init__(self, alta):
        super().__init__()
        self.alta_id = alta["id"]
        d = db.datos_alta(alta)
        self.mail = discord.ui.TextInput(label="Mail", default=d.get("mail"), required=False, max_length=100)
        self.telefono = discord.ui.TextInput(label="Teléfono", default=d.get("telefono"), required=False, max_length=30)
        self.cbu = discord.ui.TextInput(label="CBU (si te lo pasaron escrito)", default=d.get("cbu"), required=False, max_length=30)
        self.correcciones = discord.ui.TextInput(
            label="Corregir otros datos (uno por línea)", style=discord.TextStyle.paragraph, required=False, max_length=500,
            placeholder="Campo: valor\nPor ejemplo:\nN° de motor: ABC12345\nDomicilio: Av. Mitre 123, Avellaneda",
        )
        for campo in (self.mail, self.telefono, self.cbu, self.correcciones):
            self.add_item(campo)

    async def on_submit(self, interaction: discord.Interaction):
        nuevos, avisos, no_entendi = {}, [], []
        if self.mail.value.strip():
            nuevos["mail"] = self.mail.value.strip().lower()
        if self.telefono.value.strip():
            nuevos["telefono"] = self.telefono.value.strip()
        if self.cbu.value.strip():
            datos_cbu, avisos = documentos.revisar_cbu(self.cbu.value)
            nuevos.update(datos_cbu)

        # Lo escriben como "Campo: valor": buscamos el campo por su nombre lindo ("N° de motor") o por su clave ("motor")
        por_nombre = {normalizar(nombre): clave for clave, nombre in documentos.NOMBRES.items()}
        por_nombre.update({normalizar(clave): clave for clave in documentos.NOMBRES})
        for linea in self.correcciones.value.splitlines():
            campo, separador, valor = linea.partition(":")
            if not separador or not valor.strip():
                continue
            clave = por_nombre.get(normalizar(campo))
            if clave and clave not in ("tarjeta_numero",):
                nuevos[clave] = valor.strip()
            else:
                no_entendi.append(campo.strip())

        if nuevos:
            db.sumar_datos_alta(self.alta_id, nuevos)
        if no_entendi:
            avisos.append(f"No entendí estos campos: {', '.join(no_entendi)}. Usá los nombres como aparecen en la solicitud de la lista (por ejemplo \"N° de motor\").")
        if avisos:
            await interaction.response.send_message("\n".join(avisos), ephemeral=True)
        else:
            await interaction.response.defer()
        await refrescar_panel(interaction.channel, self.alta_id, abajo=False)


class MenuOmitir(discord.ui.Select):
    # Lista de lo que falta para omitirlo, y de lo omitido para volver a pedirlo
    def __init__(self, alta):
        self.alta_id = alta["id"]
        opciones = []
        for t in tareas(alta):
            if t["clave"] == "solicitud" or t["hecho"]:
                continue
            if t["omitida"]:
                opciones.append(discord.SelectOption(label=f"Volver a pedir: {t['texto']}", value=f"pedir:{t['clave']}", emoji="↩️"))
            else:
                opciones.append(discord.SelectOption(label=f"Omitir: {t['texto']}", value=f"omitir:{t['clave']}", emoji="⏭️"))
        super().__init__(placeholder="¿Qué no hace falta?", options=opciones, min_values=1, max_values=len(opciones))

    async def callback(self, interaction: discord.Interaction):
        alta = db.obtener_alta(self.alta_id)
        omitidos = db.omitidos_alta(alta)
        for valor in self.values:
            accion, clave = valor.split(":", 1)
            if accion == "omitir":
                omitidos.add(clave)
            else:
                omitidos.discard(clave)
        db.guardar_omitidos(self.alta_id, omitidos)
        await interaction.response.edit_message(content="Listo 👍", view=None)
        await refrescar_panel(interaction.channel, self.alta_id, abajo=False)


# Cada botón lleva la acción y el número de alta ("alta:mano:12"). Los paneles viejos de antes tenían
# también la etapa ("alta:siguiente:12:2"); esos los reconocemos para avisar que usen la lista nueva


class BotonAlta(discord.ui.DynamicItem[discord.ui.Button], template=r"alta:(?P<accion>[a-z]+):(?P<id>\d+)(?::\d+)?"):
    def __init__(self, accion: str, alta_id: int):
        texto, emoji, color = BOTONES.get(accion, ("Viejo", "❔", discord.ButtonStyle.secondary))
        super().__init__(discord.ui.Button(label=texto, emoji=emoji, style=color, custom_id=f"alta:{accion}:{alta_id}"))
        self.accion = accion
        self.alta_id = alta_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["accion"], int(match["id"]))

    async def callback(self, interaction: discord.Interaction):
        alta = db.obtener_alta(self.alta_id)
        if alta is None:
            await interaction.response.send_message("No encontré esta solicitud en la base 🤔", ephemeral=True)
            return
        if self.accion not in BOTONES:
            await interaction.response.send_message("Este panel es de la versión vieja de Cobi. Usá `/panel` para traer la lista nueva 👇", ephemeral=True)
            return

        if self.accion == "solicitud":
            await interaction.response.send_modal(ModalSolicitud(alta))
            await adoptar_panel(interaction, alta)
            return
        if self.accion == "mano":
            await interaction.response.send_modal(ModalCargarAMano(alta))
            await adoptar_panel(interaction, alta)
            return
        if self.accion == "mail":
            await interaction.response.send_message(texto_mail_fotos(alta), ephemeral=True)
            return
        if self.accion == "rechazada":
            if alta["estado"] not in ("en_curso", "esperando_docs", "pendiente_emision"):
                await interaction.response.send_message("Esta solicitud ya cambió de estado 🤔", ephemeral=True)
                return
            await interaction.response.send_modal(ModalRechazo(alta))
            return
        if self.accion == "omitir":
            menu = MenuOmitir(alta)
            if not menu.options:
                await interaction.response.send_message("No hay nada para omitir 👍", ephemeral=True)
                return
            vista = discord.ui.View(timeout=300)
            vista.add_item(menu)
            await interaction.response.send_message("Elegí lo que no hace falta (o lo que querés volver a pedir):", view=vista, ephemeral=True)
            await adoptar_panel(interaction, alta)
            return

        momento = ahora().strftime(FORMATO_FECHA)
        if self.accion == "fotos":
            db.sumar_datos_alta(self.alta_id, {"fotos": momento})
        elif self.accion == "pendiente":
            if alta["estado"] not in ("en_curso", "esperando_docs"):
                await interaction.response.send_message("Esta solicitud ya cambió de estado 🤔", ephemeral=True)
                return
            db.actualizar_alta(self.alta_id, estado="pendiente_emision", pendiente_desde=momento)
        elif self.accion == "emitida":
            if alta["estado"] == "emitida":
                await interaction.response.send_message("Ya estaba emitida 👍", ephemeral=True)
                await sincronizar_tarjeta(interaction.client, self.alta_id)
                return
            db.actualizar_alta(self.alta_id, estado="emitida", emitida_en=momento)
        elif self.accion == "deshacer":
            if alta["estado"] in ("emitida", "rechazada"):
                # Vuelve a donde estaba: a pendiente si había pasado por ahí (los días siguen contando desde entonces),
                # si no a la lista en curso
                db.actualizar_alta(
                    self.alta_id, estado="pendiente_emision" if alta["pendiente_desde"] else "en_curso",
                    emitida_en=None, rechazada_en=None, motivo_rechazo=None,
                )
            elif alta["estado"] == "pendiente_emision":
                db.actualizar_alta(self.alta_id, estado="en_curso", pendiente_desde=None, alerta_enviada=None)
            else:
                await interaction.response.send_message("No hay nada para volver atrás 👍", ephemeral=True)
                return

        await acomodar_despues_del_cambio(interaction, alta, self.accion)


# Lo que se le manda al cliente cuando el alta cambia de estado: (plantilla, para qué va adjunto)
MENSAJE_AL_CLIENTE = {
    "pendiente": ("confirmacion_alta", "con la orden de póliza y el certificado"),
    "emitida": ("poliza_emitida", "con la póliza y la tarjeta de circulación"),
}


async def acomodar_despues_del_cambio(interaction: discord.Interaction, alta, accion: str):
    # Ya se cambió el estado en la base (alta es cómo estaba antes): ahora acomodamos el hilo, la tarjeta y la hoja.
    # Primero le avisamos a Discord que lo recibimos (tiene 3 segundos de paciencia) y después acomodamos todo
    await interaction.response.defer()
    alta_id = alta["id"]

    # El botón puede estar en el hilo del alta o en la tarjeta de #pendientes-emision
    en_el_hilo = interaction.channel_id == alta["hilo_id"]
    hilo = interaction.channel if en_el_hilo else await buscar_hilo(interaction.client, alta["hilo_id"])
    if en_el_hilo:
        await adoptar_panel(interaction, alta)

    if hilo is not None:
        # Al quedar pendiente o emitida va una sola vez el mensaje para el cliente, y la lista abajo
        plantilla, adjuntos = MENSAJE_AL_CLIENTE.get(accion, (None, None))
        if plantilla and (texto := leer_plantilla(plantilla, alta["compania"])):
            await hilo.send(f"📲 **Para mandarle al cliente** ({adjuntos}) 👇\n```\n{texto}\n```")
            await refrescar_panel(hilo, alta_id)
        else:
            await refrescar_panel(hilo, alta_id, abajo=False)
        # Pendiente, emitida o rechazada: en el hilo no queda nada por hacer, así que lo archivamos y deja de aparecer
        # en la barra de la izquierda. Sigue estando en el foro, y si alguien escribe se reabre solo.
        # Si volvieron atrás a pendiente, también se archiva; si volvió a en curso, queda abierto para seguir
        vuelve_a_pendiente = accion == "deshacer" and db.obtener_alta(alta_id)["estado"] == "pendiente_emision"
        if accion in ("pendiente", "emitida", "rechazada") or vuelve_a_pendiente:
            await archivar(hilo)

    # Si quedó pendiente aparece su tarjeta en #pendientes-emision; si se emitió o rechazó, desaparece
    await sincronizar_tarjeta(interaction.client, alta_id)

    # La ficha en #polizas: se abre al emitir y se borra si vuelven atrás desde emitida
    if accion == "emitida":
        await polizas.crear_ficha(interaction.client, alta_id)
    elif accion == "deshacer" and alta["estado"] == "emitida":
        await polizas.borrar_ficha(interaction.client, alta_id)

    # La hoja de Google de emitidas: al emitir se carga la fila, y si vuelven atrás desde emitida se saca
    if hoja.activa():
        if accion == "emitida":
            productor = config.EQUIPO.get(interaction.user.id, [interaction.user.display_name])[0]
            hoja.anotar_para_cargar(alta_id, productor)
            if not await hoja.cargar(alta_id) and hilo is not None:
                await hilo.send("⚠️ No pude cargarla en la hoja de emitidas (Google no contestó). La reintento sola en un rato.")
        elif accion == "deshacer" and alta["estado"] == "emitida":
            if not await hoja.sacar(alta_id) and hilo is not None:
                await hilo.send("⚠️ No pude sacarla de la hoja de emitidas: borrá esa fila a mano.")


class ModalRechazo(discord.ui.Modal, title="La compañía la rechazó"):
    def __init__(self, alta):
        super().__init__()
        self.alta = alta
        self.motivo = discord.ui.TextInput(placeholder="Por ejemplo: falló la inspección, auto con daños", max_length=200, required=False)
        self.add_item(discord.ui.Label(text="Motivo", description="Opcional, para acordarse después", component=self.motivo))

    async def on_submit(self, interaction: discord.Interaction):
        alta = db.obtener_alta(self.alta["id"])
        if alta is None or alta["estado"] not in ("en_curso", "esperando_docs", "pendiente_emision"):
            await interaction.response.send_message("Esta solicitud ya cambió de estado 🤔", ephemeral=True)
            return
        db.actualizar_alta(
            alta["id"], estado="rechazada", rechazada_en=ahora().strftime(FORMATO_FECHA),
            motivo_rechazo=self.motivo.value.strip() or None,
        )
        await acomodar_despues_del_cambio(interaction, alta, "rechazada")


def canal_por_nombre(bot, nombre: str) -> discord.TextChannel | None:
    return discord.utils.find(lambda c: isinstance(c, discord.TextChannel) and mismo_nombre(c.name, nombre), bot.get_all_channels())


async def archivar(hilo: discord.Thread):
    try:
        await hilo.edit(archived=True)
    except discord.HTTPException as e:
        print(f"No pude archivar el hilo {hilo.id}: {e}")


async def buscar_hilo(bot, hilo_id: int) -> discord.Thread | None:
    # Si el hilo está archivado no está en memoria, así que lo pedimos a Discord
    try:
        return bot.get_channel(hilo_id) or await bot.fetch_channel(hilo_id)
    except discord.HTTPException:
        return None


def texto_tarjeta(alta) -> str:
    desde = datetime.strptime(alta["pendiente_desde"], FORMATO_FECHA)
    dias = (ahora() - desde).days
    vence = desde + timedelta(days=config.VIGENCIA_CERTIFICADO)
    marca = "🔴" if dias >= config.DIAS_ALERTA_CERTIFICADO else "⏳"
    hace = "desde hoy" if dias == 0 else ("hace 1 día" if dias == 1 else f"hace {dias} días")
    return (
        f"{marca} **{alta['cliente']}** · {alta['compania'] or '?'} · {alta['vehiculo'] or '?'} {alta['anio'] or ''} · "
        f"N° de solicitud {alta['n_solicitud'] or '?'}\n"
        f"Pendiente {hace} · el certificado vence el **{vence:%d/%m}** 👉 <#{alta['hilo_id']}>"
    )


async def sincronizar_tarjeta(bot, alta_id: int):
    # Deja la tarjeta como tiene que estar: si el alta está pendiente, la crea o la actualiza; si no, la borra
    alta = db.obtener_alta(alta_id)
    canal = canal_por_nombre(bot, config.CANAL_PENDIENTES)
    if alta is None or canal is None:
        return
    tarjeta = alta["tarjeta_pendiente_id"]

    if alta["estado"] == "pendiente_emision":
        vista = discord.ui.View(timeout=None)
        vista.add_item(BotonAlta("emitida", alta_id))
        vista.add_item(BotonAlta("rechazada", alta_id))
        if tarjeta:
            try:
                await canal.get_partial_message(tarjeta).edit(content=texto_tarjeta(alta), view=vista)
                return
            except discord.NotFound:
                pass  # la borraron a mano: la mandamos de nuevo
        nueva = await canal.send(texto_tarjeta(alta), view=vista)
        db.actualizar_alta(alta_id, tarjeta_pendiente_id=nueva.id)
    elif tarjeta:
        try:
            await canal.get_partial_message(tarjeta).delete()
        except discord.HTTPException:
            pass
        db.actualizar_alta(alta_id, tarjeta_pendiente_id=None)


class Altas(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.revisar_pendientes.start()

    def cog_unload(self):
        self.revisar_pendientes.cancel()

    @app_commands.command(name="panel", description="Vuelve a poner la lista de la solicitud al final del hilo")
    async def panel(self, interaction: discord.Interaction):
        alta = db.alta_por_hilo(interaction.channel_id)
        if alta is None:
            await interaction.response.send_message("Esto no es un hilo de solicitud que yo conozca 🤔", ephemeral=True)
            return
        await interaction.response.send_message("Ahí va 👇", ephemeral=True)
        await refrescar_panel(interaction.channel, alta["id"])

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not isinstance(message.channel, discord.Thread):
            return
        alta = db.alta_por_hilo(message.channel.id)
        if alta is None or alta["estado"] in ("emitida", "rechazada"):
            return
        await procesar_mensaje(message, alta)

    @tasks.loop(hours=1)
    async def revisar_pendientes(self):
        # Una vez por hora (y al arrancar): ponemos al día las tarjetas de #pendientes-emision
        # y avisamos en #alertas las que pasaron los X días (una sola vez cada una)
        for alta in [*db.altas_pendientes(), *db.altas_con_tarjeta_sobrante()]:
            try:
                await sincronizar_tarjeta(self.bot, alta["id"])
            except discord.HTTPException as e:
                print(f"No pude actualizar la tarjeta del alta {alta['id']}: {e}")

        # Las emitidas que no llegaron a la hoja de Google porque falló en el momento
        if hoja.activa():
            for alta in db.altas_para_la_hoja():
                await hoja.cargar(alta["id"])

        limite = (ahora() - timedelta(days=config.DIAS_ALERTA_CERTIFICADO)).strftime(FORMATO_FECHA)
        altas = db.altas_para_alertar(limite)
        if not altas:
            return

        canal = canal_por_nombre(self.bot, config.CANAL_ALERTAS)
        if canal is None:
            print(f"Hay {len(altas)} alertas de certificados pero no encontré el canal #{config.CANAL_ALERTAS}")
            return

        for alta in altas:
            vence = datetime.strptime(alta["pendiente_desde"], FORMATO_FECHA) + timedelta(days=config.VIGENCIA_CERTIFICADO)
            try:
                await canal.send(
                    f"⚠️ **Certificado por vencer** · {alta['cliente']} · {alta['compania']}\n"
                    f"Está pendiente de emisión desde el {fecha_linda(alta['pendiente_desde'])} "
                    f"(N° de solicitud {alta['n_solicitud'] or '?'}) y el certificado vence el **{vence:%d/%m/%Y}**. "
                    f"Hay que reclamarle la póliza a la compañía.\n👉 <#{alta['hilo_id']}>"
                )
                db.actualizar_alta(alta["id"], alerta_enviada=ahora().strftime(FORMATO_FECHA))
            except discord.HTTPException as e:
                print(f"No pude mandar la alerta del alta {alta['id']}: {e}")

    @revisar_pendientes.before_loop
    async def antes_de_revisar(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    bot.add_dynamic_items(BotonAlta)
    await bot.add_cog(Altas(bot))
