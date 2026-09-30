# Cobi 🛡️

Cobi es el asistente en Discord de **SER Seguros**, una productora de seguros argentina. Lo usa el equipo de la oficina para organizar cotizaciones, altas, siniestros y cobranzas, y para sacarse de encima las tareas repetitivas.

## El problema

Antes todo se manejaba con grupos de WhatsApp y planillas de Excel. Los datos, las fotos y los PDFs de cada cliente quedaban desparramados, y había que armar a mano mensajes que siempre eran parecidos.

## La solución

Cada cliente tiene su propio hilo en un foro de Discord (uno por área: cotizaciones, altas, siniestros y cobranzas). En el hilo queda todo junto: datos, fotos, PDFs y en qué estado está. Cobi trabaja adentro de esos hilos y hace lo repetitivo.

Los clientes no entran al Discord: siguen hablando por WhatsApp. Cobi le deja al equipo los mensajes listos para copiar y mandar.

## Qué hace hoy

- **PDF de cotización → mensaje de WhatsApp.** Subís el PDF que devuelve la web de la compañía (Nación Seguros, ATM) en un hilo del foro `cotizaciones`, y Cobi lo lee con IA y responde con un mensaje limpio: coberturas con nombres entendibles, precios en formato argentino y forma de pago. Llega en un bloque de código para que al copiarlo no se pierdan los asteriscos de WhatsApp.
- `/hola` para ver si Cobi está vivo.

## Qué viene (V1)

- `/elegida`: arma el mensaje de la cobertura que eligió el cliente, releyendo el PDF del hilo.
- `/plantilla`: los mensajes predeterminados al toque (requisitos de alta, confirmación, siniestros, etc.).
- Botones para cambiar el estado de un hilo con un clic.
- Lectura de cédula (patente, chasis, motor) y de CBU (banco), lista para copiar.
- Checklist de fotos del vehículo: Cobi reconoce cuáles subieron y avisa cuáles faltan.
- Alerta cuando un certificado de cobertura está por vencer y la póliza todavía no salió.
- Mensajes de cobro por cliente a partir de la lista de cobros rechazados.

## Tecnologías

- Python 3.11
- [discord.py](https://discordpy.readthedocs.io/)
- Google Gemini (`google-genai`) para leer PDFs e imágenes

## Estructura

```
main.py               ← arranca a Cobi y carga los cogs
config.py             ← nombre de la empresa, canales y modelos de IA
ia.py                 ← todo lo de la IA, con reintentos si está saturada
prompts/              ← las instrucciones para la IA, en .txt
cogs/
  general.py          ← /hola
  cotizaciones.py     ← PDF → mensaje de WhatsApp
```

Los prompts están en archivos aparte para poder ajustarlos sin tocar el código. Toda la IA pasa por `ia.py`, así el día que se cambie de proveedor se toca un solo archivo.

## Cómo correrlo en local

1. Cloná el repo y armá el entorno:
   ```bash
   python -m venv venv
   source venv/Scripts/activate   # en Windows con Git Bash
   pip install -r requirements.txt
   ```
2. Copiá `.env.example` a `.env` y completá las claves:
   - `DISCORD_TOKEN`: el token del bot, del [Developer Portal de Discord](https://discord.com/developers/applications).
   - `GEMINI_API_KEY`: la clave de [Google AI Studio](https://aistudio.google.com/).
3. Arrancalo:
   ```bash
   python main.py
   ```

⚠️ Si Cobi también está prendido en el servidor, apagalo mientras probás en local. Si corren los dos a la vez, responde todo duplicado.

### Configuración del bot en Discord

- Intent: **Message Content Intent** activado.
- Scopes: `bot` y `applications.commands`.
- Permisos: ver canales, enviar mensajes (también en hilos), gestionar hilos, insertar enlaces, adjuntar archivos, leer el historial, añadir reacciones y usar comandos de barra.

Para cambiar el nombre de la empresa o los canales donde Cobi lee PDFs, se toca `config.py`.

## Deploy

Cobi corre en [Wispbyte](https://wispbyte.com/). Los archivos se suben a mano desde el panel, el archivo principal tiene que llamarse `main.py` y las claves se cargan en el editor de `.env` del panel. **Nunca se suben `venv/` ni `.env`.**

## Versiones

Cada versión estable queda marcada con una etiqueta en GitHub (pestaña *Tags*):

- `v0`: el Cobi original, todo en un solo archivo.
- `v0.1`, `v0.2`, …: cada avance.
- `v1.0`: la V1 completa.

## Privacidad

En el repo solo está el código. Los datos de los clientes (PDFs, cédulas, fotos, CBU) viven en Discord y **nunca** se suben acá, ni siquiera para probar.
