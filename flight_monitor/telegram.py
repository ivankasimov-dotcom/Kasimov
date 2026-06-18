from __future__ import annotations

import requests

from .config import Settings


class TelegramAPIError(RuntimeError):
    """Raised when Telegram rejects a notification request."""


class TelegramNotifier:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = requests.Session()

    def send_message(self, text: str, parse_mode: str | None = "HTML") -> None:
        url = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": self.settings.telegram_chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        response = self.session.post(
            url,
            json=payload,
            timeout=self.settings.request_timeout_seconds,
        )

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise TelegramAPIError(f"Telegram notification failed: {response.status_code} {response.text}") from exc
