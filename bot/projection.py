"""Proyeccion a 10 años con aportes mensuales, via Monte Carlo.

Un solo numero ("2.000 se convierten en X") es una mentira comoda: asume que el
rendimiento del backtest se repite exacto cada año, cuando en realidad es UNA
muestra de 2.9 años. Este script hace lo contrario: toma las operaciones REALES
del backtest, las remuestrea al azar (bootstrap) miles de veces y devuelve el
abanico de futuros posibles.

Ademas modela dos cosas que un proyector de interes compuesto ignora:

  - APORTES MENSUALES: entran pase lo que pase, incluso en drawdown.
  - DECAIMIENTO DEL EDGE: ninguna estrategia rinde fuera de muestra lo mismo que
    en el backtest (parametros ajustados sobre esos mismos datos, regimen que
    cambia, competencia). Por eso se proyecta tambien con el edge recortado.
"""

import argparse

import numpy as np
import pandas as pd
import yaml

from bot.data import fetch_yahoo
from bot.realistic_backtest import run

TRADING_DAYS = 250


def pnl_pcts(cfg: dict, dfs: dict, capital: float) -> np.ndarray:
    """Resultado de cada operacion como fraccion del equity del momento."""
    cfg = {**cfg, "risk": {**cfg["risk"], "initial_equity": capital}}
    r = run(cfg, dfs, realista=True)
    t, cur = r["trades"], r["curva"]
    eq_en_trade = cur.reindex(t["ts"]).ffill().values
    return (t["pnl"].values / eq_en_trade), r


def simular(pcts: np.ndarray, inicial: float, aporte: float, años: int,
            trades_por_año: float, haircut: float, n_paths: int, rng) -> dict:
    """Bootstrap: remuestrea operaciones y aplica aportes mensuales."""
    n_trades = int(trades_por_año * años)
    # Recortar el edge: encoge cada resultado hacia 0 sin tocar la volatilidad.
    media = pcts.mean()
    ajustadas = pcts - media * (1 - haircut)

    # Momento (en fraccion del horizonte) de cada operacion y de cada aporte.
    meses = años * 12
    aporte_en_trade = n_trades / meses  # cuantas operaciones por mes

    equities = np.full(n_paths, float(inicial))
    picos = equities.copy()
    max_dd = np.zeros(n_paths)

    draws = rng.choice(ajustadas, size=(n_paths, n_trades), replace=True)
    for i in range(n_trades):
        equities *= 1 + draws[:, i]
        equities = np.maximum(equities, 0)  # cuenta liquidada
        # Aporte mensual prorrateado a la cadencia de operaciones.
        if i > 0 and int(i / aporte_en_trade) > int((i - 1) / aporte_en_trade):
            equities += aporte
        picos = np.maximum(picos, equities)
        max_dd = np.minimum(max_dd, equities / np.maximum(picos, 1e-9) - 1)

    aportado = inicial + aporte * (meses - 1)
    return {
        "final": equities,
        "aportado": aportado,
        "max_dd": max_dd,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config_multi.yaml")
    p.add_argument("--capital", type=float, default=2000)
    p.add_argument("--aporte", type=float, default=100)
    p.add_argument("--años", type=int, default=10)
    p.add_argument("--paths", type=int, default=20000)
    args = p.parse_args()

    cfg = yaml.safe_load(open(args.config))
    dfs = {}
    for s in cfg["symbols"]:
        try:
            d = fetch_yahoo(s, cfg["timeframe"], days=730)
            if len(d) > cfg["strategy"]["ema_trend"] + 50:
                dfs[s] = d
        except Exception:
            pass

    pcts, r = pnl_pcts(cfg, dfs, args.capital)
    años_bt = 2.92
    tpa = len(pcts) / años_bt

    print(f"\nBase: {len(pcts)} operaciones reales del backtest ({tpa:.0f}/año)")
    print(f"Resultado medio por operacion: {pcts.mean() * 100:+.3f}% del equity")
    print(f"Retorno anualizado del backtest: "
          f"{((r['equity_final'] / args.capital) ** (1 / años_bt) - 1) * 100:.1f}%\n")

    rng = np.random.default_rng(0)
    meses = args.años * 12
    aportado = args.capital + args.aporte * (meses - 1)

    print("=" * 88)
    print(f"  {args.capital:,.0f} USD iniciales + {args.aporte:,.0f} USD/mes durante {args.años} años")
    print(f"  Total aportado de tu bolsillo: {aportado:,.0f} USD")
    print("=" * 88)
    print(f"{'escenario':<34}{'malo (p10)':>14}{'tipico (p50)':>15}{'bueno (p90)':>14}{'prob. perder':>14}")
    print("-" * 88)

    anual_bt = ((r["equity_final"] / args.capital) ** (1 / años_bt) - 1) * 100
    escenarios = [
        (f"Edge completo del backtest ({anual_bt:.0f}%/año)", 1.00),
        ("Edge a la mitad (lo mas probable)", 0.50),
        ("Edge a un cuarto", 0.25),
        ("Sin edge (el bot no sirve)", 0.00),
    ]
    resultados = {}
    for nombre, hc in escenarios:
        s = simular(pcts, args.capital, args.aporte, args.años, tpa, hc, args.paths, rng)
        f = s["final"]
        p10, p50, p90 = np.percentile(f, [10, 50, 90])
        prob_perder = (f < aportado).mean()
        resultados[nombre] = s
        print(f"{nombre:<34}{p10:>13,.0f}${p50:>14,.0f}${p90:>13,.0f}${prob_perder * 100:>13.0f}%")

    print("-" * 88)
    # Referencia aburrida: un indice al 10% anual, sin drawdowns de -32%.
    r_idx = 0.10 / 12
    eq = args.capital
    for _ in range(meses - 1):
        eq = eq * (1 + r_idx) + args.aporte
    print(f"{'[referencia] indice S&P al 10%/año':<34}{'':>14}{eq:>14,.0f}${'':>14}{'':>14}")
    print("=" * 88)

    s = resultados[escenarios[0][0]]
    print(f"\n  Aun con el edge completo, en el camino vas a ver:")
    print(f"    drawdown maximo tipico: {np.median(s['max_dd']) * 100:.0f}%"
          f"   (en el 10% de los casos, peor que {np.percentile(s['max_dd'], 10) * 100:.0f}%)")
    print(f"    probabilidad de terminar con MENOS de lo que aportaste: "
          f"{(s['final'] < aportado).mean() * 100:.0f}%")


if __name__ == "__main__":
    main()
