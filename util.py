import re
import unicodedata
from datetime import datetime, timedelta, timezone

import discord

# Argentina es UTC-3 todo el año (no hay horario de verano). Los servidores suelen estar en UTC,
# así que siempre pedimos la hora de acá, esté donde esté Cobi
HORA_ARGENTINA = timezone(timedelta(hours=-3))


def ahora() -> datetime:
    # La hora argentina, sin zona pegada (así se compara con las fechas que ya están guardadas en la base)
    return datetime.now(HORA_ARGENTINA).replace(tzinfo=None)


def normalizar(texto: str) -> str:
    # Sin tildes, sin emojis y en minúscula, para comparar nombres sin volverse loco
    sin_tildes = unicodedata.normalize("NFD", texto).encode("ascii", "ignore").decode()
    return " ".join(sin_tildes.lower().split())


def telefono_limpio(texto: str) -> str:
    # Solo números, como lo piden las webs de las compañías: "+54 9 11 3664-0286" o "011 3664-0286" → "1136640286"
    digitos = re.sub(r"\D", "", texto or "")
    if digitos.startswith("549") and len(digitos) == 13:
        digitos = digitos[3:]
    elif digitos.startswith("54") and len(digitos) == 12:
        digitos = digitos[2:]
    if digitos.startswith("0"):
        digitos = digitos[1:]
    return digitos


# Los datos que se copian y pegan en la web de la compañía van solo con números: sin puntos, espacios ni guiones
SOLO_NUMEROS = {"dni", "cuil"}


def para_la_web(clave: str, valor):
    if not isinstance(valor, str) or not valor:
        return valor
    if clave in SOLO_NUMEROS:
        return re.sub(r"\D", "", valor) or valor
    if clave == "telefono":
        return telefono_limpio(valor) or valor
    return valor


def mismo_nombre(nombre_canal: str, buscado: str) -> bool:
    # "pendientes-emision⌛" o "⌛ pendientes-emision" cuentan como "pendientes-emision": los emojis y tildes no importan
    return normalizar(nombre_canal or "").strip(" -_") == normalizar(buscado).strip(" -_")


def slug(texto: str) -> str:
    # Pasa "Accidentes Personales" a "accidentes_personales", para armar nombres de archivo
    return re.sub(r"[^a-z0-9]+", "_", normalizar(texto)).strip("_")


def clave_compania(nombre: str | None) -> str:
    # De "Nación Seguros" queda "nacion" y de "ATM Seguros" queda "atm". Así no importa cómo la escriba la IA
    return slug(nombre or "").split("_")[0]


def buscar_etiqueta(foro: discord.ForumChannel, nombre: str) -> discord.ForumTag | None:
    # Comparamos "por adentro" así "Accidentes Personales" encuentra la etiqueta "👷 Accidentes Personal"
    buscado = normalizar(nombre)
    if not buscado:
        return None
    for etiqueta in foro.available_tags:
        actual = normalizar(etiqueta.name)
        if actual and (buscado in actual or actual in buscado):
            return etiqueta
    return None
