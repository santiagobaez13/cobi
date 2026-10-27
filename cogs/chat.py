import random
import re
from datetime import datetime, timedelta

import discord
from discord.ext import commands

import codigos_postales
import config
import db
from cogs import tareas
from util import ahora, mismo_nombre, normalizar

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
        dias = (ahora() - datetime.strptime(alta["pendiente_desde"], FORMATO_FECHA)).days
        estado = f"⏳ Pendiente de emisión hace {dias} día{'s' if dias != 1 else ''} (está en #{config.CANAL_PENDIENTES})"
    else:
        estado = "📋 En curso"
    return (
        f"**{alta['cliente']}** · {alta['compania'] or '?'} · {alta['vehiculo'] or '?'} {alta['anio'] or ''}{patente}\n"
        f"{estado} · N° de solicitud {alta['n_solicitud'] or '—'} 👉 <#{alta['hilo_id']}>"
    )


# Si Cobi te acaba de contestar, por un ratito un "gracias" o un "dale" se entiende que es para él aunque no lo nombres
VENTANA_DE_CHARLA = timedelta(minutes=2)

# Charla de todos los días que Cobi entiende sin IA. Solo si el mensaje es eso y nada más ("gracias cobi!", "buen día")
AGRADECER = r"(muchas )?gracias|grax|genial|joya|barbaro|buenisimo|perfecto|excelente|crack|idolo|capo|sos un groso"
SALUDAR = r"hola|holis|buen dia|buenos dias|buenas|buenas tardes|buenas noches|que tal|como andas|como va|todo bien"
DESPEDIR = r"chau|chao|nos vemos|hasta manana|hasta luego|me voy|adios"
ASENTIR = r"ok|oka|okey|dale|listo|de una|ya esta|entendido|bien|ahi va|buenisimo"


def charla(texto: str) -> str | None:
    # Devuelve qué tipo de charla es, o None si el mensaje trae algo más (una patente, una tarea...)
    resto = re.sub(r"[^a-z0-9 ]", " ", normalizar(re.sub(r"<@[!&]?\d+>", " ", texto)))
    resto = " ".join(p for p in resto.split() if p not in ("cobi", "che", "y", "vos", "a", "jaja", "jajaja"))
    if not resto:
        return None
    for tipo, frases in (("agradecer", AGRADECER), ("saludar", SALUDAR), ("despedir", DESPEDIR), ("asentir", ASENTIR)):
        if re.fullmatch(rf"(?:(?:{frases})\s*)+", resto):
            return tipo
    return None


async def responder_charla(message: discord.Message, tipo: str):
    if tipo == "asentir":
        await message.add_reaction("👍")  # para un "dale" alcanza con un pulgar, sin sumar mensajes
        return
    if tipo == "agradecer":
        texto = random.choice(["De nada 🙌", "¡Para eso estoy! 💪", "Cuando quieras 😉"])
    elif tipo == "despedir":
        texto = random.choice(["¡Chau! 👋", "¡Nos vemos! 👋", "¡Hasta mañana! 👋"])
    else:
        hora = ahora().hour
        saludo = "¡Buen día!" if 5 <= hora < 13 else "¡Buenas tardes!" if hora < 20 else "¡Buenas noches!"
        texto = f"{saludo} ¿En qué te doy una mano?"
    await message.reply(texto, mention_author=False)


def le_hablan_a_cobi(bot: commands.Bot, message: discord.Message) -> bool:
    # En el general charla todo el equipo: Cobi contesta solo si lo nombran, lo arroban o le responden un mensaje
    return bot.user in message.mentions or re.search(r"\bcobi\b", normalizar(message.content)) is not None


class Chat(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # (canal, persona) → cuándo le contestó Cobi por última vez
        self.ultima_respuesta: dict[tuple[int, int], datetime] = {}

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        clave = (message.channel.id, message.author.id)
        if message.guild is None:
            # Por privado todo es para Cobi, pero solo le contesta a los del equipo (hay datos de clientes)
            if message.author.id not in config.EQUIPO:
                return
        elif not mismo_nombre(getattr(message.channel, "name", ""), config.CANAL_CHAT):
            return
        elif not le_hablan_a_cobi(self.bot, message):
            # No lo nombraron, pero si recién le contestó y es un "gracias" o un "dale", es para él
            reciente = ahora() - self.ultima_respuesta.get(clave, datetime.min) < VENTANA_DE_CHARLA
            if reciente and (tipo := charla(message.content)):
                await responder_charla(message, tipo)
            return

        self.ultima_respuesta[clave] = ahora()
        if tipo := charla(message.content):
            await responder_charla(message, tipo)
            return

        if tareas.es_anotar(message.content):
            await tareas.anotar(self.bot, message)
            return
        if tareas.es_consulta(message.content):
            await tareas.mostrar(message)
            return
        if codigos_postales.es_pregunta(message.content):
            await message.reply(codigos_postales.responder(message.content), mention_author=False)
            return

        # Sacamos las menciones (<@123...>), si no sus números se confunden con un DNI o un teléfono
        texto = re.sub(r"<@[!&]?\d+>", " ", message.content)
        buscados = fragmentos(texto)
        resultados = sorted(((puntaje(a, buscados), a) for a in db.todas_las_altas()), key=lambda r: r[0], reverse=True)
        mejor = resultados[0][0] if resultados else 0
        if mejor == 0:
            # Acá va a entrar la IA para charlar y responder de seguros, cuando esté paga
            await message.reply(
                "Por ahora sé:\n"
                "🔎 **Buscar altas**: pasame la patente, el DNI, el N° de solicitud, el teléfono o el apellido\n"
                "📋 **Anotar tareas**: *cobi anotá para Eze: liquidarle a Damián*\n"
                "📝 **Decirte qué tenés pendiente**: *cobi, ¿qué tengo?* (o *¿qué tiene Eze?*)\n"
                "📮 **Códigos postales**: *cobi cp Banfield*\n"
                "Las preguntas de seguros todavía no las aprendí 📚"
            )
            return

        # Mostramos las que mejor coinciden (si hay empate, todas, hasta 5)
        encontradas = [a for p, a in resultados if p == mejor][:5]
        encabezado = "Es esta 👇" if len(encontradas) == 1 else f"Encontré {len(encontradas)} que coinciden 👇"
        await message.reply(encabezado + "\n\n" + "\n\n".join(describir(a) for a in encontradas))


async def setup(bot: commands.Bot):
    await bot.add_cog(Chat(bot))
