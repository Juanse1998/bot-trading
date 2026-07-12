"""Descarga y parseo de ticks historicos de Dukascopy (bid/ask reales, gratis).

Dukascopy publica un archivo .bi5 por hora y por instrumento:

    /datafeed/{INSTRUMENTO}/{YYYY}/{MM}/{DD}/{HH}h_ticks.bi5

OJO: el mes va indexado desde 0 (enero = 00, julio = 06). Es el error clasico
al usar este feed: bajas datos de otro mes sin que nada falle.

Cada archivo es LZMA. Descomprimido son registros de 20 bytes big-endian:
    uint32  ms desde el inicio de la hora
    uint32  ask en puntos
    uint32  bid en puntos
    float32 volumen ask
    float32 volumen bid

El divisor de puntos depende del instrumento (XAUUSD: 1000).
"""

import datetime as dt
import lzma
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

BASE = "https://datafeed.dukascopy.com/datafeed"
# Divisor de puntos (10^digitos de precision). Solo hace falta cuando necesitas
# el precio en unidades reales: los ratios tipo ATR/spread son invariantes al
# divisor, pero el nocional de una posicion no lo es.
POINT_DIV = {
    "XAUUSD": 1000.0, "XAGUSD": 1000.0,
    "EURUSD": 100000.0, "GBPUSD": 100000.0, "AUDUSD": 100000.0,
    "USDJPY": 1000.0, "GBPJPY": 1000.0,
}


def _url(symbol: str, when: dt.datetime) -> str:
    return f"{BASE}/{symbol}/{when.year}/{when.month - 1:02d}/{when.day:02d}/{when.hour:02d}h_ticks.bi5"


def _fetch_hour(symbol: str, when: dt.datetime, cache: Path) -> Path | None:
    """Baja una hora de ticks a cache. Devuelve None si no hay datos (fin de semana)."""
    dest = cache / symbol / f"{when:%Y-%m-%d-%H}.bi5"
    if dest.exists():
        return dest if dest.stat().st_size > 0 else None

    dest.parent.mkdir(parents=True, exist_ok=True)
    # Dukascopy limita por tasa (503) y corta conexiones si se lo apura. Un 404
    # es legitimo (fin de semana, sin datos); el resto se reintenta con espera.
    for attempt in range(4):
        r = subprocess.run(
            ["curl", "-s", "-f", "-A", "Mozilla/5.0", "--max-time", "90",
             "-w", "%{http_code}", "-o", str(dest), _url(symbol, when)],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            return dest if dest.stat().st_size > 0 else None
        if r.stdout.strip() == "404":
            dest.unlink(missing_ok=True)
            return None
        time.sleep(2 * (attempt + 1))

    dest.unlink(missing_ok=True)
    return None


_TICK_DTYPE = np.dtype([
    ("ms", ">u4"), ("ask", ">u4"), ("bid", ">u4"),
    ("ask_vol", ">f4"), ("bid_vol", ">f4"),
])


def _parse_hour(path: Path, symbol: str, hour: dt.datetime) -> pd.DataFrame:
    raw = path.read_bytes()
    if not raw:
        return pd.DataFrame()
    data = lzma.LZMADecompressor(format=lzma.FORMAT_AUTO).decompress(raw)
    # El ratio ATR/spread es invariante al divisor, asi que para instrumentos
    # cuya precision no conocemos, 1.0 (puntos crudos) sirve igual.
    div = POINT_DIV.get(symbol, 1.0)

    a = np.frombuffer(data, dtype=_TICK_DTYPE)
    return pd.DataFrame({
        "ts": np.datetime64(hour) + a["ms"].astype("timedelta64[ms]"),
        "bid": a["bid"] / div,
        "ask": a["ask"] / div,
        "bid_vol": a["bid_vol"].astype("f8"),
        "ask_vol": a["ask_vol"].astype("f8"),
    })


def load_ticks(symbol: str, start: dt.date, end: dt.date, cache: Path, workers: int = 12) -> pd.DataFrame:
    """Descarga (con cache en disco) y devuelve todos los ticks del rango."""
    hours = [
        dt.datetime(d.year, d.month, d.day, h)
        for d in pd.date_range(start, end, freq="D")
        for h in range(24)
    ]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        paths = list(pool.map(lambda h: (_fetch_hour(symbol, h, cache), h), hours))

    frames = [_parse_hour(p, symbol, h) for p, h in paths if p is not None]
    if not frames:
        raise RuntimeError("no se descargo ningun tick")

    df = pd.concat(frames, ignore_index=True).sort_values("ts").reset_index(drop=True)
    df["mid"] = (df["bid"] + df["ask"]) / 2
    df["spread"] = df["ask"] - df["bid"]
    return df


def to_bars(ticks: pd.DataFrame, freq: str = "2min") -> pd.DataFrame:
    """Velas OHLC del mid, mas el spread promedio y maximo dentro de cada vela.

    Guardar el spread por vela es el punto de todo esto: permite cobrar el costo
    real del momento de la operacion en vez de un promedio optimista.
    """
    g = ticks.set_index("ts")
    bars = g["mid"].resample(freq).ohlc()
    bars["spread_mean"] = g["spread"].resample(freq).mean()
    bars["spread_max"] = g["spread"].resample(freq).max()
    bars["ticks"] = g["mid"].resample(freq).count()
    return bars.dropna()
