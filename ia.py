import asyncio
from pathlib import Path

from google import genai
from google.genai import types

import config

CARPETA_PROMPTS = Path(__file__).parent / "prompts"

_cliente = genai.Client(api_key=config.GEMINI_API_KEY)
_config = types.GenerateContentConfig(
    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
)


def leer_prompt(nombre: str) -> str:
    # Lo leemos cada vez, así si tocás el .txt no hace falta reiniciar a Cobi
    texto = (CARPETA_PROMPTS / f"{nombre}.txt").read_text(encoding="utf-8")
    return texto.replace("{empresa}", config.EMPRESA)


def pdf(datos: bytes):
    return types.Part.from_bytes(data=datos, mime_type="application/pdf")


async def generar(contenido: list) -> str:
    ultimo_error = None

    for modelo in config.MODELOS:
        for intento in range(config.INTENTOS_POR_MODELO):
            try:
                respuesta = await _cliente.aio.models.generate_content(
                    model=modelo, contents=contenido, config=_config
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
