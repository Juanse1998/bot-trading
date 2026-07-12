"""Estrategias de trading, solo largos (spot).

Todas comparten la misma interfaz: `check_entry` devuelve stop/target si hay
señal de compra en la última vela cerrada, `check_exit` devuelve el motivo de
venta (además del stop loss / take profit, que se controlan por fuera).

- trend: cruce alcista EMA rápida/lenta con filtro de tendencia y RSI.
  Pocas señales, sigue movimientos largos.
- meanrev: compra caídas (RSI en sobreventa) dentro de una tendencia alcista
  y vende cuando el RSI se recupera. Muchas más señales.
"""

from dataclasses import dataclass

import pandas as pd

from .indicators import atr, ema, rsi


@dataclass
class Signal:
    action: str            # "buy" | "sell" | "hold"
    reason: str
    price: float
    stop_loss: float | None = None
    take_profit: float | None = None


def add_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"] = ema(df["close"], cfg["ema_fast"])
    df["ema_slow"] = ema(df["close"], cfg["ema_slow"])
    df["ema_trend"] = ema(df["close"], cfg["ema_trend"])
    df["rsi"] = rsi(df["close"], cfg["rsi_period"])
    df["atr"] = atr(df, cfg["atr_period"])
    return df


class TrendFollowing:
    name = "trend"

    def check_entry(self, prev: pd.Series, row: pd.Series, cfg: dict) -> dict | None:
        crossed_up = prev["ema_fast"] <= prev["ema_slow"] and row["ema_fast"] > row["ema_slow"]
        uptrend = row["close"] > row["ema_trend"]
        if crossed_up and uptrend and row["rsi"] < cfg["rsi_max_entry"]:
            price, atr_value = float(row["close"]), float(row["atr"])
            return {
                "reason": "cruce alcista EMA con tendencia alcista y RSI ok",
                "stop_loss": price - cfg["atr_stop_mult"] * atr_value,
                "take_profit": price + cfg["atr_target_mult"] * atr_value,
            }
        return None

    def check_exit(self, prev: pd.Series, row: pd.Series, cfg: dict) -> str | None:
        crossed_down = prev["ema_fast"] >= prev["ema_slow"] and row["ema_fast"] < row["ema_slow"]
        return "cruce bajista EMA rápida/lenta" if crossed_down else None


class MeanReversion:
    name = "meanrev"

    def check_entry(self, prev: pd.Series, row: pd.Series, cfg: dict) -> dict | None:
        uptrend = row["close"] > row["ema_trend"]
        oversold = row["rsi"] < cfg["rsi_oversold"]
        if uptrend and oversold:
            price, atr_value = float(row["close"]), float(row["atr"])
            return {
                "reason": f"RSI en sobreventa ({row['rsi']:.0f}) con tendencia alcista",
                "stop_loss": price - cfg["atr_stop_mult"] * atr_value,
                "take_profit": price + cfg["atr_target_mult"] * atr_value,
            }
        return None

    def check_exit(self, prev: pd.Series, row: pd.Series, cfg: dict) -> str | None:
        if row["rsi"] > cfg["rsi_exit"]:
            return f"RSI recuperado ({row['rsi']:.0f})"
        return None


STRATEGIES = {s.name: s for s in (TrendFollowing(), MeanReversion())}


def get_strategy(cfg: dict):
    try:
        return STRATEGIES[cfg["name"]]
    except KeyError:
        raise ValueError(
            f"Estrategia desconocida: {cfg['name']!r}. Opciones: {sorted(STRATEGIES)}"
        ) from None


def evaluate(df: pd.DataFrame, cfg: dict, position: dict | None) -> Signal:
    """Evalúa la última vela CERRADA. `position` es la posición abierta o None."""
    strategy = get_strategy(cfg)
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row["close"])

    if position is not None:
        # El stop/target se evalúan contra el mínimo/máximo de la vela, no contra
        # el cierre: una orden real en el broker se dispara cuando el precio TOCA
        # el nivel, aunque después se recupere dentro de la misma vela. Y el fill
        # ocurre EN el nivel, no en el cierre (que ya está más allá).
        # El backtest hace exactamente esto; si acá mirásemos solo el cierre, el
        # paper trading mediría una estrategia distinta a la que validamos.
        if float(row["low"]) <= position["stop_loss"]:
            return Signal("sell", "stop loss alcanzado", float(position["stop_loss"]))
        if float(row["high"]) >= position["take_profit"]:
            return Signal("sell", "take profit alcanzado", float(position["take_profit"]))
        reason = strategy.check_exit(prev, row, cfg)
        if reason:
            return Signal("sell", reason, price)
        return Signal("hold", "posición abierta, sin señal de salida", price)

    entry = strategy.check_entry(prev, row, cfg)
    if entry:
        return Signal("buy", entry["reason"], price,
                      stop_loss=entry["stop_loss"], take_profit=entry["take_profit"])
    return Signal("hold", "sin señal de entrada", price)
