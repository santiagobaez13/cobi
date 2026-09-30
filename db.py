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
