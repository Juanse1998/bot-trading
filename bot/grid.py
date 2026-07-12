"""Comparacion de TODAS las estrategias contra TODAS las clases de activo.

Advertencia metodologica, que es el punto entero de este archivo: si se prueban
N combinaciones y se elige la de mayor ganancia, se encuentra una ganadora por
azar aunque ninguna sirva. Cuantas mas combinaciones, mas seguro el espejismo.

Por eso aca NO se reporta "la mejor". Se parte el tiempo en dos:

    ENTRENO      70% mas viejo  -> donde uno elegiria
    VALIDACION   30% mas nuevo  -> nunca visto, es el que dice la verdad

Una combinacion solo vale si gana en LAS DOS. Si brilla en entreno y se apaga en
validacion, es ruido — que es lo que le pasa a la mayoria.
"""

import argparse
import itertools

import pandas as pd
import yaml

from bot.data import fetch_yahoo, get_exchange, fetch_ohlcv_history
from bot.realistic_backtest import CONTRATOS, run

GRUPOS = {
    "forex":   ["EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X", "USDCAD=X", "NZDUSD=X"],
    "metales": ["GC=F", "SI=F"],
    "indices": ["^GSPC", "^NDX"],
    "cripto":  ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"],
}

# Cripto spot: el lote minimo es despreciable y no hay apalancamiento.
CRIPTO_SPEC = {"unidades": 0.0001, "apalanc": 1, "usd_base": False}

ESTRATEGIAS = [
    ("meanrev", 2.0), ("meanrev", 3.0), ("meanrev", 4.0),
    ("trend", 2.0), ("trend", 3.0),
]


def cargar(grupo: str, símbolos: list[str], días: int) -> dict:
    dfs = {}
    if grupo == "cripto":
        ex = get_exchange("binance")
        for s in símbolos:
            try:
                dfs[s] = fetch_ohlcv_history(ex, s, "1h", días)
                CONTRATOS[s] = {**CRIPTO_SPEC, "nombre": s.split("/")[0]}
            except Exception as e:
                print(f"  -- {s}: {e}")
    else:
        for s in símbolos:
            try:
                dfs[s] = fetch_yahoo(s, "1h", days=días)
            except Exception as e:
                print(f"  -- {s}: {e}")
    return {s: d for s, d in dfs.items() if len(d) > 250}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--capital", type=float, default=2000)
    p.add_argument("--days", type=int, default=729)
    args = p.parse_args()

    print(f"Descargando {args.days} dias de velas de 1h...")
    datos = {g: cargar(g, s, args.days) for g, s in GRUPOS.items()}
    for g, d in datos.items():
        print(f"  {g:<9} {len(d)} instrumentos")

    filas = []
    for grupo, dfs in datos.items():
        if not dfs:
            continue
        todas = sorted(set().union(*(d.index for d in dfs.values())))
        corte = todas[int(len(todas) * 0.70)]
        tr = {s: d[d.index <= corte] for s, d in dfs.items()}
        te = {s: d[d.index > corte] for s, d in dfs.items()}
        tr = {s: d for s, d in tr.items() if len(d) > 250}
        te = {s: d for s, d in te.items() if len(d) > 250}
        if not tr or not te:
            continue

        for nombre, tgt in ESTRATEGIAS:
            res = []
            for dd in (tr, te):
                cfg = yaml.safe_load(open("config_multi.yaml"))
                cfg["strategy"]["name"] = nombre
                cfg["strategy"]["atr_target_mult"] = tgt
                cfg["risk"]["initial_equity"] = args.capital
                try:
                    r = run(cfg, dd, realista=True)
                    res.append(r)
                except Exception:
                    res.append(None)
            if None in res:
                continue
            ins, out = res
            filas.append({
                "grupo": grupo, "estrategia": f"{nombre} {tgt}x",
                "in": (ins["equity_final"] / args.capital - 1) * 100,
                "out": (out["equity_final"] / args.capital - 1) * 100,
                "trades": out["n_trades"], "wr": out["win_rate"] * 100,
                "dd": out["max_dd"] * 100,
            })

    df = pd.DataFrame(filas)
    df["ambas"] = (df["in"] > 0) & (df["out"] > 0)
    df = df.sort_values("out", ascending=False)

    print(f"\n{'=' * 92}")
    print("  TODAS LAS ESTRATEGIAS x TODAS LAS CLASES DE ACTIVO")
    print("  Solo sirve lo que gana en LAS DOS columnas.")
    print(f"{'=' * 92}")
    print(f"{'clase':<10}{'estrategia':<16}{'ENTRENO':>11}{'VALIDACION':>13}"
          f"{'ops':>7}{'aciertos':>10}{'max DD':>9}   ¿sirve?")
    print("-" * 92)
    for _, r in df.iterrows():
        ok = "SI" if r["ambas"] else "no"
        print(f"{r['grupo']:<10}{r['estrategia']:<16}{r['in']:>+10.1f}%{r['out']:>+12.1f}%"
              f"{r['trades']:>7.0f}{r['wr']:>9.0f}%{r['dd']:>8.0f}%   {ok}")
    print("-" * 92)

    buenas = df[df["ambas"]]
    print(f"\n  {len(buenas)} de {len(df)} combinaciones ganan en ambos periodos.")
    if len(buenas):
        b = buenas.iloc[0]
        print(f"  Mejor sobreviviente: {b['estrategia']} en {b['grupo']} "
              f"({b['in']:+.1f}% entreno / {b['out']:+.1f}% validacion)")


if __name__ == "__main__":
    main()
