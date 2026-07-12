"""Ranking de instrumentos por eficiencia para scalping, con ticks reales.

La industria usa un umbral concreto: para que el scalping sea viable, el ATR de
la vela que operas tiene que superar al costo de entrada por al menos 4:1. Por
debajo de eso el spread se come el movimiento y no hay estrategia que lo salve.

    eficiencia = ATR(vela) / spread

El truco: ese cociente es INVARIANTE al divisor de puntos de cada instrumento
(numerador y denominador escalan igual), asi que se puede comparar oro contra
EURUSD contra el S&P sin saber la precision decimal de ninguno.
"""

import datetime as dt
from pathlib import Path

import pandas as pd

from bot.dukascopy import load_ticks
from bot.indicators import atr

CACHE = Path("/private/tmp/claude-501/-Users-juanse-Desktop-Personal-Proyectos-bot-trading/4e7b4d7e-309e-409e-8470-c25ef6eaaae6/scratchpad/ticks")

SYMBOLS = [
    ("EURUSD", "forex"), ("GBPUSD", "forex"), ("USDJPY", "forex"),
    ("GBPJPY", "forex cross"), ("AUDUSD", "forex"),
    ("XAUUSD", "oro"), ("XAGUSD", "plata"),
    ("USA500IDXUSD", "S&P 500"), ("BTCUSD", "bitcoin"), ("ETHUSD", "ethereum"),
]

UMBRAL = 4.0  # ratio minimo para que el scalping sea viable


def efficiency(symbol: str, start: dt.date, end: dt.date) -> dict | None:
    try:
        ticks = load_ticks(symbol, start, end, CACHE, workers=4)
    except Exception:
        return None
    if len(ticks) < 1000:
        return None

    out = {"symbol": symbol, "ticks": len(ticks)}
    for freq, label in [("1min", "1m"), ("5min", "5m"), ("1h", "1h")]:
        g = ticks.set_index("ts")
        bars = g["mid"].resample(freq).ohlc().dropna()
        if len(bars) < 30:
            continue
        spread = g["spread"].resample(freq).mean().reindex(bars.index).mean()
        a = atr(bars, 14).mean()
        out[f"atr_{label}"] = a
        out[f"eff_{label}"] = a / spread
    out["spread_pts"] = ticks["spread"].median()
    return out


def main() -> None:
    start, end = dt.date(2026, 7, 1), dt.date(2026, 7, 3)

    rows = []
    for sym, desc in SYMBOLS:
        r = efficiency(sym, start, end)
        if r:
            r["desc"] = desc
            rows.append(r)
            print(f"  ok {sym:<14} {r['ticks']:>9,} ticks")
        else:
            print(f"  -- {sym:<14} sin datos")

    df = pd.DataFrame(rows).sort_values("eff_1m", ascending=False)

    print(f"\n{'=' * 82}")
    print("  EFICIENCIA PARA SCALPING = ATR de la vela / spread")
    print(f"  Umbral de viabilidad de la industria: {UMBRAL:.0f}:1")
    print(f"{'=' * 82}")
    print(f"{'instrumento':<16}{'':<12}{'1 min':>10}{'5 min':>10}{'1 hora':>10}   veredicto a 1min")
    print("-" * 82)
    for _, r in df.iterrows():
        e1 = r.get("eff_1m", float("nan"))
        v = "VIABLE" if e1 >= UMBRAL else ("marginal" if e1 >= 2 else "imposible")
        print(f"{r['symbol']:<16}{r['desc']:<12}"
              f"{r.get('eff_1m', 0):>10.2f}{r.get('eff_5m', 0):>10.2f}{r.get('eff_1h', 0):>10.2f}"
              f"   {v}")
    print("-" * 82)
    print(f"{'umbral':<28}{UMBRAL:>10.2f}{UMBRAL:>10.2f}{UMBRAL:>10.2f}")
    print("\n  Leer asi: '3.5' significa que el movimiento tipico de la vela es 3.5x")
    print("  el spread. Necesitas al menos 4x para que quede algo despues del peaje.")


if __name__ == "__main__":
    main()
