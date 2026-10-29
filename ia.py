import base64
import io
import json
import re
from pathlib import Path

import anthropic
from PIL import Image

import config
import db
from util import ahora

CARPETA_PROMPTS = Path(__file__).parent / "prompts"

# El SDK ya reintenta solo si Claude está saturado o se corta la conexión (429, 5xx), no hace falta hacerlo a mano
_cliente = anthropic.AsyncAnthropic(
    api_key=config.ANTHROPIC_API_KEY,
    timeout=config.ESPERA_MAXIMA_IA,
    max_retries=config.REINTENTOS_IA,
)

# Claude lee estos formatos de imagen; cualquier otro (HEIC, por ejemplo) lo pasamos a JPEG antes
FORMATOS_IMAGEN = {"image/jpeg", "image/png", "image/gif", "image/webp"}
# Claude no acepta imágenes de más de 5 MB. Las fotos de celular pesadas las achicamos (igual Claude las achica por su cuenta)
MAX_BYTES_IMAGEN = 4_500_000
LADO_MAXIMO = 2500


def leer_prompt(nombre: str, **datos: str) -> str:
    # Lo leemos cada vez, así si tocás el .txt no hace falta reiniciar a Cobi.
    # Lo que va entre llaves ({empresa}, {cobertura}...) se reemplaza por su valor
    texto = (CARPETA_PROMPTS / f"{nombre}.txt").read_text(encoding="utf-8")
    for clave, valor in {"empresa": config.EMPRESA, **datos}.items():
        texto = texto.replace("{" + clave + "}", valor)
    return texto


def _base64(datos: bytes) -> str:
    return base64.standard_b64encode(datos).decode("utf-8")


def pdf(datos: bytes) -> dict:
    return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": _base64(datos)}}


def tipo_real(datos: bytes) -> str | None:
    # Discord a veces dice "webp" y el archivo es un png (pasa con fotos que vienen de WhatsApp), y Claude lo rechaza.
    # Así que miramos los primeros bytes del archivo, que no mienten
    if datos.startswith(b"\x89PNG"):
        return "image/png"
    if datos.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if datos.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return "image/webp"
    return None


def imagen(datos: bytes, tipo: str) -> dict:
    # tipo es el mime que dice Discord ("image/jpeg"...), pero manda el que sale del archivo
    tipo = tipo_real(datos) or tipo
    if tipo not in FORMATOS_IMAGEN or len(datos) > MAX_BYTES_IMAGEN:
        datos, tipo = _achicar(datos)
    return {"type": "image", "source": {"type": "base64", "media_type": tipo, "data": _base64(datos)}}


def _achicar(datos: bytes) -> tuple[bytes, str]:
    # La pasamos a JPEG y, si es enorme, la achicamos sin perder lo que se lee
    foto = Image.open(io.BytesIO(datos))
    foto.thumbnail((LADO_MAXIMO, LADO_MAXIMO))
    salida = io.BytesIO()
    foto.convert("RGB").save(salida, format="JPEG", quality=90)
    return salida.getvalue(), "image/jpeg"


def _texto_de_json(texto: str):
    # Pedimos solo JSON, pero por las dudas sacamos el ```json ... ``` si viene envuelto
    texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto.strip())
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        # Si le agregó algo antes o después, nos quedamos con lo que va de la primera llave a la última
        inicio = min((i for i in (texto.find("{"), texto.find("[")) if i != -1), default=-1)
        fin = max(texto.rfind("}"), texto.rfind("]"))
        if inicio == -1 or fin <= inicio:
            raise
        return json.loads(texto[inicio:fin + 1])


async def generar_json(contenido: list, tarea: str = "otra"):
    return _texto_de_json(await generar([*contenido, "Respondé únicamente con el JSON, sin texto antes ni después."], tarea=tarea))


def anotar_consumo(tarea: str, uso):
    # Cuánto costó este pedido, para "cobi consumo". Si algo falla al anotar, no frenamos a Cobi por eso
    try:
        precios = config.PRECIOS_IA.get(config.MODELO_IA, {})
        leida, escrita = uso.cache_read_input_tokens or 0, uso.cache_creation_input_tokens or 0
        grande = uso.input_tokens + leida + escrita > 100_000
        entrada = precios.get("entrada_grande" if grande else "entrada", 0)
        salida = precios.get("salida_grande" if grande else "salida", 0)
        costo = (uso.input_tokens * entrada + leida * entrada * 0.1 + escrita * entrada * 1.25 + uso.output_tokens * salida) / 1_000_000
        db.anotar_consumo(ahora().strftime("%Y-%m-%d %H:%M:%S"), tarea, config.MODELO_IA, uso.input_tokens, uso.output_tokens, leida, escrita, costo)
    except Exception as e:
        print(f"No pude anotar el consumo de la IA: {e}")


async def generar(contenido: list, sistema: str | None = None, tarea: str = "otra") -> str:
    # contenido: PDFs e imágenes primero, después el texto del prompt (los str se pasan solos a bloque de texto).
    # sistema: las instrucciones fijas (quién es Cobi, la biblioteca). Van en caché, así si preguntan seguido sale más barato
    bloques = [{"type": "text", "text": c} if isinstance(c, str) else c for c in contenido]
    extras = {"system": [{"type": "text", "text": sistema, "cache_control": {"type": "ephemeral"}}]} if sistema else {}
    respuesta = await _cliente.messages.create(
        model=config.MODELO_IA,
        max_tokens=16000,
        output_config={"effort": config.ESFUERZO_IA},
        messages=[{"role": "user", "content": bloques}],
        **extras,
    )
    anotar_consumo(tarea, respuesta.usage)
    if respuesta.stop_reason == "refusal":
        raise RuntimeError("Claude no quiso responder ese pedido")
    # La respuesta puede traer bloques de "pensamiento" antes del texto: juntamos solo el texto
    return "".join(b.text for b in respuesta.content if b.type == "text").strip()
