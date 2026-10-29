import re
from datetime import datetime

import discord
from discord.ext import commands

import config
import db
import hoja
import ia
from util import buscar_etiqueta, mismo_nombre, normalizar

FORMATO_FECHA = "%Y-%m-%d %H:%M:%S"

# El foro #polizas es la cartera: cuando un alta se emite, Cobi abre ahí una ficha con los datos de la póliza.
# La historia (fotos, DNI, mensajes) queda en el hilo del alta; la ficha linkea a ese hilo


def foro_polizas(bot: commands.Bot) -> discord.ForumChannel | None:
    return discord.utils.find(
        lambda c: isinstance(c, discord.ForumChannel) and mismo_nombre(c.name, config.FORO_POLIZAS), bot.get_all_channels()
    )


def texto_ficha(alta) -> str:
    datos = db.datos_alta(alta)
    asegurado = hoja.nombre_del_asegurado(alta, datos)
    dni = f" · DNI {datos['dni']}" if datos.get("dni") else ""
    patente = f" · Patente **{datos['patente'].upper()}**" if datos.get("patente") else ""
    emitida = datetime.strptime(alta["emitida_en"], FORMATO_FECHA).strftime("%d/%m/%Y") if alta["emitida_en"] else "?"
    return (
        f"🛡️ **{asegurado}**{dni}\n"
        f"🚗 {alta['vehiculo'] or '?'} {alta['anio'] or ''}{patente}\n"
        f"🏦 {alta['compania'] or '?'} · {alta['cobertura'] or '?'}\n"
        f"🔢 N° de solicitud: {alta['n_solicitud'] or '—'}\n"
        f"📄 N° de póliza: {alta['n_poliza'] or '—'}\n"
        f"📅 Emitida el {emitida} · Vigencia: {alta['vigencia'] or '—'}\n"
        f"📂 La solicitud, con toda la historia 👉 <#{alta['hilo_id']}>"
    )


def titulo_ficha(alta) -> str:
    asegurado = hoja.nombre_del_asegurado(alta, db.datos_alta(alta))
    patente = db.datos_alta(alta).get("patente")
    partes = [asegurado, f"{alta['vehiculo'] or '?'} {alta['anio'] or ''}".strip(), (patente or "").upper(), alta["compania"] or ""]
    return " – ".join(p for p in partes if p)[:100]


def vista_ficha(alta_id: int) -> discord.ui.View:
    vista = discord.ui.View(timeout=None)
    vista.add_item(BotonPoliza(alta_id))
    return vista


async def crear_ficha(bot: commands.Bot, alta_id: int):
    alta = db.obtener_alta(alta_id)
    foro = foro_polizas(bot)
    if alta is None or foro is None or alta["poliza_hilo_id"]:
        return
    etiquetas = [e for e in (buscar_etiqueta(foro, alta["compania"] or ""), buscar_etiqueta(foro, alta["tipo"] or "")) if e]
    try:
        nueva = await foro.create_thread(
            name=titulo_ficha(alta), content=texto_ficha(alta), applied_tags=etiquetas, view=vista_ficha(alta_id),
        )
    except discord.HTTPException as e:
        print(f"No pude crear la ficha de la póliza del alta {alta_id}: {e}")
        return
    db.actualizar_alta(alta_id, poliza_hilo_id=nueva.thread.id)


async def borrar_ficha(bot: commands.Bot, alta_id: int):
    # Para "Volver atrás" desde emitida y para /limpiar
    alta = db.obtener_alta(alta_id)
    if alta is None or not alta["poliza_hilo_id"]:
        return
    try:
        hilo = bot.get_channel(alta["poliza_hilo_id"]) or await bot.fetch_channel(alta["poliza_hilo_id"])
        await hilo.delete()
    except discord.HTTPException as e:
        print(f"No pude borrar la ficha de la póliza del alta {alta_id}: {e}")
    db.actualizar_alta(alta_id, poliza_hilo_id=None)


class ModalPoliza(discord.ui.Modal, title="Completar la póliza"):
    def __init__(self, alta):
        super().__init__()
        self.alta_id = alta["id"]
        self.n_poliza = discord.ui.TextInput(max_length=40, required=False, default=alta["n_poliza"])
        self.add_item(discord.ui.Label(text="N° de póliza", component=self.n_poliza))
        self.vigencia = discord.ui.TextInput(placeholder="Por ejemplo: 28/10/2026 al 28/10/2027", max_length=60, required=False, default=alta["vigencia"])
        self.add_item(discord.ui.Label(text="Vigencia", component=self.vigencia))
        # La patente a veces no está porque no se leyó la cédula: acá se carga o se corrige
        self.patente = discord.ui.TextInput(placeholder="Por ejemplo: AB123CD", max_length=10, required=False, default=db.datos_alta(alta).get("patente"))
        self.add_item(discord.ui.Label(text="Patente", component=self.patente))

    async def on_submit(self, interaction: discord.Interaction):
        db.actualizar_alta(self.alta_id, n_poliza=self.n_poliza.value.strip() or None, vigencia=self.vigencia.value.strip() or None)
        patente = re.sub(r"[^A-Za-z0-9]", "", self.patente.value).upper()
        if patente and patente != db.datos_alta(db.obtener_alta(self.alta_id)).get("patente"):
            db.sumar_datos_alta(self.alta_id, {"patente": patente})
        alta = db.obtener_alta(self.alta_id)
        await interaction.response.edit_message(content=texto_ficha(alta), view=vista_ficha(self.alta_id))
        # Si cambió la patente, también el título de la ficha (así se encuentra buscando en el foro)
        if isinstance(interaction.channel, discord.Thread) and interaction.channel.name != titulo_ficha(alta):
            try:
                await interaction.channel.edit(name=titulo_ficha(alta))
            except discord.HTTPException as e:
                print(f"No pude cambiar el título de la ficha del alta {self.alta_id}: {e}")


class BotonPoliza(discord.ui.DynamicItem[discord.ui.Button], template=r"poliza:completar:(?P<id>\d+)"):
    def __init__(self, alta_id: int):
        super().__init__(discord.ui.Button(label="Completar póliza", emoji="✍️", style=discord.ButtonStyle.secondary, custom_id=f"poliza:completar:{alta_id}"))
        self.alta_id = alta_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction):
        alta = db.obtener_alta(self.alta_id)
        if alta is None:
            await interaction.response.send_message("No encontré esta póliza en la base 🤔", ephemeral=True)
            return
        await interaction.response.send_modal(ModalPoliza(alta))


def es_pdf(adjunto: discord.Attachment) -> bool:
    return adjunto.filename.lower().endswith(".pdf") or (adjunto.content_type or "").startswith("application/pdf")


def mismas_personas(uno: str, otro: str) -> bool:
    # "Albornoz Magali Agustina" y "ALBORNOZ, MAGALI A." son la misma: alcanza con que compartan alguna palabra larga
    palabras = lambda t: {p for p in re.findall(r"[a-z]+", normalizar(t)) if len(p) > 2}
    return bool(palabras(uno) & palabras(otro))


async def leer_pdf_de_la_ficha(message: discord.Message, alta) -> list[str]:
    # Lee los PDFs que subieron a la ficha (la póliza, el certificado...) y completa lo que falte.
    # Devuelve los avisos para el productor (si algo no coincide)
    avisos, cambios = [], {}
    datos = db.datos_alta(alta)
    for adjunto in (a for a in message.attachments if es_pdf(a)):
        leido = await ia.generar_json([ia.pdf(await adjunto.read()), ia.leer_prompt("poliza")], tarea="poliza")
        if leido.get("documento") == "otro":
            avisos.append(f"🤔 **{adjunto.filename}** no parece una póliza ni un certificado, no saqué nada de ahí.")
            continue
        # Si es de otra persona no cargamos nada: mejor avisar que ensuciar la ficha
        asegurado = hoja.nombre_del_asegurado(alta, datos)
        if leido.get("asegurado") and not mismas_personas(leido["asegurado"], asegurado):
            avisos.append(
                f"⚠️ En **{adjunto.filename}** el asegurado es **{leido['asegurado']}** y en la ficha **{asegurado}**. "
                "No cargué nada: ¿es la póliza de este cliente?"
            )
            continue
        if leido.get("n_poliza") and leido.get("documento") == "poliza":
            cambios["n_poliza"] = leido["n_poliza"]
        if leido.get("vigencia_desde") and leido.get("vigencia_hasta"):
            cambios["vigencia"] = f"{leido['vigencia_desde']} al {leido['vigencia_hasta']}"
        patente = re.sub(r"[^A-Za-z0-9]", "", leido.get("patente") or "").upper()
        if patente and not datos.get("patente"):
            db.sumar_datos_alta(alta["id"], {"patente": patente})
        elif patente and patente != datos["patente"].upper():
            avisos.append(f"⚠️ En **{adjunto.filename}** la patente es **{patente}** y en la ficha **{datos['patente'].upper()}**. Fijate cuál está bien.")
    if cambios:
        db.actualizar_alta(alta["id"], **cambios)
    return avisos


class Polizas(commands.Cog):
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Si suben un PDF a una ficha de #polizas, Cobi lo lee y la completa (N° de póliza, vigencia, patente)
        hilo = message.channel
        if message.author.bot or not isinstance(hilo, discord.Thread) or not any(es_pdf(a) for a in message.attachments):
            return
        if hilo.parent is None or not mismo_nombre(hilo.parent.name, config.FORO_POLIZAS):
            return
        alta = db.alta_por_ficha(hilo.id)
        if alta is None:
            return
        try:
            async with hilo.typing():
                avisos = await leer_pdf_de_la_ficha(message, alta)
        except Exception as e:
            print(f"Error leyendo la póliza de la ficha del alta {alta['id']}: {e}")
            await message.reply("⚠️ No pude leer el PDF (la IA no respondió). Subilo de nuevo en un rato.")
            return

        alta = db.obtener_alta(alta["id"])
        try:
            await hilo.get_partial_message(hilo.id).edit(content=texto_ficha(alta), view=vista_ficha(alta["id"]))
            if hilo.name != titulo_ficha(alta):
                await hilo.edit(name=titulo_ficha(alta))
        except discord.HTTPException as e:
            print(f"No pude actualizar la ficha del alta {alta['id']}: {e}")
        await message.add_reaction("⚠️" if avisos else "✅")
        if avisos:
            await message.reply("\n".join(avisos), mention_author=False)


async def setup(bot: commands.Bot):
    bot.add_dynamic_items(BotonPoliza)
    await bot.add_cog(Polizas())
