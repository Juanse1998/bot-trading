"""Descarga de velas OHLCV.

Dos fuentes según `exchange` en config.yaml:
- un exchange de cripto vía ccxt (ej: "binance"), datos públicos sin API key
- "yahoo": Yahoo Finance vía yfinance, para forex ("EURUSD=X"), acciones
  ("AAPL") o índices. Intradía limitado a ~730 días de histórico.
"""

import ccxt
import pandas as pd
import yfinance as yf

COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def get_exchange(name: str) -> ccxt.Exchange | None:
    if name == "yahoo":
        return None
    exchange_class = getattr(ccxt, name)
    return exchange_class({"enableRateLimit": True})


def _from_yahoo(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    df.index = df.index.tz_convert("UTC")
    df.index.name = "timestamp"
    return df


def fetch_yahoo(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    days = min(days, 729)  # límite de Yahoo para velas intradía
    df = yf.Ticker(symbol).history(period=f"{days}d", interval=timeframe)
    if df.empty:
        raise ValueError(f"Yahoo no devolvió datos para {symbol} ({timeframe})")
    return _from_yahoo(df)


def fetch_ohlcv(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str,
    limit: int = 500,
    since: int | None = None,
) -> pd.DataFrame:
    raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit, since=since)
    df = pd.DataFrame(raw, columns=COLUMNS)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.set_index("timestamp")


def fetch_candles(exchange, source: str, symbol: str,
                  timeframe: str, limit: int) -> pd.DataFrame:
    """Últimas `limit` velas, de la fuente que corresponda."""
    if source == "yahoo":
        per_day = {"1h": 20, "1d": 1}.get(timeframe, 20)
        return fetch_yahoo(symbol, timeframe, days=limit // per_day + 5).tail(limit)
    return fetch_ohlcv(exchange, symbol, timeframe, limit=limit)


def fetch_ohlcv_history(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str,
    days: int,
) -> pd.DataFrame:
    """Descarga histórico paginando hacia atrás hasta cubrir `days` días."""
    since = exchange.milliseconds() - days * 24 * 60 * 60 * 1000
    frames = []
    cursor = since
    while True:
        df = fetch_ohlcv(exchange, symbol, timeframe, limit=1000, since=cursor)
        if df.empty:
            break
        frames.append(df)
        last_ms = int(df.index[-1].timestamp() * 1000)
        if len(df) < 1000:
            break
        cursor = last_ms + 1
    history = pd.concat(frames)
    return history[~history.index.duplicated(keep="first")].sort_index()
