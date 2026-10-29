import re

import ia
from util import normalizar

# Qué datos tomamos de cada documento. Si la IA manda otra cosa, la ignoramos
CLAVES = {
    "dni_frente": ["apellido", "nombre", "dni", "sexo", "fecha_nacimiento", "nacionalidad"],
    "dni_dorso": ["domicilio", "cuil", "lugar_nacimiento"],
    "cedula": ["patente", "marca", "modelo", "tipo_vehiculo", "anio", "motor", "chasis", "titular"],
    "gnc_cedula": ["gnc_equipo", "gnc_cilindro", "gnc_vencimiento"],
    "gnc_oblea": ["gnc_oblea_numero", "gnc_oblea_vencimiento"],
    "cbu": ["cbu", "banco", "titular_cuenta", "alias"],
    "tarjeta": ["tarjeta_marca", "tarjeta_banco", "tarjeta_numero", "tarjeta_vencimiento"],
    # Lo que no es un documento: solo para poder decirle al productor qué le pasaron
    "otro": ["que_es"],
}

NOMBRES = {
    "apellido": "Apellido", "nombre": "Nombre", "dni": "DNI", "sexo": "Sexo",
    "fecha_nacimiento": "Fecha de nacimiento", "nacionalidad": "Nacionalidad",
    "domicilio": "Domicilio", "cuil": "CUIL", "lugar_nacimiento": "Lugar de nacimiento",
    "mail": "Mail", "telefono": "Teléfono",
    "patente": "Patente", "marca": "Marca", "modelo": "Modelo", "tipo_vehiculo": "Tipo", "anio": "Año",
    "motor": "N° de motor", "chasis": "N° de chasis", "titular": "Titular (cédula)",
    "gnc_equipo": "N° de equipo GNC", "gnc_cilindro": "N° de cilindro", "gnc_vencimiento": "Vencimiento cédula GNC",
    "gnc_oblea_numero": "N° de oblea", "gnc_oblea_vencimiento": "Vencimiento oblea",
    "cbu": "CBU", "banco": "Banco", "titular_cuenta": "Titular de la cuenta", "alias": "Alias",
    "tarjeta_marca": "Tarjeta", "tarjeta_banco": "Banco de la tarjeta", "tarjeta_numero": "Número",
    "tarjeta_ultimos": "Terminada en", "tarjeta_vencimiento": "Vencimiento",
}

# Los 3 primeros dígitos del CBU dicen el banco. Estos son los más comunes; si no está, usamos lo que leyó la IA
BANCOS_CBU = {
    "007": "Banco Galicia", "011": "Banco Nación", "014": "Banco Provincia", "015": "ICBC",
    "017": "BBVA", "020": "Bancor (Córdoba)", "027": "Banco Supervielle", "029": "Banco Ciudad",
    "034": "Banco Patagonia", "044": "Banco Hipotecario", "072": "Banco Santander", "143": "Brubank",
    "150": "HSBC / Galicia Más", "191": "Banco Credicoop", "285": "Banco Macro", "299": "Banco Comafi",
    "322": "Banco Industrial", "330": "Banco de Santa Fe", "386": "Banco de Entre Ríos",
}

# Los CVU (billeteras virtuales) empiezan con 000 y la billetera sale de los 7 primeros dígitos
BILLETERAS_CVU = {"0000003": "Mercado Pago"}

# Cómo nombra la gente a los bancos cuando lo escribe ("banco frances") y el nombre que usamos nosotros
ALIAS_BANCOS = {
    "frances": "BBVA", "bbva": "BBVA", "galicia mas": "HSBC / Galicia Más", "hsbc": "HSBC / Galicia Más",
    "galicia": "Banco Galicia", "nacion": "Banco Nación", "bna": "Banco Nación", "provincia": "Banco Provincia",
    "bapro": "Banco Provincia", "santander": "Banco Santander", "rio": "Banco Santander", "macro": "Banco Macro",
    "ciudad": "Banco Ciudad", "credicoop": "Banco Credicoop", "patagonia": "Banco Patagonia",
    "hipotecario": "Banco Hipotecario", "supervielle": "Banco Supervielle", "icbc": "ICBC", "brubank": "Brubank",
    "comafi": "Banco Comafi", "bancor": "Bancor (Córdoba)", "cordoba": "Bancor (Córdoba)", "santa fe": "Banco de Santa Fe",
    "entre rios": "Banco de Entre Ríos", "industrial": "Banco Industrial", "bind": "Banco Industrial",
    "mercado pago": "Mercado Pago", "mercadopago": "Mercado Pago", "uala": "Ualá", "naranja": "Naranja X",
}

# Marcas que se escriben de varias formas, para comparar la cédula con la cotización
ALIAS_MARCAS = {"vw": "volkswagen", "gm": "chevrolet", "chevy": "chevrolet", "mb": "mercedes", "benz": "mercedes"}

# Claude acepta hasta 32 MB por pedido; si suben muchas fotos pesadas, las mandamos en tandas
MAX_BYTES_POR_PEDIDO = 15 * 1024 * 1024
EXTENSIONES_IMAGEN = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".heic": "image/heic"}

MAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Un número largo (16 a 26 dígitos, con espacios o guiones en el medio) lo tomamos como CBU o CVU.
# El CBU tiene 22: si tiene otra cantidad lo tomamos igual y avisamos, porque seguro se cortó o le sobra algo
CBU = re.compile(r"(?<!\d)(?:\d[\s-]?){15,25}\d(?!\d)")
# El encabezado que queda al copiar de WhatsApp: "[10:23 p. m., 3/10/2026] Federico Palli: "
ENCABEZADO_WHATSAPP = re.compile(r"^\s*\[[^\]]*\]\s*[^:\n]{1,60}:\s*", re.MULTILINE)
# Un teléfono: arranca con + o dígito y tiene entre 10 y 13 dígitos (con espacios, guiones o paréntesis en el medio)
TELEFONO = re.compile(r"\+?\(?\d[\d\s()-]{8,20}\d")


def tipo_imagen(adjunto) -> str | None:
    # Devuelve el mime si el adjunto es una imagen, si no None
    if adjunto.content_type and adjunto.content_type.startswith("image/"):
        return adjunto.content_type.split(";")[0]
    extension = "." + adjunto.filename.rsplit(".", 1)[-1].lower() if "." in adjunto.filename else ""
    return EXTENSIONES_IMAGEN.get(extension)


def limpiar_whatsapp(texto: str) -> str:
    # Saca "[hora, fecha] Nombre: " de cada renglón, así no se confunden con datos
    return ENCABEZADO_WHATSAPP.sub("", texto or "")


def telefono_del_encabezado(texto: str) -> str | None:
    # Si el contacto no estaba agendado, WhatsApp pone el número en vez del nombre: "[10:23 p. m., 3/10/2026] +54 9 11 2345-6789: hola"
    for encabezado in ENCABEZADO_WHATSAPP.findall(texto or ""):
        quien = encabezado.split("]", 1)[-1]
        if telefono := buscar_telefono(quien):
            return telefono
    return None


def palabras(texto: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", normalizar(texto or ""))


def banco_por_nombre(texto: str) -> str | None:
    # "Banco frances" pasa a ser "BBVA". Primero probamos los alias más largos, así "galicia mas" le gana a "galicia"
    plano = " " + " ".join(palabras(texto)) + " "
    for alias in sorted(ALIAS_BANCOS, key=len, reverse=True):
        if f" {alias} " in plano:
            return ALIAS_BANCOS[alias]
    return None


def buscar_banco(texto: str) -> str | None:
    # Solo buscamos bancos en renglones que hablan de banco o CBU (si no, "Nación Seguros" sería el Banco Nación)
    for renglon in (texto or "").splitlines():
        if re.search(r"\b(banco|bco|cbu|cvu|cuenta)\b", normalizar(renglon)):
            if banco := banco_por_nombre(renglon):
                return banco
    return None


def buscar_mail(texto: str) -> str | None:
    encontrado = MAIL.search(texto or "")
    return encontrado.group(0).lower() if encontrado else None


def buscar_cbu(texto: str) -> str | None:
    encontrado = CBU.search(texto or "")
    return re.sub(r"\D", "", encontrado.group(0)) if encontrado else None


def buscar_n_solicitud(texto: str) -> str | None:
    # "solicitud 182312412", "N° de solicitud: 182312412", "solicitud nro 18231-2412"...
    encontrado = re.search(r"solicitud\D{0,12}(\d[\d-]{3,})", texto or "", re.IGNORECASE)
    return encontrado.group(1).strip("-") if encontrado else None


def buscar_telefono(texto: str) -> str | None:
    # Un DNI (8 dígitos) o un CBU (22) no son teléfonos: pedimos entre 10 y 13 dígitos
    for encontrado in TELEFONO.finditer(texto or ""):
        digitos = re.sub(r"\D", "", encontrado.group(0))
        if 10 <= len(digitos) <= 13:
            return encontrado.group(0).strip()
    return None


def cbu_valido(cbu: str) -> bool:
    # El CBU tiene dos dígitos verificadores (uno por bloque). Si no cierran, la IA leyó mal algún número
    if not re.fullmatch(r"\d{22}", cbu):
        return False
    bloque1, bloque2 = cbu[:8], cbu[8:]
    suma1 = sum(int(d) * p for d, p in zip(bloque1[:7], [7, 1, 4, 3, 9, 7, 1]))
    suma2 = sum(int(d) * p for d, p in zip(bloque2[:13], [3, 9, 7, 1, 3, 9, 7, 1, 3, 9, 7, 1, 3]))
    return (10 - suma1 % 10) % 10 == int(bloque1[7]) and (10 - suma2 % 10) % 10 == int(bloque2[13])


async def leer(imagenes: list[tuple[bytes, str]]) -> list[dict]:
    # imagenes: lista de (bytes, mime). Devuelve un documento por imagen: {"tipo": ..., "datos": {...}}
    tandas, tanda, peso = [], [], 0
    for datos, tipo in imagenes:
        if tanda and peso + len(datos) > MAX_BYTES_POR_PEDIDO:
            tandas.append(tanda)
            tanda, peso = [], 0
        tanda.append((datos, tipo))
        peso += len(datos)
    if tanda:
        tandas.append(tanda)

    leidos = []
    for tanda in tandas:
        respuesta = await ia.generar_json([*(ia.imagen(d, t) for d, t in tanda), ia.leer_prompt("documentos")], tarea="documentos")
        # A veces la IA devuelve la lista directo en vez de {"documentos": [...]}: aceptamos las dos
        lista = respuesta.get("documentos", []) if isinstance(respuesta, dict) else respuesta
        for doc in lista if isinstance(lista, list) else []:
            if not isinstance(doc, dict):
                continue
            tipo = doc.get("tipo", "otro")
            crudos = doc.get("datos") or {}
            datos = {c: str(crudos[c]).strip() for c in CLAVES.get(tipo, []) if crudos.get(c)}
            leidos.append({"tipo": tipo, "datos": datos})
    return leidos


def banco_del_cbu(cbu: str) -> str | None:
    # El banco según los primeros dígitos, si lo conocemos
    if cbu[:7] in BILLETERAS_CVU:
        return f"{BILLETERAS_CVU[cbu[:7]]} (CVU)"
    return BANCOS_CBU.get(cbu[:3])


def revisar_cbu(cbu: str, banco_dicho: str | None = None) -> tuple[dict, list[str]]:
    # Del CBU sacamos el banco y chequeamos que los números cierren. banco_dicho es el que vino escrito
    # o leído de la imagen. Devuelve (datos, avisos)
    cbu = re.sub(r"\D", "", cbu)
    avisos = []
    banco = banco_del_cbu(cbu) if len(cbu) == 22 else None

    if banco_dicho and banco and banco_por_nombre(banco_dicho) not in (None, banco) and banco_dicho != banco:
        avisos.append(f"⚠️ Por el número, el CBU es de **{banco}**, pero me dijeron **{banco_dicho}**. Revisá cuál está bien.")
    if not banco and banco_dicho:
        banco = banco_por_nombre(banco_dicho) or banco_dicho
    if not banco:
        banco = f"No identificado (código {cbu[:7] if cbu.startswith('000') else cbu[:3]})"

    if len(cbu) != 22:
        avisos.append(f"⚠️ El CBU `{cbu}` tiene {len(cbu)} dígitos y un CBU tiene 22. Fijate si se cortó o le sobra algo (lo corregís con ✍️).")
    elif not cbu_valido(cbu):
        avisos.append(f"⚠️ El CBU `{cbu}` no me cierra (los dígitos de control no coinciden). Revisalo, puede tener un número mal.")
    if cbu.startswith("000"):
        avisos.append("⚠️ Es un **CVU** (billetera virtual, tipo Mercado Pago). Ojo que ATM no los acepta.")
    return {"cbu": cbu, "banco": banco}, avisos


def comparar_vehiculo(vehiculo_cotizacion: str | None, anio_cotizacion: str | None, datos: dict) -> str | None:
    # ¿La cédula es del mismo vehículo que se cotizó? Comparamos marca, modelo y año de forma flexible
    # ("VW" = Volkswagen, "GOL TREND 1.6" coincide con "Gol"). Devuelve un aviso si no coincide, o None
    marca = [ALIAS_MARCAS.get(p, p) for p in palabras(datos.get("marca", ""))]
    modelo = [p for p in palabras(datos.get("modelo", "")) if len(p) >= 2]
    cotizado = {ALIAS_MARCAS.get(p, p) for p in palabras(vehiculo_cotizacion or "")}
    if not cotizado or not (marca or modelo):
        return None

    problemas = []
    if marca and not set(marca) & cotizado:
        problemas.append("marca")
    elif modelo and not set(modelo) & (cotizado - set(marca)):
        problemas.append("modelo")
    anio_cedula = re.sub(r"\D", "", datos.get("anio", ""))
    if anio_cedula and anio_cotizacion and anio_cedula != re.sub(r"\D", "", str(anio_cotizacion)):
        problemas.append("año")
    if not problemas:
        return None

    cedula = " ".join(filter(None, [datos.get("marca"), datos.get("modelo"), datos.get("anio")]))
    con_articulo = [{"marca": "la marca", "modelo": "el modelo", "año": "el año"}[p] for p in problemas]
    verbo = "coinciden" if len(problemas) > 1 else "coincide"
    return (f"⚠️ Ojo: no {verbo} {enumerar_simple(con_articulo)}. La cédula dice **{cedula}** y la cotización era "
            f"**{vehiculo_cotizacion} {anio_cotizacion or ''}**. ¿Es el mismo vehículo?")


def enumerar_simple(textos: list[str]) -> str:
    # Con ["marca", "año"] arma "marca y año"
    return textos[0] if len(textos) == 1 else f"{', '.join(textos[:-1])} y {textos[-1]}"


def datos_para_guardar(doc: dict) -> tuple[dict, list[str]]:
    # Lo que queda guardado de cada documento, y los avisos si hay algo raro. Devuelve (datos, avisos)
    datos, avisos = dict(doc["datos"]), []
    if doc["tipo"] == "cbu" and datos.get("cbu"):
        revisado, avisos = revisar_cbu(datos["cbu"], datos.get("banco", ""))
        datos.update(revisado)
    # El número de tarjeta lo usás para cargar, pero en la base guardamos solo los últimos 4
    if numero := re.sub(r"\D", "", datos.pop("tarjeta_numero", "")):
        datos["tarjeta_ultimos"] = numero[-4:]
    return datos, avisos
