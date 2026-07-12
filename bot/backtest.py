"""Backtester: valida la estrategia contra datos históricos.

Uso:
    python -m bot.backtest                       # config.yaml tal cual
    python -m bot.backtest --strategy meanrev    # probar otra estrategia
    python -m bot.backtest --symbol BTC/USDT --days 365
"""

import argparse

import pandas as pd
import yaml

from .data import fetch_ohlcv_history, fetch_yahoo, get_exchange
from .risk import position_size
from .strategy import add_indicators, get_strategy


def run_backtest(df: pd.DataFrame, cfg: dict) -> dict:
    scfg, rcfg = cfg["strategy"], cfg["risk"]
    strategy = get_strategy(scfg)
    df = add_indicators(df, scfg)
    fee = rcfg["fee_pct"]

    equity = float(rcfg["initial_equity"])
    position = None
    trades = []
    equity_curve = []

    # se empieza cuando la EMA de tendencia ya tiene datos suficientes
    start = scfg["ema_trend"]
    for i in range(start, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        price = float(row["close"])

        if position is not None:
            exit_price, reason = None, None
            # dentro de la vela, el stop y el target se evalúan con high/low
            if float(row["low"]) <= position["stop_loss"]:
                exit_price, reason = position["stop_loss"], "stop"
            elif float(row["high"]) >= position["take_profit"]:
                exit_price, reason = position["take_profit"], "target"
            else:
                strategy_reason = strategy.check_exit(prev, row, scfg)
                if strategy_reason:
                    exit_price, reason = price, strategy_reason

            if exit_price is not None:
                proceeds = position["qty"] * exit_price * (1 - fee)
                cost = position["qty"] * position["entry"] * (1 + fee)
                pnl = proceeds - cost
                equity += pnl
                trades.append({
                    "entry_time": position["time"],
                    "exit_time": row.name,
                    "entry": position["entry"],
                    "exit": exit_price,
                    "pnl": pnl,
                    "reason": reason,
                })
                position = None
        else:
            entry = strategy.check_entry(prev, row, scfg)
            if entry:
                qty = position_size(equity, price, entry["stop_loss"], rcfg)
                if qty > 0:
                    position = {
                        "time": row.name,
                        "entry": price,
                        "qty": qty,
                        "stop_loss": entry["stop_loss"],
                        "take_profit": entry["take_profit"],
                    }

        mark = equity
        if position is not None:
            mark += position["qty"] * (price - position["entry"])
        equity_curve.append(mark)

    return summarize(df, cfg, trades, equity_curve)


def run_portfolio_backtest(dfs: dict, cfg: dict) -> dict:
    """Backtest con capital COMPARTIDO: un solo equity opera todos los pares.

    Las ganancias de cualquier par agrandan las posiciones siguientes de todos
    (interés compuesto real de portafolio). Puede haber posiciones simultáneas
    en varios pares, por lo que la exposición total supera a la de un par solo.
    """
    scfg, rcfg = cfg["strategy"], cfg["risk"]
    strategy = get_strategy(scfg)
    fee = rcfg["fee_pct"]
    start = scfg["ema_trend"]

    dfs = {s: add_indicators(df, scfg) for s, df in dfs.items()}
    idx_maps = {s: {ts: i for i, ts in enumerate(df.index)} for s, df in dfs.items()}
    union = sorted(set().union(*(df.index for df in dfs.values())))

    equity = float(rcfg["initial_equity"])
    positions: dict = {}
    last_close: dict = {}
    trades: list = []
    curve: list = []
    peak_exposure = 0.0
    max_concurrent = 0

    for ts in union:
        for symbol, df in dfs.items():
            i = idx_maps[symbol].get(ts)
            if i is None or i < start:
                continue
            row = df.iloc[i]
            prev = df.iloc[i - 1]
            price = float(row["close"])
            last_close[symbol] = price
            position = positions.get(symbol)

            if position is not None:
                exit_price, reason = None, None
                if float(row["low"]) <= position["stop_loss"]:
                    exit_price, reason = position["stop_loss"], "stop"
                elif float(row["high"]) >= position["take_profit"]:
                    exit_price, reason = position["take_profit"], "target"
                else:
                    strategy_reason = strategy.check_exit(prev, row, scfg)
                    if strategy_reason:
                        exit_price, reason = price, strategy_reason
                if exit_price is not None:
                    proceeds = position["qty"] * exit_price * (1 - fee)
                    cost = position["qty"] * position["entry"] * (1 + fee)
                    pnl = proceeds - cost
                    equity += pnl
                    trades.append({"symbol": symbol, "pnl": pnl, "reason": reason,
                                   "exit_time": ts})
                    del positions[symbol]
            else:
                entry = strategy.check_entry(prev, row, scfg)
                if entry:
                    qty = position_size(equity, price, entry["stop_loss"], rcfg)
                    if qty > 0:
                        positions[symbol] = {
                            "entry": price,
                            "qty": qty,
                            "stop_loss": entry["stop_loss"],
                            "take_profit": entry["take_profit"],
                        }

        mark = equity + sum(p["qty"] * (last_close[s] - p["entry"])
                            for s, p in positions.items())
        curve.append(mark)
        if positions and mark > 0:
            exposure = sum(p["qty"] * last_close[s] for s, p in positions.items())
            peak_exposure = max(peak_exposure, exposure / mark)
        max_concurrent = max(max_concurrent, len(positions))

    initial = float(rcfg["initial_equity"])
    final = curve[-1] if curve else initial
    series = pd.Series(curve)
    drawdown = ((series - series.cummax()) / series.cummax()).min() if len(series) else 0.0
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    first, last = union[start], union[-1]

    return {
        "periodo": f"{first.date()} → {last.date()}",
        "operaciones": len(trades),
        "win_rate_%": round(100 * len(wins) / len(trades), 1) if trades else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else float("inf"),
        "retorno_%": round((final / initial - 1) * 100, 2),
        "max_drawdown_%": round(float(drawdown) * 100, 2),
        "equity_final": round(final, 2),
        "posiciones_simultaneas_max": max_concurrent,
        "exposicion_maxima_x_equity": round(peak_exposure, 1),
    }


def summarize(df: pd.DataFrame, cfg: dict, trades: list, equity_curve: list) -> dict:
    initial = float(cfg["risk"]["initial_equity"])
    final = equity_curve[-1] if equity_curve else initial
    curve = pd.Series(equity_curve)
    drawdown = ((curve - curve.cummax()) / curve.cummax()).min() if len(curve) else 0.0

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))

    start_idx = cfg["strategy"]["ema_trend"]
    buy_hold = (df["close"].iloc[-1] / df["close"].iloc[start_idx] - 1) * 100

    return {
        "periodo": f"{df.index[start_idx].date()} → {df.index[-1].date()}",
        "operaciones": len(trades),
        "ganadoras": len(wins),
        "perdedoras": len(losses),
        "win_rate_%": round(100 * len(wins) / len(trades), 1) if trades else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else float("inf"),
        "retorno_%": round((final / initial - 1) * 100, 2),
        "buy_hold_%": round(buy_hold, 2),
        "max_drawdown_%": round(float(drawdown) * 100, 2),
        "equity_final": round(final, 2),
        "trades": trades,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest de la estrategia")
    parser.add_argument("--symbol", help="par a testear; por defecto todos los del config")
    parser.add_argument("--strategy", help="estrategia a probar (trend | meanrev)")
    parser.add_argument("--portfolio", action="store_true",
                        help="capital compartido entre todos los pares (compounding real)")
    parser.add_argument("--days", type=int, default=365, help="días de histórico")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    if args.strategy:
        cfg["strategy"]["name"] = args.strategy

    exchange = get_exchange(cfg["exchange"])
    symbols = [args.symbol] if args.symbol else cfg["symbols"]

    def fetch(symbol):
        if cfg["exchange"] == "yahoo":
            return fetch_yahoo(symbol, cfg["timeframe"], args.days)
        return fetch_ohlcv_history(exchange, symbol, cfg["timeframe"], args.days)

    print(f"Estrategia: {cfg['strategy']['name']} | timeframe: {cfg['timeframe']} | {args.days} días")

    if args.portfolio:
        dfs = {symbol: fetch(symbol) for symbol in symbols}
        result = run_portfolio_backtest(dfs, cfg)
        print(f"\n=== PORTAFOLIO ({len(symbols)} pares, capital compartido) ===")
        for key, value in result.items():
            print(f"  {key}: {value}")
        return

    totals = {"operaciones": 0, "ganadoras": 0, "retorno_%": [], "drawdown_%": []}
    for symbol in symbols:
        if cfg["exchange"] == "yahoo":
            df = fetch_yahoo(symbol, cfg["timeframe"], args.days)
        else:
            df = fetch_ohlcv_history(exchange, symbol, cfg["timeframe"], args.days)
        result = run_backtest(df, cfg)
        print(f"\n=== {symbol} ===")
        for key, value in result.items():
            if key != "trades":
                print(f"  {key}: {value}")
        totals["operaciones"] += result["operaciones"]
        totals["ganadoras"] += result["ganadoras"]
        totals["retorno_%"].append(result["retorno_%"])
        totals["drawdown_%"].append(result["max_drawdown_%"])

    if len(symbols) > 1:
        avg_return = sum(totals["retorno_%"]) / len(totals["retorno_%"])
        win_rate = 100 * totals["ganadoras"] / totals["operaciones"] if totals["operaciones"] else 0
        print(f"\n=== TOTAL ({len(symbols)} pares) ===")
        print(f"  operaciones: {totals['operaciones']}")
        print(f"  win_rate_%: {win_rate:.1f}")
        print(f"  retorno_promedio_%: {avg_return:.2f}")
        print(f"  peor_drawdown_%: {min(totals['drawdown_%']):.2f}")


if __name__ == "__main__":
    main()
