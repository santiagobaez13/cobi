import io
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

import config

CARPETA_PLANTILLAS = Path(__file__).parent.parent / "plantillas"
EXTENSIONES_IMAGEN = (".png", ".jpg", ".jpeg", ".webp")


def plantillas_disponibles() -> dict[str, Path]:
    # Cada .md de plantillas/ es una plantilla: "requisitos_alta_auto.md" se muestra como "Requisitos alta auto"
    return {
        archivo.stem.replace("_", " ").capitalize(): archivo
        for archivo in sorted(CARPETA_PLANTILLAS.glob("*.md"))
    }


def leer_plantilla(nombre_archivo: str, compania: str | None = None) -> str | None:
    # Para el panel del alta: busca "fotos_auto.md" y compañía; si no existe devuelve None
    archivo = CARPETA_PLANTILLAS / f"{nombre_archivo}.md"
    if not archivo.is_file():
        return None
    texto = archivo.read_text(encoding="utf-8").strip()
    return texto.replace("{compania}", compania) if compania else texto


def imagen_plantilla(nombre_archivo: str) -> discord.File | None:
    # Si al lado del .md hay una imagen con el mismo nombre ("requisitos_alta_auto.jpeg"), va adjunta
    for extension in EXTENSIONES_IMAGEN:
        imagen = CARPETA_PLANTILLAS / f"{nombre_archivo}{extension}"
        if imagen.is_file():
            return discord.File(imagen)
    return None


class Plantillas(commands.Cog):
    @app_commands.command(name="plantilla", description="Te paso un mensaje predeterminado listo para WhatsApp")
    @app_commands.describe(cual="Qué plantilla querés", compania="La compañía, para las plantillas que la nombran")
    @app_commands.choices(compania=[app_commands.Choice(name=c, value=c) for c in config.COMPANIAS])
    async def plantilla(self, interaction: discord.Interaction, cual: str, compania: str | None = None):
        # Buscamos por nombre y no armamos la ruta con lo que escribieron, así nadie lee archivos que no son plantillas
        archivo = plantillas_disponibles().get(cual)
        if archivo is None:
            await interaction.response.send_message(f"No tengo ninguna plantilla que se llame **{cual}** 🤔", ephemeral=True)
            return

        texto = archivo.read_text(encoding="utf-8").strip()

        if "{compania}" in texto:
            if compania is None:
                await interaction.response.send_message("Esta plantilla nombra a la compañía, elegila en la opción **compania** 😉", ephemeral=True)
                return
            texto = texto.replace("{compania}", compania)

        # Igual que en cotizaciones: en bloque de código para que no se rompa el formato al copiar
        adjuntos = [imagen] if (imagen := imagen_plantilla(archivo.stem)) else []
        if len(texto) <= 1900:
            await interaction.response.send_message(f"📋 **{cual}**\n```\n{texto}\n```", files=adjuntos)
        else:
            adjuntos.append(discord.File(io.BytesIO(texto.encode("utf-8")), filename=f"{archivo.stem}.txt"))
            await interaction.response.send_message(f"📋 **{cual}** (quedó larga, va en archivo):", files=adjuntos)

    @plantilla.autocomplete("cual")
    async def autocompletar_cual(self, interaction: discord.Interaction, actual: str):
        return [
            app_commands.Choice(name=nombre, value=nombre)
            for nombre in plantillas_disponibles()
            if actual.lower() in nombre.lower()
        ][:25]


async def setup(bot: commands.Bot):
    await bot.add_cog(Plantillas())
