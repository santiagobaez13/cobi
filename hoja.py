import json

import aiohttp

import config
import db
from util import ahora, clave_compania

# La hoja de Google de las pólizas emitidas. Cobi no entra directo a Google: le manda la fila a un script
# (Apps Script) pegado en la misma hoja, que la agrega o la saca. La URL y la clave del script van en el .env.
# Cada fila lleva una nota escondida "Cobi alta N" en la primera celda, así el script la encuentra para sacarla


def activa() -> bool:
    return bool(config.HOJA_EMITIDAS_URL and config.HOJA_EMITIDAS_CLAVE)


async def _mandar(pedido: dict) -> dict:
    # Google contesta con una redirección; aiohttp la sigue solo y ahí viene la respuesta del script
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as sesion:
        async with sesion.post(
            config.HOJA_EMITIDAS_URL,
            data=json.dumps({**pedido, "clave": config.HOJA_EMITIDAS_CLAVE}),
            headers={"Content-Type": "application/json"},
        ) as respuesta:
            texto = await respuesta.text()
    try:
        resultado = json.loads(texto)
    except json.JSONDecodeError:
        raise RuntimeError(f"La hoja no contestó bien (HTTP {respuesta.status}): {texto[:200]}")
    if not resultado.get("ok"):
        raise RuntimeError(f"La hoja dijo que no: {resultado.get('error')}")
    return resultado


def armar_fila(alta, productor: str) -> list[str]:
    # Mismas columnas que la hoja: Fecha alta · Nombre · Patente · Compañía · Productor · Comentarios
    compania = config.COMPANIAS_ALTA.get(clave_compania(alta["compania"]), {}).get("nombre_hoja", alta["compania"] or "")
    patente = (db.datos_alta(alta).get("patente") or "").upper()
    return [ahora().strftime("%d/%m/%Y"), alta["cliente"], patente, compania, productor, ""]


def estado(alta) -> dict:
    return json.loads(alta["hoja"]) if alta["hoja"] else {}


def anotar_para_cargar(alta_id: int, productor: str):
    # Queda "pendiente" en la base hasta que la hoja confirme. Si Google falla, el loop de cada hora lo reintenta
    alta = db.obtener_alta(alta_id)
    db.actualizar_alta(alta_id, hoja=json.dumps({"estado": "pendiente", "fila": armar_fila(alta, productor)}, ensure_ascii=False))


async def cargar(alta_id: int) -> bool:
    alta = db.obtener_alta(alta_id)
    datos = estado(alta) if alta else {}
    if datos.get("estado") != "pendiente":
        return True
    try:
        await _mandar({"accion": "agregar", "id": f"Cobi alta {alta_id}", "fila": datos["fila"]})
    except Exception as e:
        print(f"No pude cargar el alta {alta_id} en la hoja: {e}")
        return False
    db.actualizar_alta(alta_id, hoja=json.dumps({**datos, "estado": "cargada"}, ensure_ascii=False))
    return True


async def sacar(alta_id: int) -> bool:
    # Para "Volver atrás" o /limpiar: si ya estaba en la hoja, sacamos su fila. Si todavía no había llegado, ni la mandamos
    alta = db.obtener_alta(alta_id)
    if alta is None or not alta["hoja"]:
        return True
    if estado(alta).get("estado") == "cargada":
        try:
            await _mandar({"accion": "sacar", "id": f"Cobi alta {alta_id}"})
        except Exception as e:
            print(f"No pude sacar el alta {alta_id} de la hoja: {e}")
            return False
    db.actualizar_alta(alta_id, hoja=None)
    return True
