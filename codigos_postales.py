import csv
import re
from functools import cache
from pathlib import Path

from util import normalizar

# La lista sale de los datos del Correo Argentino (armada por github.com/XtnPaez/cod_pos_AR, 2026).
# Son los CP de 4 números que piden las webs de las compañías. CABA no está por barrio: ahí el CP va por calle
RUTA = Path(__file__).parent / "datos" / "codigos_postales.csv"
MAX_RESULTADOS = 6

# "cobi cp quilmes oeste", "cobi código postal de lomas de zamora", "cobi codigo banfield"
PREGUNTA = re.compile(r"\b(cp|c p|codigo postal|codigos postales|cod postal|codigo|postal)\b")
# Palabras que sobran alrededor del lugar ("cobi, ¿cuál es el código postal de Banfield?")
RELLENO = {
    "cobi", "che", "cp", "c", "p", "codigo", "codigos", "postal", "postales", "cod", "de", "del", "cual", "es", "el", "la",
    "los", "las", "pasame", "pasa", "dame", "decime", "que", "tiene", "me", "por", "favor", "sabes", "en", "busca",
    "buscame", "y", "un", "a", "hola", "gracias",
}
MINUSCULAS = {"de", "del", "la", "las", "los", "y", "el"}


@cache
def _filas() -> list[dict]:
    # Se lee una sola vez y queda en memoria (son ~24.000 localidades, es poco)
    with open(RUTA, encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    for fila in filas:
        fila["_localidad"] = normalizar(fila["localidad"])
        fila["_partido"] = normalizar(fila["partido"])
    return filas


def es_pregunta(texto: str) -> bool:
    return PREGUNTA.search(normalizar(texto)) is not None


def lugar_buscado(texto: str) -> str:
    # Saca "cobi", "código postal", "de"... de las puntas y deja el lugar: "lomas de zamora", "quilmes oeste"
    palabras = re.findall(r"[a-z0-9]+", normalizar(re.sub(r"<@[!&]?\d+>", " ", texto)))
    palabras = [p for p in palabras if p not in ("cobi", "che", "cp", "codigo", "postal", "cod")]
    while palabras and palabras[0] in RELLENO:
        palabras.pop(0)
    while palabras and palabras[-1] in RELLENO:
        palabras.pop()
    return " ".join(palabras)


def lindo(nombre: str) -> str:
    # "LOMAS DE ZAMORA" → "Lomas de Zamora"
    palabras = nombre.lower().split()
    return " ".join(p if i and p in MINUSCULAS else p.capitalize() for i, p in enumerate(palabras))


def buscar(lugar: str) -> list[dict]:
    filas = _filas()
    if lugar.isdigit():
        encontradas = [f for f in filas if f["cp"] == lugar]  # al revés: "cobi cp 1832" → qué localidades son
    else:
        # La que se llama así, las del partido que se llama así ("almirante brown") y las que lo contienen
        # ("quilmes" → Quilmes Oeste, "san martin" → General San Martín)
        palabra = re.compile(rf"\b{re.escape(lugar)}\b")
        encontradas = [f for f in filas if f["_localidad"] == lugar or f["_partido"] == lugar or palabra.search(f["_localidad"])]
    # Primero el conurbano (CP 1xxx de Buenos Aires, donde laburamos casi siempre), después el resto de la provincia
    # y al final las otras provincias. Dentro de eso, primero la que se llama exacto así
    return sorted(encontradas, key=lambda f: (
        f["provincia"] != "Buenos Aires",
        not f["cp"].startswith("1"),
        f["_localidad"] != lugar,
        f["_localidad"],
    ))


def responder(texto: str) -> str:
    lugar = lugar_buscado(texto)
    if not lugar:
        return "¿De qué localidad? Por ejemplo: *cobi cp Banfield*"
    if lugar in ("capital", "capital federal", "caba", "ciudad autonoma de buenos aires"):
        return "En CABA el código postal va por calle (del 1000 al 1499), no lo tengo por barrio 😕"

    encontradas = buscar(lugar)
    if not encontradas:
        return f"No encontré **{lugar}** 🤔 Fijate cómo se escribe, o si es un barrio de CABA (esos no los tengo)."

    renglones = [
        f"📮 **{lindo(f['localidad'])}** ({lindo(f['partido'])}, {f['provincia']}): **{f['cp']}**"
        for f in encontradas[:MAX_RESULTADOS]
    ]
    if len(encontradas) > MAX_RESULTADOS:
        renglones.append(f"…y {len(encontradas) - MAX_RESULTADOS} más. Si no está, decime el nombre más completo.")
    if all(f["provincia"] != "Buenos Aires" for f in encontradas):
        renglones.append("(Si es un barrio de CABA, esos no los tengo: ahí el CP va por calle)")
    return "\n".join(renglones)
