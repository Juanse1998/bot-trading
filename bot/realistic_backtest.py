"""Backtest de portafolio con las restricciones que impone un broker real.

El backtest original compra cantidades fraccionarias (0.0037 onzas de oro) y no
modela el margen. Eso hace que cualquier cuenta, por chica que sea, parezca
capaz de operar todo. En la realidad hay dos topes duros:

  1. LOTE MINIMO: la posicion se redondea HACIA ABAJO al lote minimo del
     instrumento. Si el riesgo permitido no alcanza ni para un lote minimo,
     la operacion NO SE PUEDE TOMAR y se descarta.
  2. MARGEN: la suma del margen de las posiciones abiertas no puede superar el
     equity. Si no entra, la operacion se descarta.

Con capital chico esas dos reglas descartan la mayoria de las señales, sobre
todo en oro, plata e indices. Ese descarte ES el resultado: no es un detalle
tecnico, es la diferencia entre un backtest y algo ejecutable.
"""

import argparse

import pandas as pd
import yaml

from bot.data import fetch_yahoo
from bot.strategy import add_indicators, get_strategy

# Especificaciones retail tipicas. `unidades` = tamaño de 0.01 lote (el minimo).
# `usd_base` = el dolar es la moneda base (el P&L sale en la moneda cotizada).
CONTRATOS = {
    "EURUSD=X": {"unidades": 1000, "apalanc": 30, "usd_base": False, "nombre": "EUR/USD"},
    "GBPUSD=X": {"unidades": 1000, "apalanc": 30, "usd_base": False, "nombre": "GBP/USD"},
    "AUDUSD=X": {"unidades": 1000, "apalanc": 30, "usd_base": False, "nombre": "AUD/USD"},
    "NZDUSD=X": {"unidades": 1000, "apalanc": 30, "usd_base": False, "nombre": "NZD/USD"},
    "USDJPY=X": {"unidades": 1000, "apalanc": 30, "usd_base": True,  "nombre": "USD/JPY"},
    "USDCAD=X": {"unidades": 1000, "apalanc": 30, "usd_base": True,  "nombre": "USD/CAD"},
    "GC=F":     {"unidades": 1,    "apalanc": 20, "usd_base": False, "nombre": "Oro"},
    "SI=F":     {"unidades": 50,   "apalanc": 10, "usd_base": False, "nombre": "Plata"},
    "^GSPC":    {"unidades": 0.1,  "apalanc": 20, "usd_base": False, "nombre": "S&P 500"},
    "^NDX":     {"unidades": 0.1,  "apalanc": 20, "usd_base": False, "nombre": "Nasdaq"},
}


def _nocional(c: dict, qty: float, price: float) -> float:
    return qty if c["usd_base"] else qty * price


def _pnl_usd(c: dict, qty: float, entry: float, exit_: float) -> float:
    if c["usd_base"]:
        return qty * (exit_ - entry) / exit_       # P&L viene en la moneda cotizada
    return qty * (exit_ - entry)


def _riesgo_usd(c: dict, qty: float, stop_dist: float, price: float) -> float:
    if c["usd_base"]:
        return qty * stop_dist / price
    return qty * stop_dist


def run(cfg: dict, dfs: dict, realista: bool) -> dict:
    scfg, rcfg = cfg["strategy"], cfg["risk"]
    strategy = get_strategy(scfg)
    fee = rcfg["fee_pct"]
    start = scfg["ema_trend"]

    dfs = {s: add_indicators(df, scfg) for s, df in dfs.items()}
    idx = {s: {ts: i for i, ts in enumerate(df.index)} for s, df in dfs.items()}
    union = sorted(set().union(*(df.index for df in dfs.values())))

    equity = float(rcfg["initial_equity"])
    pos: dict = {}
    trades: list = []
    curva: list = []
    descartes = {"lote_minimo": 0, "margen": 0}
    tomadas_por_symbol: dict = {}

    for ts in union:
        for symbol, df in dfs.items():
            i = idx[symbol].get(ts)
            if i is None or i < start:
                continue
            row, prev = df.iloc[i], df.iloc[i - 1]
            price = float(row["close"])
            c = CONTRATOS[symbol]
            p = pos.get(symbol)

            if p is not None:
                exit_price, reason = None, None
                if float(row["low"]) <= p["stop_loss"]:
                    exit_price, reason = p["stop_loss"], "stop"
                elif float(row["high"]) >= p["take_profit"]:
                    exit_price, reason = p["take_profit"], "target"
                elif strategy.check_exit(prev, row, scfg):
                    exit_price, reason = price, "salida por estrategia"

                if exit_price is not None:
                    pnl = _pnl_usd(c, p["qty"], p["entry"], exit_price)
                    # Comision sobre el nocional, en ambas puntas.
                    pnl -= fee * (_nocional(c, p["qty"], p["entry"])
                                  + _nocional(c, p["qty"], exit_price))
                    equity += pnl
                    trades.append({"symbol": symbol, "pnl": pnl, "reason": reason, "ts": ts})
                    del pos[symbol]
                continue

            entry = strategy.check_entry(prev, row, scfg)
            if not entry:
                continue

            stop_dist = price - entry["stop_loss"]
            if stop_dist <= 0:
                continue

            # Tamaño ideal segun riesgo, igual que el backtest original.
            riesgo = equity * rcfg["risk_per_trade"]
            qty = riesgo * price / stop_dist if c["usd_base"] else riesgo / stop_dist
            tope = equity * rcfg["max_position_pct"] / (1 if c["usd_base"] else price)
            qty = min(qty, tope)

            if realista:
                # 1. Redondear HACIA ABAJO al lote minimo.
                lotes = int(qty / c["unidades"])
                if lotes < 1:
                    descartes["lote_minimo"] += 1
                    continue
                qty = lotes * c["unidades"]

                # 2. ¿Entra el margen, contando lo ya comprometido?
                usado = sum(_nocional(CONTRATOS[s], q["qty"], q["entry"])
                            / CONTRATOS[s]["apalanc"] for s, q in pos.items())
                nuevo = _nocional(c, qty, price) / c["apalanc"]
                if usado + nuevo > equity:
                    descartes["margen"] += 1
                    continue

            pos[symbol] = {"qty": qty, "entry": price,
                           "stop_loss": entry["stop_loss"], "take_profit": entry["take_profit"]}
            tomadas_por_symbol[symbol] = tomadas_por_symbol.get(symbol, 0) + 1

        curva.append({"ts": ts, "equity": equity})

    cur = pd.DataFrame(curva).set_index("ts")["equity"]
    dd = (cur / cur.cummax() - 1).min()
    t = pd.DataFrame(trades)
    return {
        "equity_final": equity,
        "inicial": float(rcfg["initial_equity"]),
        "curva": cur,
        "max_dd": dd,
        "n_trades": len(t),
        "win_rate": (t["pnl"] > 0).mean() if len(t) else 0,
        "descartes": descartes,
        "por_symbol": tomadas_por_symbol,
        "trades": t,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config_multi.yaml")
    p.add_argument("--capital", type=float, nargs="+", default=[500, 2000])
    p.add_argument("--days", type=int, default=730)
    args = p.parse_args()

    cfg = yaml.safe_load(open(args.config))
    print(f"Descargando {args.days} dias de velas de 1h para {len(cfg['symbols'])} instrumentos...")
    dfs = {}
    for s in cfg["symbols"]:
        try:
            df = fetch_yahoo(s, cfg["timeframe"], days=args.days)
            if len(df) > cfg["strategy"]["ema_trend"] + 50:
                dfs[s] = df
        except Exception as e:
            print(f"  -- {s}: {e}")
    per = f"{min(d.index[0] for d in dfs.values()):%b %Y} a {max(d.index[-1] for d in dfs.values()):%b %Y}"
    print(f"{len(dfs)} instrumentos, {per}\n")

    for cap in args.capital:
        cfg["risk"]["initial_equity"] = cap
        ideal = run(cfg, dfs, realista=False)
        real = run(cfg, dfs, realista=True)

        print("=" * 78)
        print(f"  CAPITAL INICIAL: {cap:,.0f} USD    ({per}, interes compuesto)")
        print("=" * 78)
        print(f"{'':<26}{'backtest original':>20}{'con broker real':>20}")
        print("-" * 78)
        for lbl, k, fmt in [
            ("equity final", "equity_final", "{:,.0f} USD"),
            ("retorno", None, None),
            ("operaciones", "n_trades", "{:,}"),
            ("aciertos", "win_rate", "{:.1%}"),
            ("drawdown maximo", "max_dd", "{:.1%}"),
        ]:
            if lbl == "retorno":
                ri = (ideal["equity_final"] / cap - 1) * 100
                rr = (real["equity_final"] / cap - 1) * 100
                print(f"{lbl:<26}{ri:>+19.1f}%{rr:>+19.1f}%")
            else:
                print(f"{lbl:<26}{fmt.format(ideal[k]):>20}{fmt.format(real[k]):>20}")
        print("-" * 78)
        d = real["descartes"]
        print(f"\n  Señales que el broker real NO deja tomar:")
        print(f"    {d['lote_minimo']:>4}  por no llegar al lote minimo")
        print(f"    {d['margen']:>4}  por falta de margen")
        print(f"\n  Operaciones efectivamente tomadas, por instrumento:")
        for s, n in sorted(real["por_symbol"].items(), key=lambda x: -x[1]):
            print(f"    {CONTRATOS[s]['nombre']:<12} {n:>4}")
        faltantes = [CONTRATOS[s]["nombre"] for s in dfs if s not in real["por_symbol"]]
        if faltantes:
            print(f"    NUNCA operados: {', '.join(faltantes)}")
        print()


if __name__ == "__main__":
    main()
