import discord
from discord import app_commands
from discord.ext import commands

import config


class General(commands.Cog):
    @app_commands.command(name="hola", description="Saludá a Cobi")
    async def hola(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"¡Hola! Soy Cobi 🛡️, listo para ayudar en {config.EMPRESA}.")


async def setup(bot: commands.Bot):
    await bot.add_cog(General())
