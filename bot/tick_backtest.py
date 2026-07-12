"""Backtest de la señal de scalping en oro con bid/ask REALES tick a tick.

La diferencia con el estudio sobre velas de Yahoo es toda la cuestion: aca la
entrada paga el ASK y la salida cobra el BID, con el spread que habia de verdad
en ese instante. La señal dispara justo cuando el precio se desploma, que es
exactamente cuando el spread se abre — un promedio de spread esconde ese costo.

Ademas la entrada se ejecuta en el primer tick DESPUES del cierre de la vela que
genera la señal, no al cierre mismo (que en vivo es inalcanzable).
"""

import argparse
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

from bot.dukascopy import load_ticks, to_bars
from bot.indicators import atr

CACHE = Path(__file__).resolve().parent.parent / ".cache" / "ticks"


def signal_atr_drop(bars: pd.DataFrame, mult: float = 1.5) -> pd.Series:
    """Vela que cae mas de `mult` x ATR: comprar el desplome."""
    a = atr(bars.rename(columns={"open": "open", "high": "high", "low": "low", "close": "close"}), 14)
    return (bars["close"] - bars["open"]) < -mult * a


def run(ticks: pd.DataFrame, bars: pd.DataFrame, hold_bars: int, bar_minutes: int,
        assumed_spread: float) -> pd.DataFrame:
    """Simula cada señal de forma aislada (sin gestion de capital): cuanto gana
    o pierde una onza comprada tras la señal y vendida `hold_bars` velas despues."""
    sig = signal_atr_drop(bars)
    ts_index = ticks["ts"].values
    # pandas etiqueta cada vela con su hora de INICIO: la vela "10:00" de 2min
    # recien cierra a las 10:02. La señal no se conoce hasta ese momento, asi
    # que la entrada va despues del cierre, no despues de la etiqueta.
    bar_len = pd.Timedelta(minutes=bar_minutes)
    hold = pd.Timedelta(minutes=bar_minutes * hold_bars)

    trades = []
    for bar_ts in bars.index[sig]:
        close_ts = bar_ts + bar_len
        # Primer tick estrictamente posterior al cierre de la vela.
        i = np.searchsorted(ts_index, np.datetime64(close_ts), side="right")
        j = np.searchsorted(ts_index, np.datetime64(close_ts + hold), side="right")
        if i >= len(ticks) or j >= len(ticks) or j <= i:
            continue

        entry, exit_ = ticks.iloc[i], ticks.iloc[j]
        # Gap: si el siguiente tick esta a mas de 1 min, el mercado estaba cerrado.
        if (entry["ts"] - close_ts).total_seconds() > 60:
            continue

        trades.append({
            "ts": bar_ts,
            "entry_ask": entry["ask"],
            "entry_spread": entry["ask"] - entry["bid"],
            "exit_bid": exit_["bid"],
            # Real: compro al ask, vendo al bid.
            "pnl_real": exit_["bid"] - entry["ask"],
            # Lo que decia el estudio con velas: mid a mid menos un spread supuesto.
            "pnl_asumido": (exit_["mid"] - entry["mid"]) - assumed_spread,
            "pnl_bruto": exit_["mid"] - entry["mid"],
        })

    return pd.DataFrame(trades)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2026-06-03")
    p.add_argument("--end", default="2026-07-10")
    p.add_argument("--bar-minutes", type=int, default=2)
    p.add_argument("--hold-bars", type=int, default=5)
    p.add_argument("--assumed-spread", type=float, default=0.30)
    p.add_argument("--cache", default=str(CACHE))
    args = p.parse_args()

    ticks = load_ticks(
        "XAUUSD",
        dt.date.fromisoformat(args.start),
        dt.date.fromisoformat(args.end),
        Path(args.cache),
    )
    bars = to_bars(ticks, f"{args.bar_minutes}min")

    print(f"\n{len(ticks):,} ticks  ->  {len(bars):,} velas de {args.bar_minutes}min")
    print(f"periodo: {ticks.ts.min():%Y-%m-%d} a {ticks.ts.max():%Y-%m-%d}\n")

    print("=" * 78)
    print("  SPREAD REAL DEL ORO (XAUUSD, Dukascopy)")
    print("=" * 78)
    s = ticks["spread"]
    print(f"  mediana {s.median():.3f}   media {s.mean():.3f}   "
          f"p90 {s.quantile(.90):.3f}   p99 {s.quantile(.99):.3f}   max {s.max():.3f}")
    print(f"  (mi supuesto en el estudio con velas de Yahoo era {args.assumed_spread:.2f})\n")

    t = run(ticks, bars, args.hold_bars, args.bar_minutes, args.assumed_spread)
    if t.empty:
        print("sin operaciones")
        return

    print("=" * 78)
    print(f"  SEÑAL 'vela > 1.5 ATR abajo' — {len(t)} operaciones, salida a {args.hold_bars} velas")
    print("=" * 78)
    print(f"{'':32}{'$/onza prom':>14}{'total $':>12}{'% aciertos':>13}")
    print("-" * 78)
    for label, col in [
        ("Edge bruto (mid a mid)", "pnl_bruto"),
        (f"Con spread asumido ({args.assumed_spread:.2f})", "pnl_asumido"),
        ("Con bid/ask REAL", "pnl_real"),
    ]:
        v = t[col]
        print(f"{label:32}{v.mean():>+14.3f}{v.sum():>+12.2f}{(v > 0).mean() * 100:>12.1f}%")
    print("-" * 78)

    print("\n  Spread pagado EN EL MOMENTO de la señal (el punto de todo esto):")
    es = t["entry_spread"]
    print(f"    mediana {es.median():.3f}   media {es.mean():.3f}   p90 {es.quantile(.90):.3f}   max {es.max():.3f}")
    print(f"    vs spread mediano general: {s.median():.3f}   ->  "
          f"la señal paga {es.median() / s.median():.1f}x el spread normal")

    real = t["pnl_real"]
    print(f"\n  Veredicto: {real.mean():+.3f} $/onza por operacion tras costos reales.")
    if real.mean() > 0:
        n_day = len(t) / max((ticks.ts.max() - ticks.ts.min()).days, 1)
        print(f"  {n_day:.1f} operaciones/dia.  Con 1 onza (0.01 lote): "
              f"{real.mean() * n_day:+.2f} $/dia.")


if __name__ == "__main__":
    main()
