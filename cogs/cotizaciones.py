import io
import re

import discord
from discord.ext import commands

import config
import db
import ia
from cogs.altas import iniciar_alta
from util import buscar_etiqueta, mismo_nombre

# La primera línea del mensaje de Cobi muestra en qué quedó la cotización
LINEA_ESTADO = {
    "pendiente": "⏳ **Esperando respuesta del cliente**",
    "no_avanzo": "❌ **No avanzó**",
    "aceptada": "✅ **Aceptó**",
}


class ErrorAlta(Exception):
    """Un problema que le avisamos al equipo tal cual, sin asustar con errores técnicos"""


def es_canal_de_cotizaciones(canal) -> bool:
    # Si es un hilo nos fijamos en el canal de arriba, así también anda en hilos de #bot-pruebas
    if isinstance(canal, discord.Thread) and canal.parent:
        canal = canal.parent
    return any(mismo_nombre(getattr(canal, "name", ""), nombre) for nombre in config.CANALES_PDF)


async def armar_mensaje(pdf_bytes: bytes) -> str:
    texto = await ia.generar([ia.pdf(pdf_bytes), ia.leer_prompt("cotizacion")])
    return texto.replace("```", "")


def cambiar_linea_estado(contenido: str, nueva_linea: str) -> str:
    _, _, resto = contenido.partition("\n")
    return f"{nueva_linea}\n{resto}"


async def texto_de_la_cotizacion(mensaje: discord.Message) -> str:
    # El texto está en el bloque de código, o en el .txt si quedó largo
    encontrado = re.search(r"```\n(.*?)\n```", mensaje.content, re.S)
    if encontrado:
        return encontrado.group(1)
    if mensaje.attachments:
        return (await mensaje.attachments[0].read()).decode("utf-8")
    return ""


def coberturas_del_mensaje(texto: str) -> list[str]:
    # Sacamos las coberturas del mensaje que ya armó Cobi, así el menú abre al toque sin llamar a la IA
    opciones = re.findall(r"^\* (.+?): \$", texto, re.M)
    if not opciones:
        # Cotización de una sola cobertura: viene como "🛡️ Cobertura: ..."
        opciones = re.findall(r"^\S*\s*Cobertura: (.+)$", texto, re.M)
    sin_repetir = dict.fromkeys(o.strip()[:100] for o in opciones)
    return list(sin_repetir)[:25]


def botones_pendiente(cotizacion_id: int) -> discord.ui.View:
    vista = discord.ui.View(timeout=None)
    vista.add_item(BotonAcepto(cotizacion_id))
    vista.add_item(BotonNoAvanzo(cotizacion_id))
    return vista


def botones_no_avanzo(cotizacion_id: int) -> discord.ui.View:
    vista = discord.ui.View(timeout=None)
    vista.add_item(BotonReabrir(cotizacion_id))
    return vista


async def crear_alta(interaction: discord.Interaction, cotizacion_id: int, cobertura: str, cliente: str, telefono: str, n_solicitud: str) -> discord.Thread:
    foro = discord.utils.find(lambda f: mismo_nombre(f.name, config.FORO_ALTAS), interaction.guild.forums)
    if foro is None:
        raise ErrorAlta(f"No encontré el foro **{config.FORO_ALTAS}** 🤔 Fijate que exista y que Cobi lo pueda ver.")

    # Releemos el PDF del mensaje original para armar la cobertura elegida
    cotizacion = db.obtener_cotizacion(cotizacion_id)
    try:
        canal = interaction.guild.get_channel_or_thread(cotizacion["canal_id"]) or await interaction.client.fetch_channel(cotizacion["canal_id"])
        mensaje_pdf = await canal.fetch_message(cotizacion["mensaje_pdf_id"])
    except discord.NotFound:
        raise ErrorAlta("No encontré el mensaje con el PDF, ¿lo borraron? 🤔")
    adjunto = discord.utils.get(mensaje_pdf.attachments, filename=cotizacion["pdf_nombre"])
    if adjunto is None:
        raise ErrorAlta("El mensaje de la cotización ya no tiene el PDF 🤔")

    datos = await ia.generar_json([ia.pdf(await adjunto.read()), ia.leer_prompt("cobertura_elegida", cobertura=cobertura)])
    mensaje = datos["mensaje"].replace("```", "").strip()

    etiquetas = [e for e in (buscar_etiqueta(foro, config.ETIQUETAS_ALTA["esperando_docs"]), buscar_etiqueta(foro, datos.get("tipo", ""))) if e]
    titulo = f"{cliente} – {datos['vehiculo_corto']} {datos['anio']} – {datos['compania']}"[:100]
    encabezado = f"🧾 **Alta de {cliente}**\n📎 Cotización: {interaction.message.jump_url}\n\n🛡️ Cobertura elegida, lista para WhatsApp:"

    contenido = f"{encabezado}\n```\n{mensaje}\n```"
    if len(contenido) <= 2000:
        creado = await foro.create_thread(name=titulo, content=contenido, applied_tags=etiquetas)
    else:
        archivo = discord.File(io.BytesIO(mensaje.encode("utf-8")), filename="cobertura_elegida.txt")
        creado = await foro.create_thread(name=titulo, content=encabezado, file=archivo, applied_tags=etiquetas)

    alta_id = db.nueva_alta(
        cotizacion_id, cliente, datos["compania"], datos["vehiculo_corto"], str(datos["anio"]), datos.get("tipo", ""), cobertura,
        creado.thread.id, gnc=bool(datos.get("gnc")), datos={"telefono": telefono} if telefono else None,
    )
    if n_solicitud:
        db.actualizar_alta(alta_id, n_solicitud=n_solicitud)
    await iniciar_alta(creado.thread, alta_id)
    return creado.thread


class ModalAcepto(discord.ui.Modal, title="¡Aceptó! Armemos el alta"):
    def __init__(self, cotizacion_id: int, coberturas: list[str]):
        super().__init__()
        self.cotizacion_id = cotizacion_id

        # Si pudimos leer las coberturas va un menú; si no, que la escriban a mano
        if coberturas:
            self.cobertura = discord.ui.Select(options=[discord.SelectOption(label=c, value=c) for c in coberturas])
        else:
            self.cobertura = discord.ui.TextInput(placeholder="Por ejemplo: Terceros Completo", max_length=100)
        self.add_item(discord.ui.Label(text="¿Qué cobertura eligió?", component=self.cobertura))

        self.cliente = discord.ui.TextInput(placeholder="Por ejemplo: Pérez Juan", max_length=60)
        self.add_item(discord.ui.Label(text="Apellido y nombre del cliente", component=self.cliente))

        # El N° de solicitud sale apenas abrís el alta en la web de la compañía
        self.solicitud = discord.ui.TextInput(placeholder="El que te dio la web de la compañía", max_length=30, required=False)
        self.add_item(discord.ui.Label(text="N° de solicitud", description="Si todavía no lo tenés, lo pasás después", component=self.solicitud))

        # El teléfono lo tenés a mano del WhatsApp; si no, se carga después en el hilo
        self.telefono = discord.ui.TextInput(placeholder="Por ejemplo: 11 2345-6789", max_length=30, required=False)
        self.add_item(discord.ui.Label(text="Teléfono (WhatsApp)", description="Opcional, lo podés pasar después", component=self.telefono))

    async def on_submit(self, interaction: discord.Interaction):
        if isinstance(self.cobertura, discord.ui.Select):
            cobertura = self.cobertura.values[0]
        else:
            cobertura = self.cobertura.value.strip()
        cliente = self.cliente.value.strip()
        telefono = self.telefono.value.strip()
        n_solicitud = self.solicitud.value.strip()
        quien = interaction.user.display_name

        if not db.tomar_cotizacion(self.cotizacion_id, quien):
            await interaction.response.send_message("Esta cotización ya no está pendiente 🤔", ephemeral=True)
            return

        # La IA tarda unos segundos: le avisamos a Discord que estamos laburando
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            hilo = await crear_alta(interaction, self.cotizacion_id, cobertura, cliente, telefono, n_solicitud)
        except ErrorAlta as e:
            db.cambiar_estado(self.cotizacion_id, "pendiente", quien)
            await interaction.followup.send(str(e), ephemeral=True)
            return
        except Exception as e:
            print(f"Error armando el alta de la cotización {self.cotizacion_id}: {e}")
            db.cambiar_estado(self.cotizacion_id, "pendiente", quien)
            await interaction.followup.send("Uy, algo falló armando el alta 😕 Probá de nuevo en un ratito.", ephemeral=True)
            return

        db.cambiar_estado(self.cotizacion_id, "aceptada", quien)
        await interaction.message.edit(
            content=cambiar_linea_estado(interaction.message.content, f"{LINEA_ESTADO['aceptada']} · {cliente} · lo marcó {quien} · {hilo.mention}"),
            view=None,
        )
        await interaction.followup.send(f"Listo, armé el alta de **{cliente}** 👉 {hilo.mention}", ephemeral=True)


# Cada botón lleva el número de cotización en su custom_id ("cotiz:acepto:12"),
# así Cobi sabe cuál es aunque lo hayan reiniciado en el medio


class BotonAcepto(discord.ui.DynamicItem[discord.ui.Button], template=r"cotiz:acepto:(?P<id>\d+)"):
    def __init__(self, cotizacion_id: int):
        super().__init__(discord.ui.Button(
            label="Aceptó", emoji="✅", style=discord.ButtonStyle.success, custom_id=f"cotiz:acepto:{cotizacion_id}",
        ))
        self.cotizacion_id = cotizacion_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction):
        cotizacion = db.obtener_cotizacion(self.cotizacion_id)
        if cotizacion is None or cotizacion["estado"] != "pendiente":
            await interaction.response.send_message("Esta cotización ya no está pendiente 🤔", ephemeral=True)
            return

        coberturas = coberturas_del_mensaje(await texto_de_la_cotizacion(interaction.message))
        await interaction.response.send_modal(ModalAcepto(self.cotizacion_id, coberturas))


class BotonNoAvanzo(discord.ui.DynamicItem[discord.ui.Button], template=r"cotiz:noavanzo:(?P<id>\d+)"):
    def __init__(self, cotizacion_id: int):
        super().__init__(discord.ui.Button(
            label="No avanzó", emoji="❌", style=discord.ButtonStyle.secondary, custom_id=f"cotiz:noavanzo:{cotizacion_id}",
        ))
        self.cotizacion_id = cotizacion_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction):
        cotizacion = db.obtener_cotizacion(self.cotizacion_id)
        # Por si dos tocaron a la vez o el botón quedó viejo
        if cotizacion is None or cotizacion["estado"] != "pendiente":
            await interaction.response.send_message("Esta cotización ya no está pendiente 🤔", ephemeral=True)
            return

        quien = interaction.user.display_name
        db.cambiar_estado(self.cotizacion_id, "no_avanzo", quien)
        await interaction.response.edit_message(
            content=cambiar_linea_estado(interaction.message.content, f"{LINEA_ESTADO['no_avanzo']} · lo marcó {quien}"),
            view=botones_no_avanzo(self.cotizacion_id),
        )


class BotonReabrir(discord.ui.DynamicItem[discord.ui.Button], template=r"cotiz:reabrir:(?P<id>\d+)"):
    def __init__(self, cotizacion_id: int):
        super().__init__(discord.ui.Button(
            label="Volver a pendiente", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id=f"cotiz:reabrir:{cotizacion_id}",
        ))
        self.cotizacion_id = cotizacion_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction):
        cotizacion = db.obtener_cotizacion(self.cotizacion_id)
        if cotizacion is None or cotizacion["estado"] != "no_avanzo":
            await interaction.response.send_message("Esta cotización ya cambió de estado 🤔", ephemeral=True)
            return

        db.cambiar_estado(self.cotizacion_id, "pendiente", interaction.user.display_name)
        await interaction.response.edit_message(
            content=cambiar_linea_estado(interaction.message.content, LINEA_ESTADO["pendiente"]),
            view=botones_pendiente(self.cotizacion_id),
        )


class Cotizaciones(commands.Cog):
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not es_canal_de_cotizaciones(message.channel):
            return

        pdfs = [a for a in message.attachments if a.filename.lower().endswith(".pdf")]

        for adjunto in pdfs:
            async with message.channel.typing():
                try:
                    pdf_bytes = await adjunto.read()
                    texto = await armar_mensaje(pdf_bytes)
                except Exception as e:
                    print(f"Error procesando {adjunto.filename}: {e}")
                    await message.reply(f"Uy, no pude leer **{adjunto.filename}** 😕 Probá subirlo de nuevo en un ratito.")
                    continue

            if not texto:
                await message.reply(f"Leí **{adjunto.filename}** pero no me salió armar el mensaje 😕 Probá de nuevo.")
                continue

            # Guardamos el mensaje del PDF para poder releerlo cuando el cliente acepte
            cotizacion_id = db.nueva_cotizacion(message.channel.id, message.id, adjunto.filename)
            vista = botones_pendiente(cotizacion_id)

            # Lo mandamos en un bloque de código así al copiarlo no se pierden los asteriscos de WhatsApp.
            # Dejamos margen bajo los 2000 de Discord porque después la línea de estado se alarga
            if len(texto) <= 1700:
                respuesta = await message.reply(
                    f"{LINEA_ESTADO['pendiente']}\n📲 Mensaje listo para WhatsApp:\n```\n{texto}\n```", view=vista,
                )
            else:
                archivo = discord.File(io.BytesIO(texto.encode("utf-8")), filename="mensaje_whatsapp.txt")
                respuesta = await message.reply(
                    f"{LINEA_ESTADO['pendiente']}\n📲 El mensaje quedó largo, te lo paso en un archivo:", file=archivo, view=vista,
                )

            db.guardar_mensaje_cobi(cotizacion_id, respuesta.id)


async def setup(bot: commands.Bot):
    bot.add_dynamic_items(BotonAcepto, BotonNoAvanzo, BotonReabrir)
    await bot.add_cog(Cotizaciones())
