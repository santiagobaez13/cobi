import re
from datetime import date, timedelta

import db
from util import ahora, normalizar

# "cobi consumo": cuánto se gastó en la IA este mes y en qué. Cada pedido a Claude queda anotado en la tabla consumo (ver ia.py)
PREGUNTA = re.compile(r"\b(consumo|consumimos|tokens|cuanto (gastamos|gaste|gasto|llevamos gastado|se gasto))\b")
NOMBRES = {
    "cotizacion": "📄 Cotizaciones",
    "comparativa": "⚖️ Comparativas",
    "acepto": "✅ Aceptó (cobertura elegida)",
    "documentos": "🪪 Fotos de documentos",
    "poliza": "📑 PDFs de pólizas",
    "asistente": "❓ Asistente",
}


def es_pregunta(texto: str) -> bool:
    return PREGUNTA.search(normalizar(texto)) is not None


def plata(monto: float) -> str:
    # Son montos chiquitos: si no llega al centavo mostramos más decimales, si no todo diría "0,00"
    if monto == 0:
        return "US$ 0"
    return (f"US$ {monto:.4f}" if monto < 0.01 else f"US$ {monto:.2f}").replace(".", ",")


def total(filas) -> tuple[float, int]:
    return sum(f["costo"] or 0 for f in filas), sum(f["pedidos"] for f in filas)


def responder() -> str:
    hoy = ahora().date()
    inicio_mes = hoy.replace(day=1)
    inicio_mes_pasado = (inicio_mes - timedelta(days=1)).replace(day=1)

    este_mes = db.consumo_entre(inicio_mes.isoformat())
    gastado, pedidos = total(este_mes)
    gastado_hoy, _ = total(db.consumo_entre(hoy.isoformat()))
    gastado_antes, _ = total(db.consumo_entre(inicio_mes_pasado.isoformat(), inicio_mes.isoformat()))

    renglones = [f"💸 **Consumo de la IA** · este mes (desde el {inicio_mes:%d/%m}): **{plata(gastado)}** en {pedidos} pedidos"]
    for fila in este_mes:
        renglones.append(f"• {NOMBRES.get(fila['tarea'], fila['tarea'])}: {fila['pedidos']} · {plata(fila['costo'] or 0)}")
    renglones.append(f"\nHoy: {plata(gastado_hoy)} · Mes pasado: {plata(gastado_antes)}")
    # Cuánto daría el mes entero si seguimos a este ritmo
    if pedidos:
        dias = (hoy - inicio_mes).days + 1
        proximo_mes = date(hoy.year + (hoy.month == 12), hoy.month % 12 + 1, 1)
        renglones.append(f"A este ritmo, el mes cierra en unos **{plata(gastado / dias * (proximo_mes - inicio_mes).days)}**.")
    renglones.append("El saldo que queda lo ves en console.anthropic.com → Billing.")
    return "\n".join(renglones)
