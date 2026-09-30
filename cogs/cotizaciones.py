import io

import discord
from discord.ext import commands

import config
import db
import ia

# La primera línea del mensaje de Cobi muestra en qué quedó la cotización
LINEA_ESTADO = {
    "pendiente": "⏳ **Esperando respuesta del cliente**",
    "no_avanzo": "❌ **No avanzó**",
}


def es_canal_de_cotizaciones(canal) -> bool:
    # Si es un hilo nos fijamos en el canal de arriba, así también anda en hilos de #bot-pruebas
    if isinstance(canal, discord.Thread) and canal.parent:
        return canal.parent.name in config.CANALES_PDF
    return getattr(canal, "name", "") in config.CANALES_PDF


async def armar_mensaje(pdf_bytes: bytes) -> str:
    texto = await ia.generar([ia.pdf(pdf_bytes), ia.leer_prompt("cotizacion")])
    return texto.replace("```", "")


def cambiar_linea_estado(contenido: str, nueva_linea: str) -> str:
    _, _, resto = contenido.partition("\n")
    return f"{nueva_linea}\n{resto}"


def botones_pendiente(cotizacion_id: int) -> discord.ui.View:
    vista = discord.ui.View(timeout=None)
    vista.add_item(BotonAcepto(cotizacion_id))
    vista.add_item(BotonNoAvanzo(cotizacion_id))
    return vista


def botones_no_avanzo(cotizacion_id: int) -> discord.ui.View:
    vista = discord.ui.View(timeout=None)
    vista.add_item(BotonReabrir(cotizacion_id))
    return vista


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
        # Esto es el paso 2: elegir cobertura, pedir el nombre y armar el hilo en altas
        await interaction.response.send_message("Esto lo estamos armando, llega en el próximo paso 🚧", ephemeral=True)


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
            # Dejamos margen bajo los 2000 de Discord porque después le cambiamos la línea de estado
            if len(texto) <= 1800:
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
