"""Paper trading: portafolio simulado persistido en JSON."""

import json
from datetime import datetime, timezone
from pathlib import Path

from .contracts import nocional, pnl_usd


class PaperPortfolio:
    def __init__(self, state_file: str, initial_equity: float, fee_pct: float):
        self.path = Path(state_file)
        self.fee_pct = fee_pct
        if self.path.exists():
            self.state = json.loads(self.path.read_text())
        else:
            self.state = {
                "equity": initial_equity,
                "positions": {},   # symbol -> posición abierta
                "history": [],     # operaciones cerradas
            }
            self._save()

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.state, indent=2, default=str))

    def get_position(self, symbol: str) -> dict | None:
        return self.state["positions"].get(symbol)

    def open_position(self, symbol: str, price: float, qty: float,
                      stop_loss: float, take_profit: float) -> dict:
        position = {
            "symbol": symbol,
            "entry": price,
            "qty": qty,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "opened_at": datetime.now(timezone.utc).isoformat(),
        }
        self.state["positions"][symbol] = position
        self._save()
        return position

    def close_position(self, symbol: str, price: float, reason: str) -> dict:
        position = self.state["positions"].pop(symbol)
        # El P&L se calcula con la especificación del contrato: en los pares donde
        # el dólar es la moneda base (USD/JPY), la ganancia sale en yenes y hay que
        # convertirla. La comisión se cobra sobre el nocional, en ambas puntas.
        bruto = pnl_usd(symbol, position["qty"], position["entry"], price)
        comision = self.fee_pct * (nocional(symbol, position["qty"], position["entry"])
                                   + nocional(symbol, position["qty"], price))
        pnl = bruto - comision
        self.state["equity"] += pnl
        trade = {
            **position,
            "exit": price,
            "pnl": round(pnl, 2),
            "reason": reason,
            "closed_at": datetime.now(timezone.utc).isoformat(),
        }
        self.state["history"].append(trade)
        self._save()
        return trade

    @property
    def equity(self) -> float:
        return self.state["equity"]

    def snapshot(self) -> None:
        """Registra el equity actual, una vez por hora, para graficar la curva.

        Se limita a una muestra por hora para que el archivo no crezca sin freno:
        el bot corre cada 15 minutos, pero opera sobre velas de 1 hora.
        """
        curve = self.state.setdefault("curve", [])
        ahora = datetime.now(timezone.utc)
        hora = ahora.replace(minute=0, second=0, microsecond=0).isoformat()
        if curve and curve[-1]["ts"] == hora:
            curve[-1]["equity"] = round(self.equity, 2)
        else:
            curve.append({"ts": hora, "equity": round(self.equity, 2)})
        self._save()
