import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# El nombre de la productora, lo usan los prompts y los mensajes de Cobi. Si cambia, se toca acá y listo
EMPRESA = "SER Seguros"

# Las compañías con las que laburamos. Si se suma una, se agrega acá
COMPANIAS = ["Nación Seguros", "ATM Seguros"]

# Canal donde Cobi deja una tarjeta por cada alta pendiente de emisión (se borra cuando se emite)
CANAL_PENDIENTES = "pendientes-emision"

# Canal para preguntarle cosas a Cobi ("che cobi, patente 329jsp, ¿qué alta fue?")
CANAL_CHAT = "cobi"

# Alerta de certificados: si un alta sigue pendiente de emisión a los X días, Cobi avisa en este canal (el mismo de preguntarle cosas, así todo lo que dice Cobi queda junto).
# El certificado de cobertura vale VIGENCIA_CERTIFICADO días
CANAL_ALERTAS = "cobi"
DIAS_ALERTA_CERTIFICADO = 10
VIGENCIA_CERTIFICADO = 15

# Cómo trabaja cada compañía en el alta. La clave es la primera palabra del nombre, sin tildes ("nacion", "atm").
# - tarea_fotos: cómo se llama el tilde de las fotos en la lista
# - fotos: qué te dice Cobi cuando ya cargaste la solicitud y faltan las fotos ({solicitud} = el N° de solicitud)
# - emision_inmediata: si la póliza sale en el momento, el cierre ofrece solo "Emitida"
COMPANIAS_ALTA = {
    "atm": {
        "tarea_fotos": "Fotos cargadas en la web",
        "fotos": "Cargá las fotos en la web de ATM (tienen que estar **fechadas**) y tocá **Continuar inspección**. Cuando termines, marcá 📸 Fotos listas.",
        "emision_inmediata": True,
    },
    "nacion": {
        "tarea_fotos": "Fotos enviadas por mail",
        "fotos": "Mandá las fotos por mail a Nación con el N° de solicitud {solicitud}. Cuando termines, marcá 📸 Fotos listas.",
        "emision_inmediata": False,
    },
}

# Los canales donde Cobi se fija si suben PDFs de cotizaciones
CANALES_PDF = {"cotizar", "bot-pruebas"}

# El foro donde Cobi abre un hilo por cada alta
FORO_ALTAS = "altas"

# Las etiquetas de estado del foro de altas. Cobi las va cambiando solo a medida que avanza la lista
ETIQUETAS_ALTA = {
    "esperando_docs": "Esperando docs",
    "cargada": "Cargada en web",
    "fotos": "Fotos enviadas",
    "emitida": "Póliza emitida",
}

# Modelos de Gemini: si el primero está saturado o sin cuota, pasamos al otro. Cada modelo tiene su propia cuota gratis,
# así que tener varios da más chances. El flash-lite lo sacamos porque se cuelga sin contestar
MODELOS = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-flash-latest"]
# Cuántas vueltas por todos los modelos antes de rendirse, y cuántos segundos le damos a cada intento.
# Con Gemini gratis saturado no tiene sentido insistir minutos: probamos una vez cada modelo y avisamos
VUELTAS_DE_REINTENTO = 1
ESPERA_MAXIMA_IA = 30
