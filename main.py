import discord
from discord.ext import commands

import config

# Cada cog es un archivo de cogs/ con lo de un área (cotizaciones, altas, etc.)
COGS = ["cogs.general", "cogs.cotizaciones", "cogs.plantillas"]


class Cobi(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)

    async def setup_hook(self):
        for cog in COGS:
            await self.load_extension(cog)
        await self.tree.sync()

    async def on_ready(self):
        print(f"Cobi conectado como {self.user}")


Cobi().run(config.DISCORD_TOKEN)
