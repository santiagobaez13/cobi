from datetime import datetime

import discord
from discord.ext import commands

import config
import db
import hoja
from util import buscar_etiqueta, mismo_nombre

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
        f"📂 La historia del alta 👉 <#{alta['hilo_id']}>"
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

    async def on_submit(self, interaction: discord.Interaction):
        db.actualizar_alta(self.alta_id, n_poliza=self.n_poliza.value.strip() or None, vigencia=self.vigencia.value.strip() or None)
        await interaction.response.edit_message(content=texto_ficha(db.obtener_alta(self.alta_id)), view=vista_ficha(self.alta_id))


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


async def setup(bot: commands.Bot):
    bot.add_dynamic_items(BotonPoliza)
