"""¿Existe ALGUNA estructura predecible en el oro a horizonte de minutos?

Probar estrategias de a una y quedarse con la que gana es la receta para
sobreoptimizar: con suficientes intentos, el azar entrega una ganadora. Este
test hace la pregunta que las engloba a todas.

Le damos a un modelo de gradient boosting todo lo que un scalper podria mirar
—precio, volatilidad, indicadores y, sobre todo, FLUJO DE ORDENES tick a tick—
y medimos cuanto predice del movimiento futuro FUERA DE MUESTRA (entrena con el
70% mas viejo, se evalua en el 30% mas nuevo, sin mezclar el tiempo).

La vara es el spread: ~$0.65 por onza. Si el modelo mas favorable no logra
seleccionar operaciones cuyo movimiento esperado supere eso, entonces no hay
estrategia manual que lo logre, y el problema es el mercado, no el indicador.
"""

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from bot.dukascopy import load_ticks
from bot.indicators import atr, ema, rsi

SPREAD = 0.65
HORIZON = 5  # velas a futuro (5 x 2min = 10 min)


def build_bars(ticks: pd.DataFrame, freq: str = "2min") -> pd.DataFrame:
    """Velas con microestructura: ademas de OHLC, el desequilibrio de flujo."""
    t = ticks.copy()
    t["mid"] = (t["bid"] + t["ask"]) / 2
    t["spread"] = t["ask"] - t["bid"]
    # Regla del tick: si el mid sube, el trade fue iniciado por un comprador.
    t["tick_dir"] = np.sign(t["mid"].diff()).fillna(0)
    t["signed_vol"] = t["tick_dir"] * (t["bid_vol"] + t["ask_vol"])

    g = t.set_index("ts")
    bars = g["mid"].resample(freq).ohlc()
    bars["spread"] = g["spread"].resample(freq).mean()
    bars["n_ticks"] = g["mid"].resample(freq).count()
    bars["volume"] = g[["bid_vol", "ask_vol"]].sum(axis=1).resample(freq).sum()
    # Flujo de ordenes: el insumo real del scalping profesional.
    bars["ofi"] = g["signed_vol"].resample(freq).sum()
    bars["tick_imb"] = g["tick_dir"].resample(freq).mean()
    bars["bid_ask_vol_imb"] = (
        (g["bid_vol"].resample(freq).sum() - g["ask_vol"].resample(freq).sum())
        / (g["bid_vol"].resample(freq).sum() + g["ask_vol"].resample(freq).sum())
    )
    return bars.dropna()


def features(bars: pd.DataFrame) -> pd.DataFrame:
    """Todo lo conocido AL CIERRE de cada vela. Nada del futuro."""
    c = bars["close"]
    f = pd.DataFrame(index=bars.index)

    for n in (1, 2, 3, 5, 10, 20, 30, 60):
        f[f"ret_{n}"] = c - c.shift(n)
    for n in (5, 20, 60):
        f[f"vol_{n}"] = c.diff().rolling(n).std()
        f[f"ofi_{n}"] = bars["ofi"].rolling(n).sum()
        f[f"tickimb_{n}"] = bars["tick_imb"].rolling(n).mean()

    f["atr"] = atr(bars, 14)
    f["rsi"] = rsi(c, 14)
    f["rsi_fast"] = rsi(c, 5)
    f["z20"] = (c - c.rolling(20).mean()) / c.rolling(20).std()
    f["z60"] = (c - c.rolling(60).mean()) / c.rolling(60).std()
    f["ema_dist"] = c - ema(c, 200)
    f["range"] = bars["high"] - bars["low"]
    f["body"] = bars["close"] - bars["open"]
    f["spread"] = bars["spread"]
    f["n_ticks"] = bars["n_ticks"]
    f["volume"] = bars["volume"]
    f["ofi"] = bars["ofi"]
    f["tick_imb"] = bars["tick_imb"]
    f["vol_imb"] = bars["bid_ask_vol_imb"]

    hour = bars.index.hour + bars.index.minute / 60
    f["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    f["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    return f


def main() -> None:
    cache = Path("/private/tmp/claude-501/-Users-juanse-Desktop-Personal-Proyectos-bot-trading/4e7b4d7e-309e-409e-8470-c25ef6eaaae6/scratchpad/ticks")
    ticks = load_ticks("XAUUSD", dt.date(2026, 6, 4), dt.date(2026, 7, 8), cache)

    bars = build_bars(ticks)
    X = features(bars)
    y = bars["close"].shift(-HORIZON) - bars["close"]  # movimiento futuro en $/onza

    ok = X.notna().all(axis=1) & y.notna()
    X, y = X[ok], y[ok]

    split = int(len(X) * 0.70)
    Xtr, ytr, Xte, yte = X[:split], y[:split], X[split:], y[split:]

    print(f"\n{len(X):,} velas de 2min  |  {X.shape[1]} features (incluye flujo de ordenes)")
    print(f"entrena: {X.index[0]:%d-%b} a {X.index[split-1]:%d-%b}  ({len(Xtr):,})")
    print(f"evalua : {X.index[split]:%d-%b} a {X.index[-1]:%d-%b}  ({len(Xte):,})  <- nunca visto\n")

    model = HistGradientBoostingRegressor(
        max_iter=400, learning_rate=0.05, max_depth=6,
        l2_regularization=1.0, random_state=0,
    )
    model.fit(Xtr, ytr)
    pred = model.predict(Xte)

    ss_res = ((yte - pred) ** 2).sum()
    ss_tot = ((yte - ytr.mean()) ** 2).sum()
    r2 = 1 - ss_res / ss_tot

    print("=" * 74)
    print("  PODER PREDICTIVO FUERA DE MUESTRA")
    print("=" * 74)
    print(f"  R2 out-of-sample: {r2:+.4f}   (0 = no predice nada; negativo = peor que la media)\n")

    print("  Si operaramos SOLO las señales mas fuertes del modelo:")
    print(f"  {'selectividad':<22}{'n':>7}{'mov. real prom':>17}{'vs spread 0.65':>17}")
    print("-" * 74)
    for q, label in [(0.90, "top 10% alcistas"), (0.95, "top 5% alcistas"), (0.99, "top 1% alcistas")]:
        thr = np.quantile(pred, q)
        m = pred >= thr
        mv = yte[m].mean()
        print(f"  {label:<22}{m.sum():>7}{mv:>+17.3f}{mv - SPREAD:>+17.3f}")
    for q, label in [(0.10, "top 10% bajistas"), (0.05, "top 5% bajistas"), (0.01, "top 1% bajistas")]:
        thr = np.quantile(pred, q)
        m = pred <= thr
        mv = -yte[m].mean()  # en corto se gana cuando baja
        print(f"  {label:<22}{m.sum():>7}{mv:>+17.3f}{mv - SPREAD:>+17.3f}")
    print("-" * 74)
    print("  (la ultima columna es la ganancia neta por onza; negativa = pierde plata)\n")

    imp = pd.Series(
        np.abs(np.corrcoef(np.column_stack([Xte.values, pred]), rowvar=False)[-1, :-1]),
        index=X.columns,
    ).sort_values(ascending=False)
    print("  Lo que mas mira el modelo:")
    for k, v in imp.head(6).items():
        print(f"    {k:<16} {v:.3f}")


if __name__ == "__main__":
    main()
