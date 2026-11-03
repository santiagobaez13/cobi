import json

import discord
from discord.ext import commands

import config
import db
import hoja
import ia
from cogs.plantillas import leer_plantilla
from cogs.polizas import es_pdf
from util import ahora, mismo_nombre, normalizar

FORMATO_FECHA = "%Y-%m-%d %H:%M:%S"

# #cobranzas: se sube el informe de débitos rechazados que manda la compañía (el PDF) y Cobi arma la lista
# con un desplegable: elegís el cliente y te da el mensaje para mandarle. Cuando se avisó a todos, lo festeja
POR_MENU = 25    # lo máximo que deja Discord en un desplegable
MAX_MENUS = 5    # y no entran más de 5 desplegables por mensaje (125 clientes, sobra)
LARGO_LISTA = 1900
# Si el motivo dice solo "rechazado" no aporta nada; si dice otra cosa ("cuenta cerrada") se lo mostramos al productor
MOTIVOS_SIN_INFO = {"", "rechazado", "rechazada"}


def es_canal_de_cobranzas(canal) -> bool:
    # Puede ser un canal de texto o un foro (ahí el informe queda adentro de un hilo)
    nombre = canal.parent.name if isinstance(canal, discord.Thread) and canal.parent else getattr(canal, "name", "")
    return mismo_nombre(nombre, config.CANAL_COBRANZAS)


def rechazos_de(cobranza) -> list[dict]:
    return json.loads(cobranza["rechazos"])


def avisados_de(cobranza) -> set[int]:
    return set(json.loads(cobranza["avisados"]))


def a_numero(importe: str) -> float:
    try:
        return float(str(importe).replace("$", "").replace(".", "").replace(",", ".").strip())
    except ValueError:
        return 0.0


def plata(numero: float) -> str:
    # 215889.82 → "$215.889,82"
    return "$" + f"{numero:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def agrupar(renglones: list[dict]) -> list[dict]:
    # A veces un mismo cliente figura más de una vez (dos cobros de la misma póliza, a veces de la misma cuota).
    # Un cliente = un mensaje: juntamos por póliza, sumamos el importe y guardamos el detalle para avisarle al productor
    por_poliza: dict[str, dict] = {}
    for r in renglones:
        clave = str(r.get("poliza") or r.get("dni") or r.get("nombre_completo"))
        if clave not in por_poliza:
            por_poliza[clave] = {**r, "detalle": []}
        por_poliza[clave]["detalle"].append({"cuota": r.get("cuota"), "importe": r.get("importe"), "motivo": r.get("motivo")})
    for grupo in por_poliza.values():
        cuotas = list(dict.fromkeys(str(d["cuota"]) for d in grupo["detalle"] if d["cuota"]))
        grupo["cuota"] = " y ".join(cuotas)
        grupo["importe"] = plata(sum(a_numero(d["importe"]) for d in grupo["detalle"]))[1:]
    return list(por_poliza.values())


def texto_cuotas(rechazo: dict) -> str:
    # "la cuota 1" / "las cuotas 3 y 4"
    cuota = rechazo.get("cuota") or ""
    if not cuota:
        return "la cuota"
    return f"las cuotas {cuota}" if " y " in cuota else f"la cuota {cuota}"


def texto_lista(cobranza) -> str:
    rechazos, avisados = rechazos_de(cobranza), avisados_de(cobranza)
    total = sum(a_numero(r.get("importe")) for r in rechazos)
    fecha = f" · informe del {cobranza['fecha_informe']}" if cobranza["fecha_informe"] else ""
    encabezado = (
        f"💸 **Débitos rechazados de {cobranza['compania'] or '?'}**{fecha}\n"
        f"{len(rechazos)} cliente{'s' if len(rechazos) != 1 else ''} · total **{plata(total)}** · "
        f"avisados **{len(avisados)} de {len(rechazos)}**\n"
    )
    pie = "\n🎉 **¡Terminamos con todos!**" if len(avisados) >= len(rechazos) else "\n👇 Elegí a quién le armo el mensaje"
    renglones = []
    for i, r in enumerate(rechazos):
        renglon = f"{'✅' if i in avisados else '⬜'} **{r.get('nombre_completo') or '?'}** · póliza {r.get('poliza') or '?'} · ${r.get('importe') or '?'}"
        # Si son muchos, que el mensaje no pase el máximo de Discord
        if len(encabezado) + len("\n".join(renglones + [renglon])) + len(pie) + 30 > LARGO_LISTA:
            renglones.append(f"…y {len(rechazos) - i} más (están en el desplegable)")
            break
        renglones.append(renglon)
    return encabezado + "\n".join(renglones) + pie


def vista_lista(cobranza) -> discord.ui.View:
    vista = discord.ui.View(timeout=None)
    cantidad = len(rechazos_de(cobranza))
    for pagina in range(min((cantidad + POR_MENU - 1) // POR_MENU, MAX_MENUS)):
        vista.add_item(MenuRechazos(cobranza["id"], pagina))
    return vista


def solicitud_del_cliente(rechazo: dict):
    # Si el cliente pasó por Cobi, lo encontramos por el DNI (sirve para el nombre de pila y para linkear su póliza)
    dni = "".join(c for c in str(rechazo.get("dni") or "") if c.isdigit())
    altas = db.altas_por_dni(dni) if dni else []
    return altas[0] if altas else None


def mensaje_para(cobranza, rechazo: dict, alta) -> str:
    # El nombre del DNI que cargamos manda; si no lo tenemos, el que sacó la IA del informe
    nombre = rechazo.get("nombre") or ""
    if alta is not None and (del_dni := db.datos_alta(alta).get("nombre")):
        nombre = del_dni.split()[0].title()
    texto = leer_plantilla("debito_rechazado", cobranza["compania"] or "la compañía")
    if not nombre:
        texto = texto.replace("Hola {nombre},", "Hola,")
    for clave, valor in {"nombre": nombre, "cuotas": texto_cuotas(rechazo), "poliza": rechazo.get("poliza"), "importe": rechazo.get("importe")}.items():
        texto = texto.replace("{" + clave + "}", str(valor or "—"))
    return texto


class MenuRechazos(discord.ui.DynamicItem[discord.ui.Select], template=r"cobranza:(?P<id>\d+):(?P<pagina>\d+)"):
    def __init__(self, cobranza_id: int, pagina: int):
        cobranza = db.obtener_cobranza(cobranza_id)
        rechazos = rechazos_de(cobranza) if cobranza else []
        avisados = avisados_de(cobranza) if cobranza else set()
        desde = pagina * POR_MENU
        opciones = [
            discord.SelectOption(
                label=(r.get("nombre_completo") or "?")[:100],
                value=str(i),
                description=f"Póliza {r.get('poliza') or '?'} · cuota {r.get('cuota') or '?'} · ${r.get('importe') or '?'}"[:100],
                emoji="✅" if i in avisados else "⬜",
            )
            for i, r in enumerate(rechazos[desde:desde + POR_MENU], start=desde)
        ] or [discord.SelectOption(label="(no encontré el informe)", value="-1")]
        hasta = min(desde + POR_MENU, len(rechazos))
        texto = "Elegí a quién le armo el mensaje" if len(rechazos) <= POR_MENU else f"Clientes {desde + 1} a {hasta}"
        super().__init__(discord.ui.Select(placeholder=texto, options=opciones, custom_id=f"cobranza:{cobranza_id}:{pagina}"))
        self.cobranza_id = cobranza_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["id"]), int(match["pagina"]))

    async def callback(self, interaction: discord.Interaction):
        cobranza = db.obtener_cobranza(self.cobranza_id)
        indice = int(self.item.values[0])
        if cobranza is None or not 0 <= indice < len(rechazos_de(cobranza)):
            await interaction.response.send_message("No encontré este informe en la base 🤔", ephemeral=True)
            return
        rechazo = rechazos_de(cobranza)[indice]
        alta = solicitud_del_cliente(rechazo)

        # Lo que es para el productor va afuera del bloque; adentro, solo lo que se le manda al cliente
        notas = []
        detalle = rechazo.get("detalle") or []
        if len(detalle) > 1:
            cobros = " + ".join(f"${d['importe']} (cuota {d['cuota']})" for d in detalle)
            notas.append(f"⚠️ Figura {len(detalle)} veces en el informe: {cobros}. En el mensaje va el total, revisalo antes de mandarlo.")
        motivos = {d.get("motivo") for d in detalle} if detalle else {rechazo.get("motivo")}
        motivos = [m for m in motivos if normalizar(m or "") not in MOTIVOS_SIN_INFO]
        if motivos:
            notas.append(f"❗ Motivo del rechazo: **{' / '.join(motivos)}**")
        if alta is not None:
            donde = f"<#{alta['poliza_hilo_id']}>" if alta["poliza_hilo_id"] else f"<#{alta['hilo_id']}>"
            notas.append(f"📂 {hoja.nombre_del_asegurado(alta, db.datos_alta(alta))} 👉 {donde}")
        await interaction.response.send_message(
            f"📲 Para mandarle a **{rechazo.get('nombre_completo') or '?'}** (📎 acordate de adjuntarle el cupón de pago):\n"
            f"```\n{mensaje_para(cobranza, rechazo, alta)}\n```" + ("\n" + "\n".join(notas) if notas else ""),
            ephemeral=True,
        )

        # Lo marcamos como avisado y actualizamos la lista (el desplegable vuelve a quedar sin nada elegido)
        avisados = avisados_de(cobranza) | {indice}
        db.actualizar_cobranza(self.cobranza_id, avisados=json.dumps(sorted(avisados)))
        cobranza = db.obtener_cobranza(self.cobranza_id)
        try:
            await interaction.message.edit(content=texto_lista(cobranza), view=vista_lista(cobranza))
        except discord.HTTPException as e:
            print(f"No pude actualizar la lista de la cobranza {self.cobranza_id}: {e}")

        if len(avisados) >= len(rechazos_de(cobranza)) and not cobranza["terminada"]:
            db.actualizar_cobranza(self.cobranza_id, terminada=ahora().strftime(FORMATO_FECHA))
            await interaction.channel.send(
                f"🎉 **¡Terminamos con todos!** Ya se armó el mensaje para los {len(avisados)} rechazos de {cobranza['compania'] or 'la compañía'}. 💪"
            )


class Cobranzas(commands.Cog):
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None or not es_canal_de_cobranzas(message.channel):
            return
        for adjunto in (a for a in message.attachments if es_pdf(a)):
            try:
                async with message.channel.typing():
                    leido = await ia.generar_json([ia.pdf(await adjunto.read()), ia.leer_prompt("rechazos")], tarea="cobranzas")
            except Exception as e:
                print(f"Error leyendo el informe de rechazos {adjunto.filename}: {e}")
                await message.reply(f"⚠️ No pude leer **{adjunto.filename}** (la IA no respondió). Subilo de nuevo en un rato.", mention_author=False)
                continue
            if not isinstance(leido, dict) or not leido.get("es_informe") or not leido.get("rechazos"):
                await message.reply(f"🤔 **{adjunto.filename}** no parece un informe de débitos rechazados.", mention_author=False)
                continue

            cobranza_id = db.nueva_cobranza(
                leido.get("compania"), leido.get("fecha_informe"), agrupar(leido["rechazos"]), message.channel.id, ahora().strftime(FORMATO_FECHA)
            )
            cobranza = db.obtener_cobranza(cobranza_id)
            enviado = await message.reply(texto_lista(cobranza), view=vista_lista(cobranza), mention_author=False)
            db.actualizar_cobranza(cobranza_id, mensaje_id=enviado.id)


async def setup(bot: commands.Bot):
    bot.add_dynamic_items(MenuRechazos)
    await bot.add_cog(Cobranzas())
