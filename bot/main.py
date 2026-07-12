"""Loop principal: genera señales en vivo y las registra en el paper trading.

Uso:
    python -m bot.main            # corre en loop, revisa cada `poll_minutes`
    python -m bot.main --once     # una sola pasada (útil para cron)
"""

import argparse
import logging
import time

import yaml

from .data import fetch_candles, get_exchange
from .notifier import Notifier
from .paper import PaperPortfolio
from .risk import position_size
from .strategy import add_indicators, evaluate

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot")


def fmt(price: float) -> str:
    """Formatea precios con decimales acordes a su magnitud (BTC vs DOGE)."""
    if price >= 100:
        return f"{price:,.2f}"
    if price >= 1:
        return f"{price:,.4f}"
    return f"{price:.6f}"


def check_symbol(exchange, symbol: str, cfg: dict,
                 portfolio: PaperPortfolio, notifier: Notifier) -> None:
    scfg, rcfg = cfg["strategy"], cfg["risk"]
    df = fetch_candles(exchange, cfg["exchange"], symbol,
                       cfg["timeframe"], scfg["ema_trend"] + 60)
    df = df.iloc[:-1]  # descartar la vela en curso: solo velas cerradas
    df = add_indicators(df, scfg)

    position = portfolio.get_position(symbol)
    signal = evaluate(df, scfg, position)

    if signal.action == "buy":
        # Se le pasan el símbolo y las posiciones abiertas para que respete el
        # lote mínimo y el margen disponible. Sin eso el bot "compraría" tamaños
        # que ningún broker acepta y el paper trading no mediría nada real.
        qty = position_size(portfolio.equity, signal.price, signal.stop_loss, rcfg,
                            symbol=symbol, abiertas=portfolio.state["positions"])
        if qty <= 0:
            log.info("%s: señal descartada (no llega al lote mínimo o falta margen)", symbol)
            return
        portfolio.open_position(symbol, signal.price, qty,
                                signal.stop_loss, signal.take_profit)
        notifier.send(
            f"🟢 *COMPRA {symbol}*\n"
            f"Precio: {fmt(signal.price)}\n"
            f"Cantidad: {qty:.6f}\n"
            f"Stop loss: {fmt(signal.stop_loss)}\n"
            f"Take profit: {fmt(signal.take_profit)}\n"
            f"Motivo: {signal.reason}"
        )
    elif signal.action == "sell":
        trade = portfolio.close_position(symbol, signal.price, signal.reason)
        emoji = "✅" if trade["pnl"] > 0 else "🔴"
        notifier.send(
            f"{emoji} *VENTA {symbol}*\n"
            f"Precio: {fmt(signal.price)}\n"
            f"PnL: {trade['pnl']:+,.2f} USD\n"
            f"Motivo: {signal.reason}\n"
            f"Equity: {portfolio.equity:,.2f} USD"
        )
    else:
        log.info("%s: %s (precio %s)", symbol, signal.reason, fmt(signal.price))


def run_once(cfg: dict, portfolio: PaperPortfolio, notifier: Notifier) -> None:
    exchange = get_exchange(cfg["exchange"])
    for symbol in cfg["symbols"]:
        try:
            check_symbol(exchange, symbol, cfg, portfolio, notifier)
        except Exception:
            log.exception("Error procesando %s", symbol)
    portfolio.snapshot()


def main() -> None:
    parser = argparse.ArgumentParser(description="Bot de señales de trading")
    parser.add_argument("--once", action="store_true", help="una sola pasada y salir")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    portfolio = PaperPortfolio(
        cfg["paper"]["state_file"],
        cfg["risk"]["initial_equity"],
        cfg["risk"]["fee_pct"],
    )
    notifier = Notifier(cfg.get("telegram", {}))

    if args.once:
        run_once(cfg, portfolio, notifier)
        return

    poll_seconds = cfg["loop"]["poll_minutes"] * 60
    log.info("Bot iniciado. Equity: %.2f USD. Revisión cada %d min.",
             portfolio.equity, cfg["loop"]["poll_minutes"])
    while True:
        run_once(cfg, portfolio, notifier)
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
