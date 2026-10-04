import re
import unicodedata

import discord


def normalizar(texto: str) -> str:
    # Sin tildes, sin emojis y en minúscula, para comparar nombres sin volverse loco
    sin_tildes = unicodedata.normalize("NFD", texto).encode("ascii", "ignore").decode()
    return " ".join(sin_tildes.lower().split())


def mismo_nombre(nombre_canal: str, buscado: str) -> bool:
    # "pendientes-emision⌛" o "⌛ pendientes-emision" cuentan como "pendientes-emision": los emojis y tildes no importan
    return normalizar(nombre_canal or "").strip(" -_") == normalizar(buscado).strip(" -_")


def slug(texto: str) -> str:
    # "Accidentes Personales" → "accidentes_personales", para armar nombres de archivo
    return re.sub(r"[^a-z0-9]+", "_", normalizar(texto)).strip("_")


def clave_compania(nombre: str | None) -> str:
    # "Nación Seguros" → "nacion", "ATM Seguros" → "atm". Así no importa cómo la escriba la IA
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
