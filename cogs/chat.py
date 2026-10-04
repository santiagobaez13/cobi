import re
from datetime import datetime

import discord
from discord.ext import commands

import config
import db
from util import mismo_nombre, normalizar

FORMATO_FECHA = "%Y-%m-%d %H:%M:%S"

# Palabras de la charla que no son datos ("che cobi, auto patente 329jsp, ¿qué alta fue?")
PALABRAS_DE_RELLENO = {
    "che", "cobi", "hola", "auto", "moto", "patente", "dominio", "que", "alta", "altas", "fue", "del", "las", "los",
    "una", "uno", "cual", "quien", "dni", "numero", "nro", "solicitud", "buscame", "busca", "buscar", "pasame",
    "link", "hilo", "cliente", "esta", "donde", "por", "favor", "gracias", "con", "para", "tenes", "telefono",
    "cuil", "cbu", "mail", "poliza", "anda", "estado", "como", "quedo", "era", "seguro", "vehiculo",
}


def fragmentos(texto: str) -> list[str]:
    # Las palabras sueltas y también pegadas de a 2 y 3, así "AB 123 CD" se encuentra como "ab123cd"
    palabras = re.findall(r"[a-z0-9]+", normalizar(texto))
    juntas = ["".join(palabras[i:i + n]) for n in (2, 3) for i in range(len(palabras) - n + 1)]
    return palabras + juntas


def puntaje(alta, buscados: list[str]) -> int:
    datos = db.datos_alta(alta)
    patente = re.sub(r"[^a-z0-9]", "", normalizar(datos.get("patente", "")))
    numeros = [re.sub(r"\D", "", str(v or "")) for v in (datos.get("dni"), datos.get("cuil"), datos.get("telefono"), datos.get("cbu"), alta["n_solicitud"])]
    nombres = set(re.findall(r"[a-z]+", normalizar(" ".join(filter(None, [alta["cliente"], datos.get("apellido"), datos.get("nombre")])))))

    total = 0
    for b in buscados:
        if patente and b == patente:
            total += 10  # la patente es lo más seguro
        elif b.isdigit() and any(b == n or (len(b) >= 5 and b in n) for n in numeros if n):
            total += 5   # DNI, CUIL, teléfono, CBU o N° de solicitud (exacto, o un pedazo de 5 números o más)
        elif b.isalpha() and len(b) >= 3 and b not in PALABRAS_DE_RELLENO and b in nombres:
            total += 3   # apellido o nombre
    return total


def describir(alta) -> str:
    datos = db.datos_alta(alta)
    patente = f" · {datos['patente']}" if datos.get("patente") else ""
    if alta["estado"] == "emitida":
        emitida = datetime.strptime(alta["emitida_en"], FORMATO_FECHA).strftime("%d/%m/%Y") if alta["emitida_en"] else "?"
        estado = f"✅ Emitida el {emitida}"
    elif alta["estado"] == "pendiente_emision":
        dias = (datetime.now() - datetime.strptime(alta["pendiente_desde"], FORMATO_FECHA)).days
        estado = f"⏳ Pendiente de emisión hace {dias} día{'s' if dias != 1 else ''} (está en #{config.CANAL_PENDIENTES})"
    else:
        estado = "📋 En curso"
    return (
        f"**{alta['cliente']}** · {alta['compania'] or '?'} · {alta['vehiculo'] or '?'} {alta['anio'] or ''}{patente}\n"
        f"{estado} · N° de solicitud {alta['n_solicitud'] or '—'} 👉 <#{alta['hilo_id']}>"
    )


class Chat(commands.Cog):
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not mismo_nombre(getattr(message.channel, "name", ""), config.CANAL_CHAT):
            return

        buscados = fragmentos(message.content)
        resultados = sorted(((puntaje(a, buscados), a) for a in db.todas_las_altas()), key=lambda r: r[0], reverse=True)
        mejor = resultados[0][0] if resultados else 0
        if mejor == 0:
            await message.reply(
                "No encontré ningún alta con eso 🤔 Probá con la **patente**, el **DNI**, el **N° de solicitud**, "
                "el **teléfono** o el **apellido** del cliente."
            )
            return

        # Mostramos las que mejor coinciden (si hay empate, todas, hasta 5)
        encontradas = [a for p, a in resultados if p == mejor][:5]
        encabezado = "Es esta 👇" if len(encontradas) == 1 else f"Encontré {len(encontradas)} que coinciden 👇"
        await message.reply(encabezado + "\n\n" + "\n\n".join(describir(a) for a in encontradas))


async def setup(bot: commands.Bot):
    await bot.add_cog(Chat())
