import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# El nombre de la productora, lo usan los prompts y los mensajes de Cobi. Si cambia, se toca acá y listo
EMPRESA = "SER Seguros"

# Las compañías con las que laburamos. Si se suma una, se agrega acá
COMPANIAS = ["Nación Seguros", "ATM Seguros"]

# Los canales donde Cobi se fija si suben PDFs de cotizaciones
CANALES_PDF = {"cotizar", "bot-pruebas"}

# El foro donde Cobi abre un hilo por cada alta, y con qué etiqueta de estado arranca
FORO_ALTAS = "altas"
ETIQUETA_ALTA_NUEVA = "Esperando docs"

# Modelos de Gemini: si el primero está saturado, pasamos al otro
MODELOS = ["gemini-flash-latest", "gemini-flash-lite-latest"]
INTENTOS_POR_MODELO = 3
