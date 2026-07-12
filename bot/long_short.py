"""Reversion a la media SIMETRICA: tambien opera en corto.

Las estrategias de strategy.py son solo-largos, herencia de cuando el proyecto
era de cripto spot. Pero en forex, CFDs de oro e indices vender en corto cuesta
lo mismo que comprar, asi que la mitad de las señales se estaba tirando a la
basura: compramos cuando el RSI marca sobreventa, pero cuando marca sobrecompra
no hacemos nada.

La logica es el espejo exacto:
    LARGO : precio sobre la EMA200 (tendencia alcista) + RSI en sobreventa
    CORTO : precio bajo la EMA200 (tendencia bajista)  + RSI en sobrecompra

El backtest de realistic_backtest.py asume largos (stop abajo, objetivo arriba),
asi que aca va una version que lleva la direccion de cada posicion.
"""

import argparse

import pandas as pd
import yaml

from bot.data import fetch_yahoo
from bot.indicators import atr, ema, rsi
from bot.realistic_backtest import CONTRATOS, _nocional


def add_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    df = df.copy()
    df["ema_trend"] = ema(df["close"], cfg["ema_trend"])
    df["rsi"] = rsi(df["close"], cfg["rsi_period"])
    df["atr"] = atr(df, cfg["atr_period"])
    return df


def entrada(row: pd.Series, cfg: dict, permitir_cortos: bool) -> dict | None:
    price, a, r = float(row["close"]), float(row["atr"]), float(row["rsi"])
    if a <= 0:
        return None

    if price > row["ema_trend"] and r < cfg["rsi_oversold"]:
        return {"dir": 1,
                "stop": price - cfg["atr_stop_mult"] * a,
                "target": price + cfg["atr_target_mult"] * a}

    if permitir_cortos and price < row["ema_trend"] and r > (100 - cfg["rsi_oversold"]):
        return {"dir": -1,
                "stop": price + cfg["atr_stop_mult"] * a,
                "target": price - cfg["atr_target_mult"] * a}
    return None


def salida(row: pd.Series, direccion: int, cfg: dict) -> bool:
    r = float(row["rsi"])
    if direccion == 1:
        return r > cfg["rsi_exit"]
    return r < (100 - cfg["rsi_exit"])


def run(cfg: dict, dfs: dict, permitir_cortos: bool) -> dict:
    scfg, rcfg = cfg["strategy"], cfg["risk"]
    fee = rcfg["fee_pct"]
    start = scfg["ema_trend"]

    dfs = {s: add_indicators(d, scfg) for s, d in dfs.items()}
    idx = {s: {ts: i for i, ts in enumerate(d.index)} for s, d in dfs.items()}
    union = sorted(set().union(*(d.index for d in dfs.values())))

    equity = float(rcfg["initial_equity"])
    pos: dict = {}
    trades: list = []
    curva: list = []

    for ts in union:
        for symbol, df in dfs.items():
            i = idx[symbol].get(ts)
            if i is None or i < start:
                continue
            row = df.iloc[i]
            price = float(row["close"])
            c = CONTRATOS[symbol]
            p = pos.get(symbol)

            if p is not None:
                d = p["dir"]
                exit_price = None
                # Con un corto, el stop esta ARRIBA y el objetivo ABAJO.
                if d == 1:
                    if float(row["low"]) <= p["stop"]:
                        exit_price = p["stop"]
                    elif float(row["high"]) >= p["target"]:
                        exit_price = p["target"]
                else:
                    if float(row["high"]) >= p["stop"]:
                        exit_price = p["stop"]
                    elif float(row["low"]) <= p["target"]:
                        exit_price = p["target"]
                if exit_price is None and salida(row, d, scfg):
                    exit_price = price

                if exit_price is not None:
                    bruto = p["qty"] * (exit_price - p["entry"]) * d
                    if c["usd_base"]:
                        bruto /= exit_price
                    bruto -= fee * (_nocional(c, p["qty"], p["entry"])
                                    + _nocional(c, p["qty"], exit_price))
                    equity += bruto
                    trades.append({"symbol": symbol, "pnl": bruto, "dir": d, "ts": ts})
                    del pos[symbol]
                continue

            e = entrada(row, scfg, permitir_cortos)
            if not e:
                continue

            stop_dist = abs(price - e["stop"])
            if stop_dist <= 0:
                continue
            riesgo = equity * rcfg["risk_per_trade"]
            qty = riesgo * price / stop_dist if c["usd_base"] else riesgo / stop_dist
            qty = min(qty, equity * rcfg["max_position_pct"] / (1 if c["usd_base"] else price))

            lotes = int(qty / c["unidades"])
            if lotes < 1:
                continue
            qty = lotes * c["unidades"]

            usado = sum(_nocional(CONTRATOS[s], q["qty"], q["entry"]) / CONTRATOS[s]["apalanc"]
                        for s, q in pos.items())
            if usado + _nocional(c, qty, price) / c["apalanc"] > equity:
                continue

            pos[symbol] = {"qty": qty, "entry": price, "dir": e["dir"],
                           "stop": e["stop"], "target": e["target"]}
        curva.append({"ts": ts, "equity": equity})

    cur = pd.DataFrame(curva).set_index("ts")["equity"]
    t = pd.DataFrame(trades)
    return {
        "equity_final": equity,
        "curva": cur,
        "max_dd": (cur / cur.cummax() - 1).min(),
        "n_trades": len(t),
        "win_rate": (t["pnl"] > 0).mean() if len(t) else 0,
        "trades": t,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--capital", type=float, default=2000)
    p.add_argument("--target", type=float, default=3.0)
    args = p.parse_args()

    base = yaml.safe_load(open("config_multi.yaml"))
    dfs = {}
    for s in base["symbols"]:
        try:
            d = fetch_yahoo(s, base["timeframe"], days=730)
            if len(d) > base["strategy"]["ema_trend"] + 50:
                dfs[s] = d
        except Exception:
            pass

    # Validacion honesta: elegir sobre el 70% mas viejo, medir sobre el 30% no visto.
    todas = sorted(set().union(*(d.index for d in dfs.values())))
    corte = todas[int(len(todas) * 0.70)]
    tr = {s: d[d.index <= corte] for s, d in dfs.items()}
    te = {s: d[d.index > corte] for s, d in dfs.items()}
    te = {s: d for s, d in te.items() if len(d) > base["strategy"]["ema_trend"] + 20}

    print(f"\nvalidacion fuera de muestra: desde {corte:%b %Y}\n")
    print(f"{'':<26}{'IN-SAMPLE':>14}{'OUT-OF-SAMPLE':>16}{'operaciones':>13}{'aciertos':>10}{'max DD':>9}")
    print("-" * 90)

    for cortos, etiqueta in [(False, "Solo largos (actual)"), (True, "Largos + CORTOS")]:
        fila = []
        for dd in (tr, te):
            cfg = yaml.safe_load(open("config_multi.yaml"))
            cfg["strategy"]["atr_target_mult"] = args.target
            cfg["risk"]["initial_equity"] = args.capital
            fila.append(run(cfg, dd, cortos))
        ins, outs = fila
        print(f"{etiqueta:<26}"
              f"{(ins['equity_final'] / args.capital - 1) * 100:>+13.1f}%"
              f"{(outs['equity_final'] / args.capital - 1) * 100:>+15.1f}%"
              f"{outs['n_trades']:>13,}{outs['win_rate'] * 100:>9.0f}%{outs['max_dd'] * 100:>8.0f}%")
    print("-" * 90)
    print(f"(objetivo {args.target}x ATR, capital {args.capital:,.0f} USD)")


if __name__ == "__main__":
    main()
