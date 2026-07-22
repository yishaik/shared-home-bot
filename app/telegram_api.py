from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings


log = logging.getLogger("homebot.telegram.raw_api")


class TelegramRawApi:
    """Feature-gated access to Bot API methods newer than the installed PTB release."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = httpx.AsyncClient(timeout=20)

    @property
    def enabled(self) -> bool:
        return bool(self.settings.telegram_raw_api_enabled and self.settings.telegram_bot_token)

    async def close(self) -> None:
        await self._client.aclose()

    async def call(self, method: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        url = (
            f"{self.settings.telegram_bot_api_base_url.rstrip('/')}"
            f"/bot{self.settings.telegram_bot_token}/{method}"
        )
        try:
            response = await self._client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                log.warning("Telegram raw method failed method=%s description=%s", method, data.get("description"))
                return None
            return data.get("result") if isinstance(data.get("result"), dict) else data
        except Exception:
            log.exception("Telegram raw method failed method=%s", method)
            return None

    async def send_ephemeral_text(
        self,
        *,
        chat_id: int,
        receiver_user_id: int,
        text: str,
        message_thread_id: int | None = None,
        callback_query_id: str | None = None,
    ) -> bool:
        if not self.settings.telegram_enable_ephemeral_messages:
            return False
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "receiver_user_id": receiver_user_id,
            "text": text,
        }
        if message_thread_id is not None:
            payload["message_thread_id"] = message_thread_id
        if callback_query_id:
            payload["callback_query_id"] = callback_query_id
        return await self.call("sendMessage", payload) is not None

    async def send_rich_message(self, payload: dict[str, Any]) -> bool:
        if not self.settings.telegram_enable_rich_messages:
            return False
        return await self.call("sendRichMessage", payload) is not None
