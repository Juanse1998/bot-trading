"""Comparacion justa: el bot contra el S&P 500, con los mismos aportes.

La comparacion honesta exige simetria. Antes compare la mediana Monte Carlo del
bot contra un S&P creciendo liso al 10% anual — eso favorece al indice, porque
le saca la volatilidad y con ella el lastre que si le cobre al bot.

Aca las dos se simulan igual:
  - retornos REALES remuestreados (bootstrap por bloques, para conservar la
    autocorrelacion: los meses malos vienen en racha, no sueltos)
  - los mismos aportes mensuales
  - las mismas metricas de riesgo

El S&P sale de su historia real (1990-2026), que incluye 2000, 2008 y 2020.
"""

import argparse

import numpy as np
import pandas as pd
import yaml
import yfinance as yf

from bot.data import fetch_yahoo
from bot.realistic_backtest import run


def sp500_mensual() -> np.ndarray:
    d = yf.Ticker("^GSPC").history(period="max", interval="1mo")
    r = d["Close"].pct_change().dropna()
    return r[r.index >= "1990-01-01"].values


def bot_mensual(cfg_path: str, capital: float) -> tuple[np.ndarray, float]:
    cfg = yaml.safe_load(open(cfg_path))
    dfs = {}
    for s in cfg["symbols"]:
        try:
            d = fetch_yahoo(s, cfg["timeframe"], days=729)
            if len(d) > cfg["strategy"]["ema_trend"] + 50:
                dfs[s] = d
        except Exception:
            pass
    cfg["risk"]["initial_equity"] = capital
    r = run(cfg, dfs, realista=True)
    m = r["curva"].resample("ME").last().pct_change().dropna()
    anual = (r["equity_final"] / capital) ** (1 / 2.92) - 1
    return m.values, anual


def simular(mensuales: np.ndarray, inicial: float, aporte: float, meses: int,
            n_paths: int, haircut: float, rng, bloque: int = 6) -> dict:
    """Bootstrap por bloques: preserva las rachas (buenas y malas)."""
    media = mensuales.mean()
    ajust = mensuales - media * (1 - haircut)

    n_bloques = meses // bloque + 1
    starts = rng.integers(0, len(ajust) - bloque, size=(n_paths, n_bloques))
    idx = (starts[:, :, None] + np.arange(bloque)[None, None, :]).reshape(n_paths, -1)[:, :meses]
    rets = ajust[idx]

    eq = np.full(n_paths, float(inicial))
    pico = eq.copy()
    dd = np.zeros(n_paths)
    for i in range(meses):
        eq = eq * (1 + rets[:, i])
        eq = np.maximum(eq, 0)
        if i < meses - 1:
            eq += aporte
        pico = np.maximum(pico, eq)
        dd = np.minimum(dd, eq / np.maximum(pico, 1e-9) - 1)
    return {"final": eq, "dd": dd}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config_final.yaml")
    p.add_argument("--capital", type=float, default=2000)
    p.add_argument("--aporte", type=float, default=100)
    p.add_argument("--años", type=int, default=10)
    p.add_argument("--paths", type=int, default=30000)
    args = p.parse_args()

    meses = args.años * 12
    aportado = args.capital + args.aporte * (meses - 1)
    rng = np.random.default_rng(0)

    sp = sp500_mensual()
    bot, anual_bot = bot_mensual(args.config, args.capital)

    print(f"\nS&P 500: {len(sp)} meses reales (1990-2026, incluye 2000/2008/2020)")
    print(f"   retorno medio {sp.mean() * 12 * 100:.1f}%/año   volatilidad mensual {sp.std() * 100:.1f}%")
    print(f"Bot:     {len(bot)} meses de backtest")
    print(f"   retorno {anual_bot * 100:.1f}%/año   volatilidad mensual {bot.std() * 100:.1f}%\n")

    print("=" * 94)
    print(f"  {args.capital:,.0f} USD + {args.aporte:,.0f}/mes durante {args.años} años  "
          f"(de tu bolsillo salen {aportado:,.0f} USD)")
    print("=" * 94)
    print(f"{'':<34}{'malo (p10)':>13}{'TIPICO':>13}{'bueno (p90)':>13}"
          f"{'peor caida':>13}{'prob. perder':>14}")
    print("-" * 94)

    casos = [
        ("S&P 500 (comprar y esperar)", sp, 1.00),
        ("Bot — edge completo", bot, 1.00),
        ("Bot — edge a la mitad", bot, 0.50),
        ("Bot — edge a un cuarto", bot, 0.25),
    ]
    for nombre, serie, hc in casos:
        s = simular(serie, args.capital, args.aporte, meses, args.paths, hc, rng)
        f = s["final"]
        print(f"{nombre:<34}{np.percentile(f, 10):>12,.0f}${np.median(f):>12,.0f}$"
              f"{np.percentile(f, 90):>12,.0f}${np.median(s['dd']) * 100:>12.0f}%"
              f"{(f < aportado).mean() * 100:>13.0f}%")
    print("-" * 94)
    print("\n  'peor caida' = drawdown maximo tipico en el camino.")
    print("  'prob. perder' = terminar con menos plata de la que aportaste.")


if __name__ == "__main__":
    main()
