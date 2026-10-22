import re
from datetime import date, datetime, time, timedelta

import discord
from discord.ext import commands, tasks

import config
import db
from util import HORA_ARGENTINA, ahora, normalizar

FORMATO_FECHA = "%Y-%m-%d %H:%M:%S"
DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
MAX_BOTONES = 20

# "cobi anotá...", "agendale...", "recordale a Eze..." → es una tarea nueva
ANOTAR = re.compile(r"\b(anot\w*|agend\w*|record\w*)\b(\s+(una\s+)?tarea)?[\s:,]*", re.IGNORECASE)
# "cobi, ¿qué tengo?", "¿qué le queda a Eze?", "mis tareas" → quieren ver la lista
CONSULTAR = re.compile(r"\b(que tengo|que me queda|que le queda|que tiene|tareas)\b")
# Para cuándo: "hoy", "para mañana", "el viernes", "para el 25/10"
CUANDO = re.compile(
    r"\b(?:para\s+)?(?:el\s+)?(hoy|ma[ñn]ana|lunes|martes|mi[ée]rcoles|jueves|viernes|s[áa]bado|domingo|\d{1,2}/\d{1,2})\b",
    re.IGNORECASE,
)
# Para quién: "para Eze", "a Ezequiel", "para mí"
PARA_QUIEN = re.compile(r"\b(?:para|a)\s+([^\W\d_]+)\b", re.IGNORECASE)


def nombre(persona_id: int) -> str:
    return config.EQUIPO.get(persona_id, ["alguien"])[0]


def persona_por_nombre(palabra: str) -> int | None:
    buscado = normalizar(palabra)
    for persona_id, apodos in config.EQUIPO.items():
        if buscado in (normalizar(a) for a in apodos):
            return persona_id
    return None


def es_anotar(texto: str) -> bool:
    return ANOTAR.search(texto) is not None


def es_consulta(texto: str) -> bool:
    return CONSULTAR.search(normalizar(texto)) is not None


def leer_cuando(texto: str) -> tuple[date, str]:
    # Devuelve la fecha y el texto sin esa parte. Si no dice nada, es para hoy
    hoy = ahora().date()
    m = CUANDO.search(texto)
    if m is None:
        return hoy, texto
    palabra = normalizar(m.group(1))
    if palabra == "hoy":
        fecha = hoy
    elif palabra == "manana":
        fecha = hoy + timedelta(days=1)
    elif palabra in DIAS:
        # El próximo que viene ("el lunes" un lunes es el de la semana que viene)
        fecha = hoy + timedelta(days=(DIAS.index(palabra) - hoy.weekday()) % 7 or 7)
    else:
        dia, mes = (int(n) for n in palabra.split("/"))
        try:
            fecha = date(hoy.year, mes, dia)
        except ValueError:
            return hoy, texto
        if fecha < hoy - timedelta(days=30):
            fecha = date(hoy.year + 1, mes, dia)  # "el 5/1" dicho en diciembre es del año que viene
    return fecha, texto[:m.start()] + texto[m.end():]


def leer_para_quien(texto: str, autor_id: int) -> tuple[int | None, str]:
    # Devuelve a quién es y el texto sin esa parte (None si no lo dice).
    # "liquidarle a Damián" no se toca porque Damián no es del equipo
    for m in PARA_QUIEN.finditer(texto):
        palabra = normalizar(m.group(1))
        persona_id = autor_id if palabra == "mi" else persona_por_nombre(palabra)
        if persona_id is not None:
            return persona_id, texto[:m.start()] + texto[m.end():]
    return None, texto


def armar_tarea(texto: str, autor_id: int, mencionados: list[int]) -> tuple[str, int, date]:
    # De "che cobi anotá para Eze: liquidarle a Damián para mañana" saca ("Liquidarle a Damián", Eze, mañana)
    texto = re.sub(r"<@[!&]?\d+>", " ", texto)
    texto = re.sub(r"\b(che\s+)?cobi\b[\s,:]*", " ", texto, flags=re.IGNORECASE)
    texto = ANOTAR.sub(" ", texto, count=1)
    fecha, texto = leer_cuando(texto)
    # Si lo dice con palabras ("para Eze", "para mí") manda eso; si no, el @ que hayan puesto; si no, es del que escribe
    para_id, texto = leer_para_quien(texto, autor_id)
    if para_id is None:
        para_id = next((m for m in mencionados if m in config.EQUIPO), autor_id)
    texto = re.sub(r"\s+", " ", texto).strip(" ,.:;-¿?¡!")
    texto = re.sub(r"^que\s+", "", texto, flags=re.IGNORECASE)  # "anotale a Eze que llame a Pérez" → "Llame a Pérez"
    return texto[:1].upper() + texto[1:], para_id, fecha


def a_quien_consultan(texto: str, autor_id: int, mencionados: list[int]) -> int:
    # "¿qué tiene Eze?" → Eze. Si no nombran a nadie del equipo, la lista es del que pregunta
    for m in mencionados:
        if m in config.EQUIPO:
            return m
    for palabra in re.findall(r"[a-z]+", normalizar(texto)):
        if palabra != "cobi" and (persona_id := persona_por_nombre(palabra)) is not None:
            return persona_id
    return autor_id


def cuando_lindo(para_cuando: str) -> str:
    fecha = date.fromisoformat(para_cuando)
    hoy = ahora().date()
    if fecha == hoy:
        return "hoy"
    if fecha < hoy:
        return f"⚠️ atrasada, era para el {fecha:%d/%m}"
    if fecha == hoy + timedelta(days=1):
        return "mañana"
    return f"el {DIAS[fecha.weekday()]} {fecha:%d/%m}"


def texto_lista(persona_id: int, encabezado: str | None = None, hasta: str | None = None) -> tuple[str, list]:
    lista = db.tareas_pendientes(persona_id, hasta)
    encabezado = encabezado or f"📋 **Pendientes de {nombre(persona_id)}**"
    if not lista:
        return f"{encabezado}\nNada pendiente 🎉", []
    renglones = [f"**{i}.** {t['texto']} · {cuando_lindo(t['para_cuando'])}" for i, t in enumerate(lista[:MAX_BOTONES], 1)]
    if len(lista) > MAX_BOTONES:
        renglones.append(f"…y {len(lista) - MAX_BOTONES} más")
    return f"{encabezado}\n" + "\n".join(renglones) + "\n\nTocá el número cuando la termines 👇", lista[:MAX_BOTONES]


def vista_lista(lista: list) -> discord.ui.View:
    vista = discord.ui.View(timeout=None)
    for i, tarea in enumerate(lista, 1):
        vista.add_item(BotonTarea(tarea["id"], f"{i}"))
    return vista


class BotonTarea(discord.ui.DynamicItem[discord.ui.Button], template=r"tarea:hecha:(?P<id>\d+)"):
    def __init__(self, tarea_id: int, etiqueta: str = "Hecha"):
        super().__init__(discord.ui.Button(label=etiqueta, emoji="✅", style=discord.ButtonStyle.success, custom_id=f"tarea:hecha:{tarea_id}"))
        self.tarea_id = tarea_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["id"]), item.label or "Hecha")

    async def callback(self, interaction: discord.Interaction):
        tarea = db.obtener_tarea(self.tarea_id)
        if tarea is None:
            await interaction.response.send_message("No encontré esa tarea 🤔", ephemeral=True)
            return

        recien = db.terminar_tarea(self.tarea_id, ahora().strftime(FORMATO_FECHA))

        # Si el botón estaba en una lista, la rearmamos sin la que se terminó. Si era el aviso de una sola, la tachamos
        if self.item.label == "Hecha":
            await interaction.response.edit_message(content=f"✅ ~~{tarea['texto']}~~ · hecha", view=None)
        else:
            # Si era el aviso de la tarde, seguimos mostrando solo lo de hoy para atrás
            encabezado = interaction.message.content.split("\n")[0]
            hasta = ahora().date().isoformat() if "te quedó pendiente" in encabezado else None
            texto, lista = texto_lista(tarea["para_id"], encabezado=encabezado, hasta=hasta)
            await interaction.response.edit_message(content=texto, view=vista_lista(lista))

        # Si se la había anotado otro, le avisamos que ya está
        if recien and tarea["de_id"] != tarea["para_id"] and interaction.user.id != tarea["de_id"]:
            await mandar_privado(interaction.client, tarea["de_id"], f"✅ **{nombre(interaction.user.id)}** terminó: {tarea['texto']}")


async def mandar_privado(bot: commands.Bot, persona_id: int, texto: str, view: discord.ui.View | None = None) -> bool:
    try:
        usuario = bot.get_user(persona_id) or await bot.fetch_user(persona_id)
        await usuario.send(texto, view=view or discord.utils.MISSING)
        return True
    except discord.HTTPException as e:
        print(f"No le pude mandar un privado a {persona_id}: {e}")
        return False


async def anotar(bot: commands.Bot, message: discord.Message):
    mencionados = [u.id for u in message.mentions if not u.bot]
    texto, para_id, fecha = armar_tarea(message.content, message.author.id, mencionados)
    if not texto:
        await message.reply("¿Qué te anoto? Por ejemplo: *cobi anotá para Eze: liquidarle a Damián*")
        return

    tarea_id = db.nueva_tarea(texto, para_id, message.author.id, fecha.isoformat(), ahora().strftime(FORMATO_FECHA))
    cuando = cuando_lindo(fecha.isoformat())
    para_otro = para_id != message.author.id

    # Si es para otro, le llega por privado con el botón para marcarla
    avisado = True
    if para_otro:
        avisado = await mandar_privado(
            bot, para_id,
            f"📋 **{nombre(message.author.id)}** te anotó una tarea para {cuando}:\n**{texto}**",
            vista_lista_una(tarea_id),
        )

    respuesta = f"📋 Anotado para **{nombre(para_id) if para_otro else 'vos'}**, para {cuando}: {texto}"
    if para_otro and not avisado:
        respuesta += f"\n(No le pude mandar el privado: <@{para_id}>, fijate acá 👆)"
    await message.reply(respuesta, mention_author=False)


def vista_lista_una(tarea_id: int) -> discord.ui.View:
    vista = discord.ui.View(timeout=None)
    vista.add_item(BotonTarea(tarea_id))
    return vista


async def mostrar(message: discord.Message):
    mencionados = [u.id for u in message.mentions if not u.bot]
    persona_id = a_quien_consultan(message.content, message.author.id, mencionados)
    if persona_id not in config.EQUIPO:
        await message.reply("Las tareas son para los del equipo y no te tengo anotado 🤔")
        return
    texto, lista = texto_lista(persona_id)
    await message.reply(texto, view=vista_lista(lista), mention_author=False)


class Tareas(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.avisar_pendientes.start()

    def cog_unload(self):
        self.avisar_pendientes.cancel()

    @tasks.loop(time=time(hour=config.HORA_AVISO_TAREAS, tzinfo=HORA_ARGENTINA))
    async def avisar_pendientes(self):
        # Al cierre del día (de lunes a viernes) cada uno recibe por privado lo que le quedó
        hoy = ahora().date()
        if hoy.weekday() >= 5:
            return
        for persona_id in config.EQUIPO:
            texto, lista = texto_lista(
                persona_id, encabezado=f"📋 **{nombre(persona_id)}, te quedó pendiente:**", hasta=hoy.isoformat()
            )
            if lista:
                await mandar_privado(self.bot, persona_id, texto, vista_lista(lista))

    @avisar_pendientes.before_loop
    async def antes_de_avisar(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    bot.add_dynamic_items(BotonTarea)
    await bot.add_cog(Tareas(bot))
