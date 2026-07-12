"""Gestión de riesgo: cuánto comprar en cada operación."""

from .contracts import margen, spec


def position_size(equity: float, price: float, stop_loss: float, risk_cfg: dict,
                  symbol: str | None = None, abiertas: dict | None = None) -> float:
    """Cantidad a comprar arriesgando `risk_per_trade` del equity hasta el stop.

    Se limita además a `max_position_pct` del equity para no concentrar todo
    el capital en una sola operación.

    Si se pasa `symbol`, se aplican las dos restricciones que impone un broker
    real y que antes faltaban:

      1. La cantidad se redondea HACIA ABAJO al lote mínimo. Si no alcanza ni
         para un lote, devuelve 0: esa operación NO se puede tomar. (Con oro,
         el mínimo son 4.000 USD de nocional — no existe "media onza".)
      2. El margen de la nueva posición, sumado al de las ya abiertas, no puede
         superar el equity.

    Sin esto el bot calcula tamaños que ningún broker acepta, y el paper trading
    mide una estrategia que no es la que podrías ejecutar.
    """
    risk_amount = equity * risk_cfg["risk_per_trade"]
    stop_distance = price - stop_loss
    if stop_distance <= 0:
        return 0.0

    s = spec(symbol) if symbol else None
    if s and s["usd_base"]:
        # El stop está en la moneda cotizada; pasarlo a USD para dimensionar.
        qty = risk_amount * price / stop_distance
        max_qty = equity * risk_cfg["max_position_pct"]
    else:
        qty = risk_amount / stop_distance
        max_qty = equity * risk_cfg["max_position_pct"] / price
    qty = min(qty, max_qty)

    if symbol is None:
        return qty

    lotes = int(qty / s["unidades"])
    if lotes < 1:
        return 0.0
    qty = lotes * s["unidades"]

    usado = sum(
        margen(sym, p["qty"], p["entry"]) for sym, p in (abiertas or {}).items()
    )
    if usado + margen(symbol, qty, price) > equity:
        return 0.0

    return qty
