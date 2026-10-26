import discord
from discord import app_commands
from discord.ext import commands

import config
import db
from cogs.altas import buscar_hilo, canal_por_nombre
from util import ahora

FORMATO_FECHA = "%Y-%m-%d %H:%M:%S"
ESTADOS = {"pendiente_emision": "⏳ pendiente", "emitida": "✅ emitida"}


def resumen(alta) -> str:
    return f"{alta['cliente']} · {alta['vehiculo'] or '?'} {alta['anio'] or ''}".strip()


async def borrar_todo(bot: commands.Bot, alta_id: int):
    # Borra el alta de todos lados: el hilo, la tarjeta de pendientes, el mensaje de Cobi en #cotizar y la base.
    # Lo de Discord primero (si algo ya no estaba, seguimos igual), y la base al final
    alta = db.obtener_alta(alta_id)
    if alta is None:
        return
    cotizacion = db.obtener_cotizacion(alta["cotizacion_id"])

    mensajes = []
    if alta["tarjeta_pendiente_id"] and (canal := canal_por_nombre(bot, config.CANAL_PENDIENTES)):
        mensajes.append(canal.get_partial_message(alta["tarjeta_pendiente_id"]))
    if cotizacion and cotizacion["mensaje_cobi_id"] and (canal := bot.get_channel(cotizacion["canal_id"])):
        mensajes.append(canal.get_partial_message(cotizacion["mensaje_cobi_id"]))
    for mensaje in mensajes:
        try:
            await mensaje.delete()
        except discord.HTTPException:
            pass

    if alta["hilo_id"] and (hilo := await buscar_hilo(bot, alta["hilo_id"])):
        try:
            await hilo.delete()
        except discord.HTTPException as e:
            print(f"No pude borrar el hilo del alta {alta_id}: {e}")

    db.borrar_alta(alta_id, ahora().strftime(FORMATO_FECHA))


class Confirmar(discord.ui.View):
    def __init__(self, ids: list[int]):
        super().__init__(timeout=300)
        self.ids = ids

    @discord.ui.button(label="Sí, borrar", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def si(self, interaction: discord.Interaction, boton: discord.ui.Button):
        await interaction.response.edit_message(content="Borrando… ⏳", view=None)
        for alta_id in self.ids:
            await borrar_todo(interaction.client, alta_id)
        await interaction.edit_original_response(
            content=f"✅ Listo, borré {len(self.ids)} alta{'s' if len(self.ids) != 1 else ''}. "
                    "Si me equivoqué, quedó una copia en la base: avisale a Santi."
        )

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, boton: discord.ui.Button):
        await interaction.response.edit_message(content="Cancelado, no borré nada 👍", view=None)


class MenuAltas(discord.ui.Select):
    def __init__(self, altas: list):
        opciones = [
            discord.SelectOption(
                label=resumen(a)[:100],
                description=f"#{a['id']} · {a['compania'] or '?'} · {ESTADOS.get(a['estado'], '📋 en curso')} · {a['creada'][:10]}"[:100],
                value=str(a["id"]),
            )
            for a in altas
        ]
        super().__init__(placeholder="Elegí las altas que querés borrar", min_values=1, max_values=len(opciones), options=opciones)

    async def callback(self, interaction: discord.Interaction):
        ids = [int(v) for v in self.values]
        nombres = "\n".join(f"• {resumen(a)}" for i in ids if (a := db.obtener_alta(i)))
        await interaction.response.edit_message(
            content=f"🗑️ Vas a borrar **{len(ids)}**:\n{nombres}\n\n"
                    "Se borra el hilo, la tarjeta de pendientes, el mensaje de Cobi en #cotizar y los datos. ¿Seguro?",
            view=Confirmar(ids),
        )


class Limpieza(commands.Cog):
    @app_commands.command(name="limpiar", description="Borrar altas de prueba (el hilo, la tarjeta y los datos)")
    async def limpiar(self, interaction: discord.Interaction):
        if interaction.user.id not in config.EQUIPO:
            await interaction.response.send_message("Esto es solo para el equipo 🔒", ephemeral=True)
            return
        # Un menú de Discord muestra hasta 25 opciones: van las más nuevas
        altas = db.todas_las_altas()[:25]
        if not altas:
            await interaction.response.send_message("No hay altas para borrar 👍", ephemeral=True)
            return
        vista = discord.ui.View(timeout=300)
        vista.add_item(MenuAltas(altas))
        await interaction.response.send_message("Elegí las que querés borrar (podés marcar varias) 👇", view=vista, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Limpieza())
