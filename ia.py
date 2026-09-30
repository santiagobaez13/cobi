import asyncio
import json
from pathlib import Path

from google import genai
from google.genai import types

import config

CARPETA_PROMPTS = Path(__file__).parent / "prompts"

_cliente = genai.Client(api_key=config.GEMINI_API_KEY)
_sin_afc = types.AutomaticFunctionCallingConfig(disable=True)
_config_texto = types.GenerateContentConfig(automatic_function_calling=_sin_afc)
_config_json = types.GenerateContentConfig(automatic_function_calling=_sin_afc, response_mime_type="application/json")


def leer_prompt(nombre: str, **datos: str) -> str:
    # Lo leemos cada vez, así si tocás el .txt no hace falta reiniciar a Cobi.
    # Lo que va entre llaves ({empresa}, {cobertura}...) se reemplaza por su valor
    texto = (CARPETA_PROMPTS / f"{nombre}.txt").read_text(encoding="utf-8")
    for clave, valor in {"empresa": config.EMPRESA, **datos}.items():
        texto = texto.replace("{" + clave + "}", valor)
    return texto


def pdf(datos: bytes):
    return types.Part.from_bytes(data=datos, mime_type="application/pdf")


async def generar_json(contenido: list) -> dict:
    return json.loads(await generar(contenido, _config_json))


async def generar(contenido: list, ajustes=_config_texto) -> str:
    ultimo_error = None

    for modelo in config.MODELOS:
        for intento in range(config.INTENTOS_POR_MODELO):
            try:
                respuesta = await _cliente.aio.models.generate_content(
                    model=modelo, contents=contenido, config=ajustes
                )
                return (respuesta.text or "").strip()
            except Exception as e:
                ultimo_error = e
                saturado = any(c in str(e) for c in ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED"))
                if not saturado:
                    raise
                espera = 2 * (intento + 1)
                print(f"{modelo} saturado (intento {intento + 1}), reintento en {espera}s...")
                await asyncio.sleep(espera)

    raise ultimo_error
