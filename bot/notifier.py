"""Envío de señales: consola siempre, Telegram si está configurado."""

import logging

import requests

log = logging.getLogger("bot")


class Notifier:
    def __init__(self, telegram_cfg: dict):
        self.token = telegram_cfg.get("token") or ""
        self.chat_id = str(telegram_cfg.get("chat_id") or "")

    def send(self, message: str) -> None:
        log.info(message)
        if self.token and self.chat_id:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={"chat_id": self.chat_id, "text": message, "parse_mode": "Markdown"},
                    timeout=10,
                )
            except requests.RequestException as exc:
                log.warning("No se pudo enviar a Telegram: %s", exc)
