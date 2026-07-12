"""Especificaciones de contrato de cada instrumento, tal como las impone un broker.

El backtest y el bot en vivo TIENEN que usar las mismas: si el backtest compra
0.0037 onzas de oro y el bot en vivo tambien, los dos mienten igual. La unica
forma de que el paper trading mida algo real es que ambos respeten el lote
minimo y el margen.

  unidades : tamaño de 0.01 lote (el minimo que un broker retail deja operar)
  apalanc  : apalancamiento maximo tipico (define el margen exigido)
  usd_base : el dolar es la moneda BASE del par (USD/JPY, USD/CAD).
             En esos, 1.000 unidades valen 1.000 USD y el P&L sale en la moneda
             cotizada, asi que hay que dividirlo por el precio para pasarlo a USD.
"""

CONTRATOS: dict[str, dict] = {
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

# Fallback para instrumentos sin especificar (cripto spot: sin lote minimo real).
DEFAULT = {"unidades": 0.0001, "apalanc": 1, "usd_base": False, "nombre": "?"}


def spec(symbol: str) -> dict:
    return CONTRATOS.get(symbol, {**DEFAULT, "nombre": symbol})


def nocional(symbol: str, qty: float, price: float) -> float:
    """Cuanto vale la posicion, en USD."""
    return qty if spec(symbol)["usd_base"] else qty * price


def margen(symbol: str, qty: float, price: float) -> float:
    """Cuanta plata inmoviliza esa posicion."""
    return nocional(symbol, qty, price) / spec(symbol)["apalanc"]


def pnl_usd(symbol: str, qty: float, entry: float, exit_: float) -> float:
    """P&L de un largo, en USD."""
    bruto = qty * (exit_ - entry)
    return bruto / exit_ if spec(symbol)["usd_base"] else bruto
