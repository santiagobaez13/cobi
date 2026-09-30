import io

import discord
from discord.ext import commands

import config
import ia


def es_canal_de_cotizaciones(canal) -> bool:
    # En un foro cada publicación es un hilo, así que nos fijamos en el nombre del foro de arriba
    if isinstance(canal, discord.Thread) and canal.parent:
        return canal.parent.name in config.CANALES_PDF
    return getattr(canal, "name", "") in config.CANALES_PDF


async def armar_mensaje(pdf_bytes: bytes) -> str:
    texto = await ia.generar([ia.pdf(pdf_bytes), ia.leer_prompt("cotizacion")])
    return texto.replace("```", "")


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

            # Lo mandamos en un bloque de código así al copiarlo no se pierden los asteriscos de WhatsApp
            if len(texto) <= 1900:
                await message.reply(f"📲 Mensaje listo para WhatsApp:\n```\n{texto}\n```")
            else:
                archivo = discord.File(io.BytesIO(texto.encode("utf-8")), filename="mensaje_whatsapp.txt")
                await message.reply("📲 El mensaje quedó largo, te lo paso en un archivo:", file=archivo)


async def setup(bot: commands.Bot):
    await bot.add_cog(Cotizaciones())
