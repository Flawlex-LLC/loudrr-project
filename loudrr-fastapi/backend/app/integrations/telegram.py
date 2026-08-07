"""Telegram Bot API client (spec §7) — sends notification messages.

Only ever called from the outbox drain (never inline in a request). Raises on
failure so the drain can mark the event for retry.
"""
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0)


class TelegramClient:
    def __init__(self, bot_token: str | None = None):
        self.bot_token = bot_token if bot_token is not None else settings.telegram_bot_token

    async def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: dict | None = None,
    ) -> bool:
        """Send a Telegram text message. Optionally attach an inline-keyboard
        reply_markup (parity with Django bots/telegram/notifications.py:43-46 —
        the "Open Loudrr" WebApp button on waitlist cards)."""
        if not self.bot_token:
            logger.warning("TELEGRAM_BOT_TOKEN not configured — skipping send")
            return False
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()  # raise → outbox marks for retry
        return True


def get_telegram_client() -> TelegramClient:
    return TelegramClient()
