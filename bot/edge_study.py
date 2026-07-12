"""Mide el edge bruto de señales de scalping en oro, en dolares por onza.

No es un backtest: no hay stops, targets ni gestion de posicion. La pregunta es
mas basica y decide todo lo demas: dada una señal, ¿cuanto se mueve el precio a
favor en promedio, y ese movimiento supera al spread?

Si el edge bruto no le gana al costo, ninguna gestion de riesgo lo arregla.
"""

import argparse

import numpy as np
import pandas as pd
import yfinance as yf

from bot.indicators import atr, ema, rsi

# Spread tipico en XAUUSD por onza (ida y vuelta cuesta el spread una vez).
SPREADS = {"ECN bueno": 0.15, "retail tipico": 0.30}

HORIZONS = [1, 3, 5, 10, 20]


def load(interval: str, period: str) -> pd.DataFrame:
    d = yf.download("GC=F", period=period, interval=interval, progress=False, auto_adjust=False)
    d.columns = [c[0].lower() for c in d.columns]
    return d.dropna()


def signals(d: pd.DataFrame) -> dict[str, pd.Series]:
    """Señales long-only, todas evaluadas al cierre de la vela."""
    close, high, low = d["close"], d["high"], d["low"]
    r = rsi(close, 14)
    e_fast, e_slow, e_trend = ema(close, 20), ema(close, 50), ema(close, 200)
    a = atr(d, 14)

    mean = close.rolling(20).mean()
    std = close.rolling(20).std()
    z = (close - mean) / std

    return {
        "meanrev: RSI<30": r < 30,
        "meanrev: RSI<25": r < 25,
        "meanrev: z-score < -2": z < -2,
        "meanrev: z<-2 + sobre EMA200": (z < -2) & (close > e_trend),
        "momentum: quiebre max 20 velas": close > high.rolling(20).max().shift(1),
        "momentum: cruce EMA20/50": (e_fast > e_slow) & (e_fast.shift(1) <= e_slow.shift(1)),
        "momentum: 3 velas verdes": (close > d["open"]) & (close.shift(1) > d["open"].shift(1)) & (close.shift(2) > d["open"].shift(2)),
        "volatilidad: vela > 1.5 ATR abajo": (close - d["open"]) < -1.5 * a,
    }


def study(d: pd.DataFrame, label: str) -> None:
    close = d["close"]
    sigs = signals(d)

    # Movimiento futuro a favor de un largo, en dolares por onza.
    fwd = {h: close.shift(-h) - close for h in HORIZONS}

    print(f"\n{'=' * 96}")
    print(f"  {label}   ({len(d)} velas)")
    print(f"{'=' * 96}")
    print(f"{'señal':<34}{'n':>6}", end="")
    for h in HORIZONS:
        print(f"{f'+{h}v':>11}", end="")
    print()
    print("-" * 96)

    for name, sig in sigs.items():
        n = int(sig.sum())
        if n < 20:
            print(f"{name:<34}{n:>6}   (muestra insuficiente)")
            continue
        print(f"{name:<34}{n:>6}", end="")
        for h in HORIZONS:
            edge = fwd[h][sig].mean()
            print(f"{edge:>+11.3f}", end="")
        print()

    print("-" * 96)
    print(f"{'COSTO A SUPERAR (spread)':<34}{'':>6}", end="")
    for _ in HORIZONS:
        print(f"{-SPREADS['retail tipico']:>11.3f}", end="")
    print("   <- retail tipico")
    print(f"{'':<34}{'':>6}", end="")
    for _ in HORIZONS:
        print(f"{-SPREADS['ECN bueno']:>11.3f}", end="")
    print("   <- ECN bueno")

    # Baseline: el drift del propio activo. Si el oro subio en la muestra,
    # cualquier señal long-only hereda ese drift sin tener edge real.
    print()
    print("Baseline (todas las velas, sin señal):")
    print(f"{'  drift del oro':<34}{len(d):>6}", end="")
    for h in HORIZONS:
        print(f"{fwd[h].mean():>+11.3f}", end="")
    print()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--interval", default="5m")
    p.add_argument("--period", default="60d")
    args = p.parse_args()

    d = load(args.interval, args.period)
    study(d, f"ORO (GC=F) — velas de {args.interval}, {args.period}")


if __name__ == "__main__":
    main()
