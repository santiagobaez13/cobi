# Cobi 🛡️

Cobi es el asistente en Discord de **SER Seguros**, una productora de seguros argentina. Lo usa el equipo de la oficina para organizar cotizaciones, altas, siniestros y cobranzas, y para sacarse de encima las tareas repetitivas.

## El problema

Antes todo se manejaba con grupos de WhatsApp y planillas de Excel. Los datos, las fotos y los PDFs de cada cliente quedaban desparramados, y había que armar a mano mensajes que siempre eran parecidos.

## La solución

Cobi guía al equipo paso a paso con botones, desde que llega la cotización hasta que la póliza queda emitida. Las cotizaciones entran por un canal de texto (la mayoría no avanza, así que no ensucian nada), y cuando el cliente acepta, cada alta pasa a tener su propio hilo en un foro de Discord, donde Cobi junta los datos que le van pasando y arma la solicitud lista para cargar.

Los clientes no entran al Discord: siguen hablando por WhatsApp. Cobi le deja al equipo los mensajes listos para copiar y mandar.

## Qué hace hoy

**Cotizaciones (`#cotizar`)**
- **PDF de cotización → mensaje de WhatsApp.** Subís el PDF que devuelve la web de la compañía (Nación Seguros, ATM) y Cobi lo lee con IA y responde con un mensaje limpio: coberturas con nombres entendibles, precios en formato argentino y forma de pago. Llega en un bloque de código para que al copiarlo no se pierdan los asteriscos de WhatsApp.
- **Seguimiento.** Abajo del mensaje hay botones `✅ Aceptó` y `❌ No avanzó`. Los botones siguen andando aunque Cobi se reinicie.
- **Aceptó → alta.** Se abre una ventanita para elegir la cobertura y cargar el nombre del cliente, el N° de solicitud y el teléfono. Cobi relee el PDF, arma el mensaje de "Cobertura elegida" y crea el hilo del alta en el foro `altas`, con título y etiquetas. Si la cotización trae GNC, lo tiene en cuenta.

**Altas (foro `altas`): una lista de tareas viva**
- Al crear el hilo, Cobi manda los requisitos para el cliente (la imagen de la oficina + un texto corto), según compañía y si es auto o moto.
- Abajo deja **una sola lista** que se va tildando sola y siempre queda al final del hilo: DNI, cédula, GNC, forma de pago, mail, teléfono, N° de solicitud y fotos.
- El productor le va pasando a Cobi lo que le llega por WhatsApp, en el orden que sea:
  - **Imágenes** de DNI, cédula, cédula y oblea de GNC, CBU o tarjeta: Cobi las lee con IA.
  - **Texto**, incluso pegado de WhatsApp: mail, teléfono, CBU (con o sin banco), N° de solicitud.
- **La solicitud en texto plano** está adentro de la lista y se completa a medida que llegan los datos, lista para copiar en la web de la compañía.
- **Controles:** CBU con dígitos verificadores, banco según el número, aviso de CVU, y la cédula comparada contra el vehículo cotizado.
- **Botones:** `✍️ Cargar a mano`, `⏭️ Omitir` (lo que no hace falta), `📸 Fotos listas`, y el cierre: `⏳ Pendiente de emisión` / `✅ Emitida`, según cómo trabaje cada compañía.
- Lo que falta nunca frena: Cobi sigue y lo deja como nota.

**Pendientes de emisión (`#pendientes-emision`)**
- Una tarjeta por cada alta que espera la póliza, con los días que lleva y cuándo vence el certificado. Desde la tarjeta se marca como emitida y desaparece.
- Si a los 10 días sigue pendiente, aviso en `#alertas`.

**Buscador (`#cobi`)**
- "che cobi, patente 329jsp, ¿qué alta fue?": busca por patente, DNI, N° de solicitud, teléfono o apellido y responde con el link al hilo y su estado. Busca directo en la base, sin IA.

**Otros**
- `/plantilla`: los mensajes predeterminados de la oficina al toque, con su imagen si la tienen.
- `/panel`: vuelve a poner la lista del alta al final del hilo.

## Qué viene

- **Cobros vencidos:** un mensaje por cliente a partir de la lista de cobros rechazados.
- **Siniestros:** el predeterminado de datos para pedirle al cliente.
- Pasar a un plan pago de IA antes de usarlo con datos reales.

## Tecnologías

- Python 3.12
- [discord.py](https://discordpy.readthedocs.io/)
- Claude (Haiku), de Anthropic, para leer PDFs e imágenes
- SQLite

## Estructura

```
main.py               ← arranca a Cobi y carga los cogs
config.py             ← empresa, compañías, canales, alertas y modelos de IA
ia.py                 ← todo lo de la IA (Claude)
db.py                 ← la base de datos (SQLite)
documentos.py         ← lectura de documentos, CBU, bancos y datos escritos
util.py               ← cositas compartidas (nombres, etiquetas)
prompts/              ← las instrucciones para la IA, en .txt
plantillas/           ← los mensajes predeterminados (.md) y sus imágenes
cogs/
  general.py          ← /hola
  cotizaciones.py     ← #cotizar: PDF → WhatsApp, Aceptó → alta
  altas.py            ← la lista de tareas del alta, #pendientes-emision y alertas
  plantillas.py       ← /plantilla
  chat.py             ← el buscador de #cobi
```

Los prompts y las plantillas están en archivos aparte para poder ajustarlos sin tocar el código: para sumar una plantilla nueva alcanza con crear un `.md` en `plantillas/` (y si lleva imagen, ponerla al lado con el mismo nombre). Toda la IA pasa por `ia.py`, así el día que se cambie de proveedor se toca un solo archivo. Cómo trabaja cada compañía en el alta se configura en `COMPANIAS_ALTA` de `config.py`.

## Cómo correrlo en local

1. Cloná el repo y armá el entorno:
   ```bash
   python -m venv venv
   source venv/Scripts/activate   # en Windows con Git Bash
   pip install -r requirements.txt
   ```
2. Copiá `.env.example` a `.env` y completá las claves:
   - `DISCORD_TOKEN`: el token del bot, del [Developer Portal de Discord](https://discord.com/developers/applications).
   - `ANTHROPIC_API_KEY`: la clave de la [consola de Anthropic](https://console.anthropic.com/).
3. Arrancalo:
   ```bash
   python main.py
   ```

⚠️ Si Cobi también está prendido en el servidor, apagalo mientras probás en local (`sudo systemctl stop cobi`). Si corren los dos a la vez, responde todo duplicado.

### Configuración del bot en Discord

- Intent: **Message Content Intent** activado.
- Scopes: `bot` y `applications.commands`.
- Permisos: ver canales, enviar mensajes (también en hilos), gestionar hilos, insertar enlaces, adjuntar archivos, leer el historial, añadir reacciones y usar comandos de barra.

Canales que usa Cobi: `#cotizar`, el foro `altas`, `#pendientes-emision`, `#alertas` y `#cobi`. Los nombres se cambian en `config.py` (los emojis en el nombre del canal no molestan).

## Deploy

Cobi corre en un servidor propio (Oracle Cloud, Ubuntu) como servicio de systemd, así arranca solo y se levanta si se cae. Los archivos del servidor están en `servidor/`:

- `cobi.service`: el servicio de systemd.
- `actualizar.sh`: baja el código nuevo de GitHub, instala dependencias y reinicia a Cobi.

El `.env` se copia al servidor a mano (por `scp`), **nunca** por GitHub.

## Versiones

Cada versión estable queda marcada con una etiqueta en GitHub (pestaña *Tags*):

- `v0`: el Cobi original, todo en un solo archivo.
- `v0.1`, `v0.2`, …: cada avance.
- `v1.0`: la V1 completa.

## Privacidad

En el repo solo está el código. Los datos de los clientes (PDFs, cédulas, fotos, CBU) viven en Discord y **nunca** se suben acá, ni siquiera para probar. La base de datos (`cobi.db`) también queda afuera del repo.
