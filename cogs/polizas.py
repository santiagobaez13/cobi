import re
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

import config
import db
import hoja
import ia
from util import ahora, buscar_etiqueta, clave_compania, mismo_nombre, normalizar, para_la_web

FORMATO_FECHA = "%Y-%m-%d %H:%M:%S"

# El foro #polizas es la cartera: cuando un alta se emite, Cobi abre ahí una ficha con los datos de la póliza.
# La historia (fotos, DNI, mensajes) queda en el hilo del alta; la ficha linkea a ese hilo


def foro_polizas(bot: commands.Bot) -> discord.ForumChannel | None:
    return discord.utils.find(
        lambda c: isinstance(c, discord.ForumChannel) and mismo_nombre(c.name, config.FORO_POLIZAS), bot.get_all_channels()
    )


def texto_ficha(alta) -> str:
    datos = db.datos_alta(alta)
    asegurado = hoja.nombre_del_asegurado(alta, datos)
    dni = f" · DNI {para_la_web('dni', datos['dni'])}" if datos.get("dni") else ""
    patente = f" · Patente **{datos['patente'].upper()}**" if datos.get("patente") else ""
    emitida = datetime.strptime(alta["emitida_en"], FORMATO_FECHA).strftime("%d/%m/%Y") if alta["emitida_en"] else "?"
    return (
        f"🛡️ **{asegurado}**{dni}\n"
        f"🚗 {alta['vehiculo'] or '?'} {alta['anio'] or ''}{patente}\n"
        f"🏦 {alta['compania'] or '?'} · {alta['cobertura'] or '?'}\n"
        f"🔢 N° de solicitud: {alta['n_solicitud'] or '—'}\n"
        f"📄 N° de póliza: {alta['n_poliza'] or '—'}\n"
        f"📅 Emitida el {emitida} · Vigencia: {alta['vigencia'] or '—'}\n"
        + (f"📂 La solicitud, con toda la historia 👉 <#{alta['hilo_id']}>" if alta["hilo_id"] else "✍️ Cargada a mano con /poliza (no pasó por Cobi)")
    )


def titulo_ficha(alta) -> str:
    asegurado = hoja.nombre_del_asegurado(alta, db.datos_alta(alta))
    patente = db.datos_alta(alta).get("patente")
    partes = [asegurado, f"{alta['vehiculo'] or '?'} {alta['anio'] or ''}".strip(), (patente or "").upper(), alta["compania"] or ""]
    return " – ".join(p for p in partes if p)[:100]


def vista_ficha(alta_id: int) -> discord.ui.View:
    vista = discord.ui.View(timeout=None)
    vista.add_item(BotonPoliza(alta_id))
    return vista


async def crear_ficha(bot: commands.Bot, alta_id: int):
    alta = db.obtener_alta(alta_id)
    foro = foro_polizas(bot)
    if alta is None or foro is None or alta["poliza_hilo_id"]:
        return
    etiquetas = [e for e in (buscar_etiqueta(foro, alta["compania"] or ""), buscar_etiqueta(foro, alta["tipo"] or "")) if e]
    try:
        nueva = await foro.create_thread(
            name=titulo_ficha(alta), content=texto_ficha(alta), applied_tags=etiquetas, view=vista_ficha(alta_id),
        )
    except discord.HTTPException as e:
        print(f"No pude crear la ficha de la póliza del alta {alta_id}: {e}")
        return
    db.actualizar_alta(alta_id, poliza_hilo_id=nueva.thread.id)


async def borrar_ficha(bot: commands.Bot, alta_id: int):
    # Para "Volver atrás" desde emitida y para /limpiar
    alta = db.obtener_alta(alta_id)
    if alta is None or not alta["poliza_hilo_id"]:
        return
    try:
        hilo = bot.get_channel(alta["poliza_hilo_id"]) or await bot.fetch_channel(alta["poliza_hilo_id"])
        await hilo.delete()
    except discord.HTTPException as e:
        print(f"No pude borrar la ficha de la póliza del alta {alta_id}: {e}")
    db.actualizar_alta(alta_id, poliza_hilo_id=None)


class ModalPoliza(discord.ui.Modal, title="Completar la póliza"):
    def __init__(self, alta):
        super().__init__()
        self.alta_id = alta["id"]
        self.n_poliza = discord.ui.TextInput(max_length=40, required=False, default=alta["n_poliza"])
        self.add_item(discord.ui.Label(text="N° de póliza", component=self.n_poliza))
        self.vigencia = discord.ui.TextInput(placeholder="Por ejemplo: 28/10/2026 al 28/10/2027", max_length=60, required=False, default=alta["vigencia"])
        self.add_item(discord.ui.Label(text="Vigencia", component=self.vigencia))
        # La patente a veces no está porque no se leyó la cédula: acá se carga o se corrige
        self.patente = discord.ui.TextInput(placeholder="Por ejemplo: AB123CD", max_length=10, required=False, default=db.datos_alta(alta).get("patente"))
        self.add_item(discord.ui.Label(text="Patente", component=self.patente))

    async def on_submit(self, interaction: discord.Interaction):
        db.actualizar_alta(self.alta_id, n_poliza=self.n_poliza.value.strip() or None, vigencia=self.vigencia.value.strip() or None)
        patente = re.sub(r"[^A-Za-z0-9]", "", self.patente.value).upper()
        if patente and patente != db.datos_alta(db.obtener_alta(self.alta_id)).get("patente"):
            db.sumar_datos_alta(self.alta_id, {"patente": patente})
        alta = db.obtener_alta(self.alta_id)
        await interaction.response.edit_message(content=texto_ficha(alta), view=vista_ficha(self.alta_id))
        # Si cambió la patente, también el título de la ficha (así se encuentra buscando en el foro)
        if isinstance(interaction.channel, discord.Thread) and interaction.channel.name != titulo_ficha(alta):
            try:
                await interaction.channel.edit(name=titulo_ficha(alta))
            except discord.HTTPException as e:
                print(f"No pude cambiar el título de la ficha del alta {self.alta_id}: {e}")


class BotonPoliza(discord.ui.DynamicItem[discord.ui.Button], template=r"poliza:completar:(?P<id>\d+)"):
    def __init__(self, alta_id: int):
        super().__init__(discord.ui.Button(label="Completar póliza", emoji="✍️", style=discord.ButtonStyle.secondary, custom_id=f"poliza:completar:{alta_id}"))
        self.alta_id = alta_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction):
        alta = db.obtener_alta(self.alta_id)
        if alta is None:
            await interaction.response.send_message("No encontré esta póliza en la base 🤔", ephemeral=True)
            return
        await interaction.response.send_modal(ModalPoliza(alta))


class ModalPolizaNueva(discord.ui.Modal, title="Cargar póliza a mano"):
    # Para las pólizas que se emiten por fuera de Cobi: quedan en la base, con su ficha en #polizas y su fila en la hoja
    def __init__(self, compania: str, tipo: str, productor: str):
        super().__init__()
        self.compania, self.tipo, self.productor = compania, tipo, productor
        self.asegurado = discord.ui.TextInput(placeholder="Apellido Nombre, como en el DNI", max_length=80)
        self.add_item(discord.ui.Label(text="Asegurado", component=self.asegurado))
        self.dni = discord.ui.TextInput(max_length=15, required=False)
        self.add_item(discord.ui.Label(text="DNI", component=self.dni))
        self.patente = discord.ui.TextInput(placeholder="Por ejemplo: AB123CD", max_length=10, required=False)
        self.add_item(discord.ui.Label(text="Patente", component=self.patente))
        self.vehiculo = discord.ui.TextInput(placeholder="Por ejemplo: Gol Trend 2015", max_length=60, required=False)
        self.add_item(discord.ui.Label(text="Vehículo y año", component=self.vehiculo))
        self.n_poliza = discord.ui.TextInput(max_length=40, required=False)
        self.add_item(discord.ui.Label(text="N° de póliza", component=self.n_poliza))

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        vehiculo = " ".join(self.vehiculo.value.split())
        anio = m.group() if (m := re.search(r"\b(19|20)\d{2}\b", vehiculo)) else ""
        if anio:
            vehiculo = " ".join(vehiculo.replace(anio, " ").split()).strip(" -–,")
        leido = {
            "asegurado": self.asegurado.value, "dni": self.dni.value, "patente": self.patente.value,
            "vehiculo": vehiculo, "anio": anio, "tipo": self.tipo, "n_poliza": self.n_poliza.value,
        }
        texto = await registrar_poliza(interaction.client, leido, self.compania, self.productor)
        await interaction.followup.send(texto + "\n📎 Si tenés el PDF de la póliza, subilo en la ficha y completo el N° y la vigencia.", ephemeral=True)


def companias_conocidas() -> list[str]:
    # Las de config más las que ya aparecieron en pólizas cargadas: así una compañía nueva queda "registrada" sola
    vistas = [c for c in db.companias_en_la_base() if c and c != "?"]
    return list(dict.fromkeys(config.COMPANIAS + sorted(vistas)))


def prompt_poliza() -> str:
    return ia.leer_prompt("poliza", companias=", ".join(companias_conocidas()))


async def registrar_poliza(bot, leido: dict, compania: str, productor: str, pdf: discord.Attachment | None = None) -> str:
    # Una póliza emitida por fuera de Cobi (con /poliza): va a la misma tabla que las altas, ya emitida y sin
    # cotización ni hilo de solicitud (cotizacion_id 0). Le abre su ficha y la manda a la hoja. Devuelve qué pasó
    limpio = lambda clave: " ".join(str(leido.get(clave) or "").split())
    datos = {}
    if patente := re.sub(r"[^A-Za-z0-9]", "", limpio("patente")).upper():
        datos["patente"] = patente
    if dni := limpio("dni"):
        datos["dni"] = dni
    tipo = "Moto" if normalizar(limpio("tipo")) == "moto" else "Auto"
    alta_id = db.nueva_alta(
        0, limpio("asegurado").title() or "?", compania, limpio("vehiculo") or None, limpio("anio"), tipo, limpio("cobertura") or None, None, datos=datos,
    )
    desde, hasta = limpio("vigencia_desde"), limpio("vigencia_hasta")
    db.actualizar_alta(
        alta_id, estado="emitida", emitida_en=ahora().strftime(FORMATO_FECHA),
        n_poliza=limpio("n_poliza") or None, vigencia=f"{desde} al {hasta}" if desde and hasta else None,
    )

    await crear_ficha(bot, alta_id)
    alta = db.obtener_alta(alta_id)
    renglones = []
    if alta["poliza_hilo_id"]:
        renglones.append(f"✅ Cargada la póliza de **{hoja.nombre_del_asegurado(alta, datos)}** ({compania}). Su ficha 👉 <#{alta['poliza_hilo_id']}>")
        if pdf is not None:
            # El PDF queda guardado en la ficha, así está a mano cuando el cliente la pide
            try:
                hilo = bot.get_channel(alta["poliza_hilo_id"]) or await bot.fetch_channel(alta["poliza_hilo_id"])
                await hilo.send("📄 La póliza", file=await pdf.to_file())
            except discord.HTTPException as e:
                print(f"No pude dejar el PDF en la ficha del alta {alta_id}: {e}")
    else:
        renglones.append(f"✅ Cargada en la base, pero no pude abrir la ficha (¿existe el foro #{config.FORO_POLIZAS}?).")
    if hoja.activa():
        hoja.anotar_para_cargar(alta_id, productor)
        renglones.append(
            f"📊 En la hoja a nombre de **{productor}**." if await hoja.cargar(alta_id)
            else "⚠️ Google no respondió: la mando a la hoja sola en la próxima hora."
        )
    if clave_compania(compania) not in {clave_compania(c) for c in config.COMPANIAS}:
        renglones.append(f"🆕 Es la primera de **{compania}**: si querés que tenga etiqueta en #{config.FORO_POLIZAS}, creala con ese nombre.")
    return "\n".join(renglones)


def es_pdf(adjunto: discord.Attachment) -> bool:
    return adjunto.filename.lower().endswith(".pdf") or (adjunto.content_type or "").startswith("application/pdf")


def mismas_personas(uno: str, otro: str) -> bool:
    # "Albornoz Magali Agustina" y "ALBORNOZ, MAGALI A." son la misma: alcanza con que compartan alguna palabra larga
    palabras = lambda t: {p for p in re.findall(r"[a-z]+", normalizar(t)) if len(p) > 2}
    return bool(palabras(uno) & palabras(otro))


async def leer_pdf_de_la_ficha(message: discord.Message, alta) -> list[str]:
    # Lee los PDFs que subieron a la ficha (la póliza, el certificado...) y completa lo que falte.
    # Devuelve los avisos para el productor (si algo no coincide)
    avisos, cambios = [], {}
    datos = db.datos_alta(alta)
    for adjunto in (a for a in message.attachments if es_pdf(a)):
        leido = await ia.generar_json([ia.pdf(await adjunto.read()), prompt_poliza()], tarea="poliza")
        if leido.get("documento") == "otro":
            avisos.append(f"🤔 **{adjunto.filename}** no parece una póliza ni un certificado, no saqué nada de ahí.")
            continue
        # Si es de otra persona no cargamos nada: mejor avisar que ensuciar la ficha
        asegurado = hoja.nombre_del_asegurado(alta, datos)
        if leido.get("asegurado") and not mismas_personas(leido["asegurado"], asegurado):
            avisos.append(
                f"⚠️ En **{adjunto.filename}** el asegurado es **{leido['asegurado']}** y en la ficha **{asegurado}**. "
                "No cargué nada: ¿es la póliza de este cliente?"
            )
            continue
        if leido.get("n_poliza") and leido.get("documento") == "poliza":
            cambios["n_poliza"] = leido["n_poliza"]
        if leido.get("vigencia_desde") and leido.get("vigencia_hasta"):
            cambios["vigencia"] = f"{leido['vigencia_desde']} al {leido['vigencia_hasta']}"
        patente = re.sub(r"[^A-Za-z0-9]", "", leido.get("patente") or "").upper()
        if patente and not datos.get("patente"):
            db.sumar_datos_alta(alta["id"], {"patente": patente})
        elif patente and patente != datos["patente"].upper():
            avisos.append(f"⚠️ En **{adjunto.filename}** la patente es **{patente}** y en la ficha **{datos['patente'].upper()}**. Fijate cuál está bien.")
    if cambios:
        db.actualizar_alta(alta["id"], **cambios)
    return avisos


class Polizas(commands.Cog):
    @app_commands.command(name="poliza", description="Cargar una póliza emitida por fuera de Cobi: subí el PDF y armo la ficha y la fila de la hoja")
    @app_commands.describe(
        pdf="El PDF de la póliza: saco todos los datos de ahí",
        productor="Quién la emitió (si no lo elegís, vos)",
        compania="Solo para cargarla a mano, sin PDF (podés escribir una nueva)",
    )
    @app_commands.choices(productor=[app_commands.Choice(name=n[0], value=n[0]) for n in config.EQUIPO.values()])
    async def poliza(self, interaction: discord.Interaction, pdf: discord.Attachment | None = None,
                     productor: str | None = None, compania: str | None = None):
        if interaction.user.id not in config.EQUIPO:
            await interaction.response.send_message("Esto es solo para los del equipo 🙏", ephemeral=True)
            return
        productor = productor or config.EQUIPO[interaction.user.id][0]

        # Sin PDF: la ventanita para cargarla a mano (ahí sí hace falta saber la compañía)
        if pdf is None:
            if not compania:
                await interaction.response.send_message("Adjuntá el PDF de la póliza en **pdf** 📎 (o elegí la **compania** para cargarla a mano).", ephemeral=True)
                return
            await interaction.response.send_modal(ModalPolizaNueva(compania.strip(), "Auto", productor))
            return
        if not es_pdf(pdf):
            await interaction.response.send_message("Eso no es un PDF 🤔", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            leido = await ia.generar_json([ia.pdf(await pdf.read()), prompt_poliza()], tarea="poliza")
        except Exception as e:
            print(f"Error leyendo la póliza {pdf.filename} de /poliza: {e}")
            await interaction.followup.send("⚠️ No pude leer el PDF (la IA no respondió). Probá de nuevo en un rato.", ephemeral=True)
            return
        if not isinstance(leido, dict) or leido.get("documento") not in ("poliza", "certificado") or not leido.get("asegurado"):
            await interaction.followup.send(f"🤔 **{pdf.filename}** no parece una póliza: no cargué nada.", ephemeral=True)
            return
        # Si ya está cargada (por N° de póliza), no la duplicamos
        if leido.get("n_poliza") and (ya := db.alta_por_n_poliza(str(leido["n_poliza"]).strip())):
            donde = f"<#{ya['poliza_hilo_id']}>" if ya["poliza_hilo_id"] else "la base"
            await interaction.followup.send(f"👀 La póliza N° {leido['n_poliza']} ya estaba cargada 👉 {donde}", ephemeral=True)
            return
        texto = await registrar_poliza(interaction.client, leido, leido.get("compania") or compania or "?", productor, pdf)
        await interaction.followup.send(texto, ephemeral=True)

    @poliza.autocomplete("compania")
    async def compania_autocompletar(self, interaction: discord.Interaction, actual: str):
        # Sugiere las conocidas, pero se puede escribir cualquiera (si es nueva, queda registrada al cargarla)
        return [app_commands.Choice(name=c, value=c) for c in companias_conocidas() if normalizar(actual) in normalizar(c)][:25]

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Si suben un PDF a una ficha de #polizas, Cobi lo lee y la completa (N° de póliza, vigencia, patente)
        hilo = message.channel
        if message.author.bot or not isinstance(hilo, discord.Thread) or not any(es_pdf(a) for a in message.attachments):
            return
        if hilo.parent is None or not mismo_nombre(hilo.parent.name, config.FORO_POLIZAS):
            return
        alta = db.alta_por_ficha(hilo.id)
        if alta is None:
            return
        try:
            async with hilo.typing():
                avisos = await leer_pdf_de_la_ficha(message, alta)
        except Exception as e:
            print(f"Error leyendo la póliza de la ficha del alta {alta['id']}: {e}")
            await message.reply("⚠️ No pude leer el PDF (la IA no respondió). Subilo de nuevo en un rato.")
            return

        alta = db.obtener_alta(alta["id"])
        try:
            await hilo.get_partial_message(hilo.id).edit(content=texto_ficha(alta), view=vista_ficha(alta["id"]))
            if hilo.name != titulo_ficha(alta):
                await hilo.edit(name=titulo_ficha(alta))
        except discord.HTTPException as e:
            print(f"No pude actualizar la ficha del alta {alta['id']}: {e}")
        await message.add_reaction("⚠️" if avisos else "✅")
        if avisos:
            await message.reply("\n".join(avisos), mention_author=False)


async def setup(bot: commands.Bot):
    bot.add_dynamic_items(BotonPoliza)
    await bot.add_cog(Polizas())
