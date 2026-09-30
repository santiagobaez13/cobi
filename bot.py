import os
import io
import asyncio
import discord
from discord import app_commands
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

# Modelos de Gemini: si el primero está saturado, prueba con el siguiente
MODELOS = ["gemini-flash-latest", "gemini-flash-lite-latest"]
INTENTOS_POR_MODELO = 3

# Canales donde Cobi lee los PDFs de cotizaciones
CANALES_PDF = {"cotizaciones", "bot-pruebas"}

PROMPT_COTIZACION = """Sos el asistente de SER Seguros, una productora de seguros argentina. Te paso el PDF de una cotización de una compañía de seguros. Armá el mensaje de WhatsApp para el cliente siguiendo EXACTAMENTE este formato:

Te paso la cotización de [Compañía] para tu vehículo:

Compañía: [Compañía]
🚗 [VEHÍCULO tal como figura en el PDF] – Año [año]
💰 Valor asegurado: $[valor]

Opciones de cobertura:

* [Nombre amigable]: $[premio] por [mes, bimestre, etc., según la vigencia del PDF]
* [Nombre amigable]: $[premio] por [...]

El [nombre de la cobertura más completa] incluye [beneficios principales] y otros beneficios adicionales.

💳 Forma de pago: [forma de pago].
📲 Presupuesto N° [número]

EJEMPLO REAL de cómo tiene que quedar:

Te paso la cotización de Nación Seguros para tu vehículo:

Compañía: Nación Seguros
🚗 RENAULT-KANGOO 2 EXPRESS 1.6 CONFORT 1 PLC AA – Año 2011
💰 Valor asegurado: $10.285.000

Opciones de cobertura:

* Responsabilidad Civil: $73.433,49 por mes
* Responsabilidad Civil + Asistencia Mecánica: $80.796,21 por mes
* RC + Robo/Incendio Total: $87.485,95 por mes
* RC + Robo, Incendio y Destrucción Total: $90.803,10 por mes
* Terceros Completo: $93.751,69 por mes
* Terceros Completo + Destrucción Total: $97.894,41 por mes
* Terceros Completo Premium Nación: $114.657,04 por mes

El Terceros Completo Premium incluye responsabilidad civil, robo e incendio total y parcial, destrucción total por accidente (cláusula del 80%), vidrios laterales, cerraduras, luneta y parabrisas, granizo, daño parcial por robo total aparecido y otros beneficios adicionales.

💳 Forma de pago: Débito Automático.
📲 Presupuesto N° 18899938

REGLAS:
- Nombres amigables: traducí los nombres técnicos como en el ejemplo (por ejemplo "B1 - RC TODO TOTALES" → "RC + Robo/Incendio Total"). Nunca copies nombres cortados del PDF.
- Precios con centavos y formato argentino ($73.433,49), exactamente como figuran en el PDF.
- Si el PDF tiene GNC asegurado, agregá debajo del valor asegurado la línea: ⛽ GNC: $[monto]
- No incluyas los montos de accidentes personales.
- Si hay UNA sola cobertura, en lugar de "Opciones de cobertura" poné:
  🛡️ Cobertura: [nombre amigable]
  Incluye:
  ✔️ [un beneficio por línea]
  💳 Valor: $[premio] por [período]
  Si el PDF aclara que incluye seguro de vida, sumalo como un ✔️ más.
- Para débito en CBU poné "Débito Automático"; para tarjeta, "Tarjeta de Crédito".
- No incluyas datos del tomador ni del productor.
- No inventes nada que no esté en el PDF.
- Respondé únicamente con el mensaje, sin comentarios."""

ia = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


def es_canal_de_cotizaciones(canal) -> bool:
    # En un foro, cada publicación es un hilo: miramos el nombre del foro "padre"
    if isinstance(canal, discord.Thread) and canal.parent:
        return canal.parent.name in CANALES_PDF
    return getattr(canal, "name", "") in CANALES_PDF


async def armar_mensaje(pdf_bytes: bytes) -> str:
    contenido = [
        types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
        PROMPT_COTIZACION,
    ]
    config = types.GenerateContentConfig(
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
    )
    ultimo_error = None

    for modelo in MODELOS:
        for intento in range(INTENTOS_POR_MODELO):
            try:
                respuesta = await ia.aio.models.generate_content(
                    model=modelo, contents=contenido, config=config
                )
                texto = (respuesta.text or "").strip()
                return texto.replace("```", "")
            except Exception as e:
                ultimo_error = e
                saturado = any(c in str(e) for c in ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED"))
                if not saturado:
                    raise
                espera = 2 * (intento + 1)
                print(f"{modelo} saturado (intento {intento + 1}), reintento en {espera}s...")
                await asyncio.sleep(espera)

    raise ultimo_error


@tree.command(name="hola", description="Saludá a Cobi")
async def hola(interaction: discord.Interaction):
    await interaction.response.send_message("¡Hola! Soy Cobi 🛡️, listo para ayudar en SER Seguros.")


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if not es_canal_de_cotizaciones(message.channel):
        return

    pdfs = [a for a in message.attachments if a.filename.lower().endswith(".pdf")]

    for adjunto in pdfs:
        async with message.channel.typing():
            try:
                pdf_bytes = await adjunto.read()
                texto = await armar_mensaje(pdf_bytes)
            except Exception as e:
                print(f"Error procesando {adjunto.filename}: {e}")
                await message.reply(f"Uy, no pude leer **{adjunto.filename}** 😕 Probá subirlo de nuevo en un ratito.")
                continue

        if not texto:
            await message.reply(f"Leí **{adjunto.filename}** pero no me salió armar el mensaje 😕 Probá de nuevo.")
            continue

        # Va dentro de un bloque de código para que al copiarlo se mantengan los asteriscos de WhatsApp
        if len(texto) <= 1900:
            await message.reply(f"📲 Mensaje listo para WhatsApp:\n```\n{texto}\n```")
        else:
            archivo = discord.File(io.BytesIO(texto.encode("utf-8")), filename="mensaje_whatsapp.txt")
            await message.reply("📲 El mensaje quedó largo, te lo paso en un archivo:", file=archivo)


@client.event
async def on_ready():
    await tree.sync()
    print(f"Cobi conectado como {client.user}")


client.run(os.getenv("DISCORD_TOKEN"))
