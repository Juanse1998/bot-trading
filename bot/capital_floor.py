"""¿Cuanto capital hace falta REALMENTE para ejecutar la estrategia de 1h?

El backtest compra cantidades fraccionarias (0.0037 onzas de oro) y no modela
el margen. Un broker real impone dos cosas que el backtest ignora y que, con
cuentas chicas, mandan sobre todo lo demas:

  1. LOTE MINIMO: no podes comprar menos de 0.01 lote. En oro eso es 1 onza
     entera (~$4.100 de nocional). No hay "media onza".
  2. APALANCAMIENTO MAXIMO: el margen que te exigen por esa posicion minima.

Si el lote minimo te obliga a arriesgar mas que tu `risk_per_trade`, la gestion
de riesgo deja de existir: cada operacion es una apuesta al azar sobre tu cuenta.
Este script calcula, para cada instrumento, el capital minimo que hace que la
posicion mas chica posible siga respetando el riesgo configurado.
"""

import datetime as dt
from pathlib import Path

import pandas as pd

from bot.dukascopy import _parse_hour
from bot.indicators import atr

CACHE = Path("/private/tmp/claude-501/-Users-juanse-Desktop-Personal-Proyectos-bot-trading/4e7b4d7e-309e-409e-8470-c25ef6eaaae6/scratchpad/ticks")

# Lote minimo (0.01 lote) en unidades del activo, y apalancamiento retail tipico.
#
# `usd_base`: si el dolar es la moneda BASE del par (USD/JPY), entonces 1.000
# unidades valen 1.000 USD y el stop viene expresado en la moneda cotizada (yenes),
# asi que hay que dividirlo por el precio para pasarlo a dolares. En los pares
# XXX/USD (EUR/USD, oro) es al reves: el nocional se obtiene multiplicando por el
# precio y el stop ya esta en dolares.
CONTRATO = {
    "XAUUSD": {"unidades": 1.0,    "nombre": "oro (1 onza)",        "apalanc": 20, "usd_base": False},
    "EURUSD": {"unidades": 1000.0, "nombre": "EUR/USD (1.000 EUR)", "apalanc": 30, "usd_base": False},
    "GBPUSD": {"unidades": 1000.0, "nombre": "GBP/USD (1.000 GBP)", "apalanc": 30, "usd_base": False},
    "USDJPY": {"unidades": 1000.0, "nombre": "USD/JPY (1.000 USD)", "apalanc": 30, "usd_base": True},
}

RIESGO_POR_TRADE = 0.02   # config_multi.yaml
ATR_STOP_MULT = 2.0       # config_multi.yaml


def cargar(sym: str) -> pd.DataFrame:
    frames = []
    for f in sorted((CACHE / sym).glob("2026-07-0[123]-*.bi5")):
        h = dt.datetime.strptime(f.stem, "%Y-%m-%d-%H")
        d = _parse_hour(f, sym, h)
        if len(d):
            frames.append(d)
    t = pd.concat(frames)
    t["mid"] = (t["bid"] + t["ask"]) / 2
    return t


def main() -> None:
    print()
    print("=" * 92)
    print("  CAPITAL MINIMO REAL POR INSTRUMENTO  (estrategia 1h, riesgo 2%, stop 2xATR)")
    print("=" * 92)
    print(f"{'instrumento':<24}{'nocional min':>14}{'stop 2xATR':>12}"
          f"{'riesgo del lote min':>21}{'capital minimo':>16}")
    print("-" * 92)

    filas = []
    for sym, c in CONTRATO.items():
        t = cargar(sym)
        bars = t.set_index("ts")["mid"].resample("1h").ohlc().dropna()
        a = atr(bars, 14).mean()
        precio = bars["close"].iloc[-1]

        stop_dist = ATR_STOP_MULT * a               # distancia al stop, en precio
        if c["usd_base"]:
            nocional = c["unidades"]                          # ya esta en USD
            riesgo_lote = c["unidades"] * stop_dist / precio  # el stop esta en la cotizada
        else:
            nocional = c["unidades"] * precio
            riesgo_lote = c["unidades"] * stop_dist
        # Para que ese riesgo sea el 2% del equity: equity = riesgo / 0.02
        cap_riesgo = riesgo_lote / RIESGO_POR_TRADE
        # Ademas hay que poder poner el margen de esa posicion.
        cap_margen = nocional / c["apalanc"]
        cap_min = max(cap_riesgo, cap_margen)

        filas.append((sym, c, nocional, stop_dist, riesgo_lote, cap_min, cap_margen, cap_riesgo))
        print(f"{c['nombre']:<24}{nocional:>13,.0f}${stop_dist:>12.4f}"
              f"{riesgo_lote:>20.2f}${cap_min:>15,.0f}$")

    print("-" * 92)
    print("\n  'riesgo del lote min' = cuanta plata perdes si salta el stop en la posicion")
    print("  MAS CHICA que el broker te deja abrir. Si eso supera el 2% de tu cuenta,")
    print("  ya no estas gestionando riesgo: estas apostando.\n")

    for equity in (50, 100, 500, 2000, 5000):
        print(f"  Con {equity:>5,} USD de capital:")
        for sym, c, nocional, stop_dist, riesgo_lote, cap_min, cap_margen, _ in filas:
            pct = riesgo_lote / equity
            margen_ok = equity >= cap_margen
            if not margen_ok:
                estado = f"IMPOSIBLE (el margen exige {cap_margen:,.0f}$)"
            elif pct <= RIESGO_POR_TRADE:
                estado = f"ok — arriesgas {pct * 100:.1f}% por trade"
            else:
                estado = f"TEMERARIO — arriesgas {pct * 100:.0f}% del capital por trade"
            print(f"      {c['nombre']:<24} {estado}")
        print()


if __name__ == "__main__":
    main()
