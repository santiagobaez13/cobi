import json
import sqlite3
from contextlib import closing
from pathlib import Path

# Acá van datos de clientes: el .db está en el .gitignore y nunca se sube
RUTA_DB = Path(__file__).parent / "cobi.db"


def _conectar() -> sqlite3.Connection:
    con = sqlite3.connect(RUTA_DB)
    con.row_factory = sqlite3.Row
    return con


def crear_tablas():
    with closing(_conectar()) as con, con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS cotizaciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canal_id INTEGER NOT NULL,
                mensaje_pdf_id INTEGER NOT NULL,
                pdf_nombre TEXT NOT NULL,
                mensaje_cobi_id INTEGER,
                estado TEXT NOT NULL DEFAULT 'pendiente',
                cambiado_por TEXT,
                creada TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                actualizada TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS altas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cotizacion_id INTEGER NOT NULL REFERENCES cotizaciones(id),
                cliente TEXT NOT NULL,
                compania TEXT,
                vehiculo TEXT,
                anio TEXT,
                tipo TEXT,
                cobertura TEXT,
                hilo_id INTEGER,
                estado TEXT NOT NULL DEFAULT 'en_curso',
                creada TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                actualizada TEXT
            )
        """)
        # Columnas que se sumaron después: si la base es vieja, las agregamos sin tocar lo que ya hay
        _agregar_columnas(con, "altas", {
            "etapa": "INTEGER NOT NULL DEFAULT 1",
            "n_solicitud": "TEXT",
            "pendiente_desde": "TEXT",
            "emitida_en": "TEXT",
            "datos": "TEXT",
            "alerta_enviada": "TEXT",
            "gnc": "INTEGER NOT NULL DEFAULT 0",
            "omitidos": "TEXT",
            "panel_id": "INTEGER",
            "tarjeta_pendiente_id": "INTEGER",
        })


def _agregar_columnas(con: sqlite3.Connection, tabla: str, columnas: dict[str, str]):
    existentes = {fila["name"] for fila in con.execute(f"PRAGMA table_info({tabla})")}
    for nombre, tipo in columnas.items():
        if nombre not in existentes:
            con.execute(f"ALTER TABLE {tabla} ADD COLUMN {nombre} {tipo}")


def nueva_cotizacion(canal_id: int, mensaje_pdf_id: int, pdf_nombre: str) -> int:
    with closing(_conectar()) as con, con:
        cursor = con.execute(
            "INSERT INTO cotizaciones (canal_id, mensaje_pdf_id, pdf_nombre) VALUES (?, ?, ?)",
            (canal_id, mensaje_pdf_id, pdf_nombre),
        )
        return cursor.lastrowid


def guardar_mensaje_cobi(cotizacion_id: int, mensaje_cobi_id: int):
    with closing(_conectar()) as con, con:
        con.execute("UPDATE cotizaciones SET mensaje_cobi_id = ? WHERE id = ?", (mensaje_cobi_id, cotizacion_id))


def obtener_cotizacion(cotizacion_id: int) -> sqlite3.Row | None:
    with closing(_conectar()) as con:
        return con.execute("SELECT * FROM cotizaciones WHERE id = ?", (cotizacion_id,)).fetchone()


def cambiar_estado(cotizacion_id: int, estado: str, usuario: str):
    with closing(_conectar()) as con, con:
        con.execute(
            "UPDATE cotizaciones SET estado = ?, cambiado_por = ?, actualizada = datetime('now', 'localtime') WHERE id = ?",
            (estado, usuario, cotizacion_id),
        )


def tomar_cotizacion(cotizacion_id: int, usuario: str) -> bool:
    # La pasamos a "aceptando" solo si seguía pendiente. Si dos tocan Aceptó a la vez, gana uno solo
    with closing(_conectar()) as con, con:
        cursor = con.execute(
            "UPDATE cotizaciones SET estado = 'aceptando', cambiado_por = ?, actualizada = datetime('now', 'localtime') "
            "WHERE id = ? AND estado = 'pendiente'",
            (usuario, cotizacion_id),
        )
        return cursor.rowcount == 1


def nueva_alta(cotizacion_id: int, cliente: str, compania: str, vehiculo: str, anio: str, tipo: str, cobertura: str,
               hilo_id: int, gnc: bool = False, datos: dict | None = None) -> int:
    with closing(_conectar()) as con, con:
        cursor = con.execute(
            "INSERT INTO altas (cotizacion_id, cliente, compania, vehiculo, anio, tipo, cobertura, hilo_id, gnc, datos) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cotizacion_id, cliente, compania, vehiculo, anio, tipo, cobertura, hilo_id, int(gnc),
             json.dumps(datos or {}, ensure_ascii=False)),
        )
        return cursor.lastrowid


def obtener_alta(alta_id: int) -> sqlite3.Row | None:
    with closing(_conectar()) as con:
        return con.execute("SELECT * FROM altas WHERE id = ?", (alta_id,)).fetchone()


def alta_por_hilo(hilo_id: int) -> sqlite3.Row | None:
    with closing(_conectar()) as con:
        return con.execute("SELECT * FROM altas WHERE hilo_id = ?", (hilo_id,)).fetchone()


# Solo dejamos tocar estas columnas, así nadie mete cualquier cosa en el UPDATE
CAMPOS_ALTA = {"etapa", "estado", "n_solicitud", "pendiente_desde", "emitida_en", "alerta_enviada", "omitidos", "panel_id", "tarjeta_pendiente_id"}


def actualizar_alta(alta_id: int, **campos):
    if not campos or not set(campos) <= CAMPOS_ALTA:
        raise ValueError(f"Campos de alta inválidos: {set(campos) - CAMPOS_ALTA}")
    asignaciones = ", ".join(f"{campo} = ?" for campo in campos)
    with closing(_conectar()) as con, con:
        con.execute(
            f"UPDATE altas SET {asignaciones}, actualizada = datetime('now', 'localtime') WHERE id = ?",
            (*campos.values(), alta_id),
        )


def datos_alta(alta) -> dict:
    # Los datos leídos de los documentos van como JSON en la columna "datos"
    return json.loads(alta["datos"]) if alta["datos"] else {}


def omitidos_alta(alta) -> set[str]:
    # Los tildes que el productor decidió no cargar
    return set(json.loads(alta["omitidos"])) if alta["omitidos"] else set()


def guardar_omitidos(alta_id: int, omitidos: set[str]):
    actualizar_alta(alta_id, omitidos=json.dumps(sorted(omitidos)))


def sumar_datos_alta(alta_id: int, nuevos: dict) -> dict:
    # Lo nuevo pisa a lo viejo, pero un dato vacío nunca borra uno que ya teníamos
    with closing(_conectar()) as con, con:
        fila = con.execute("SELECT datos FROM altas WHERE id = ?", (alta_id,)).fetchone()
        datos = json.loads(fila["datos"]) if fila and fila["datos"] else {}
        datos.update({clave: valor for clave, valor in nuevos.items() if valor})
        con.execute(
            "UPDATE altas SET datos = ?, actualizada = datetime('now', 'localtime') WHERE id = ?",
            (json.dumps(datos, ensure_ascii=False), alta_id),
        )
        return datos


def altas_para_alertar(pendiente_antes_de: str) -> list[sqlite3.Row]:
    # Las que siguen pendientes de emisión desde antes de esa fecha y todavía no avisamos
    with closing(_conectar()) as con:
        return con.execute(
            "SELECT * FROM altas WHERE estado = 'pendiente_emision' AND alerta_enviada IS NULL AND pendiente_desde <= ?",
            (pendiente_antes_de,),
        ).fetchall()


def altas_pendientes() -> list[sqlite3.Row]:
    with closing(_conectar()) as con:
        return con.execute("SELECT * FROM altas WHERE estado = 'pendiente_emision' ORDER BY pendiente_desde").fetchall()


def altas_con_tarjeta_sobrante() -> list[sqlite3.Row]:
    # Las que tienen tarjeta en #pendientes pero ya no están pendientes (por si se emitieron con Cobi apagado o algo falló)
    with closing(_conectar()) as con:
        return con.execute("SELECT * FROM altas WHERE estado != 'pendiente_emision' AND tarjeta_pendiente_id IS NOT NULL").fetchall()


def todas_las_altas() -> list[sqlite3.Row]:
    # Para el buscador de #cobi. Son pocas, así que filtramos en Python
    with closing(_conectar()) as con:
        return con.execute("SELECT * FROM altas ORDER BY id DESC").fetchall()
