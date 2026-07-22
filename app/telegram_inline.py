from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass

from telegram import InlineQueryResultArticle, InputTextMessageContent, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ChosenInlineResultHandler,
    ContextTypes,
    InlineQueryHandler,
)

from app.calendar_service import CalendarService
from app.config import Settings
from app.share_policy import ShareKind, SharePolicy
from app.share_renderers import ShareCard, event_card, help_card, shopping_card, todo_card
from app.store_v2 import Store


log = logging.getLogger("homebot.telegram.inline")

INLINE_USAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS telegram_inline_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL,
    result_id TEXT NOT NULL,
    result_kind TEXT NOT NULL,
    query_hash TEXT NOT NULL DEFAULT '',
    chosen_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_telegram_inline_usage_user
    ON telegram_inline_usage(telegram_user_id, chosen_at DESC);
"""

ALIASES: dict[str, ShareKind] = {
    "todo": "todo",
    "todos": "todo",
    "task": "todo",
    "tasks": "todo",
    "משימה": "todo",
    "משימות": "todo",
    "מטלה": "todo",
    "מטלות": "todo",
    "shop": "shopping",
    "shopping": "shopping",
    "buy": "shopping",
    "קניות": "shopping",
    "קניה": "shopping",
    "לקנות": "shopping",
    "event": "event",
    "events": "event",
    "calendar": "event",
    "אירוע": "event",
    "אירועים": "event",
    "יומן": "event",
    "help": "help",
    "עזרה": "help",
}


@dataclass(frozen=True, slots=True)
class InlineIntent:
    kind: ShareKind | None
    term: str


def parse_inline_intent(raw_query: str) -> InlineIntent:
    normalized = " ".join((raw_query or "").split()).strip()
    if not normalized:
        return InlineIntent(None, "")
    first, _, remainder = normalized.partition(" ")
    key = first.casefold().rstrip(":")
    kind = ALIASES.get(key)
    return InlineIntent(kind, remainder.strip() if kind else normalized)


def _parse_offset(value: str) -> int:
    try:
        return max(0, int(value or "0"))
    except (TypeError, ValueError):
        return 0


def _kind_from_result_id(result_id: str) -> str:
    parts = (result_id or "").split(":", 2)
    return parts[1] if len(parts) >= 3 and parts[0] == "sh" else "unknown"


class InlineShareService:
    """Read-only, policy-gated search for content that may leave the household."""

    def __init__(
        self,
        settings: Settings,
        store: Store,
        *,
        calendar: CalendarService | None = None,
    ):
        self.settings = settings
        self.store = store
        self.policy = SharePolicy(settings)
        self.calendar = calendar or CalendarService(settings, store)
        self._schema_lock = asyncio.Lock()
        self._schema_ready = False

    async def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._schema_lock:
            if self._schema_ready:
                return
            await self.store.db.executescript(INLINE_USAGE_SCHEMA)
            await self.store.db.commit()
            self._schema_ready = True

    def actor_may_use(self, actor_id: int) -> bool:
        return self.settings.telegram_inline_enabled and self.policy.actor_may_use(actor_id)

    async def _todo_cards(self) -> list[ShareCard]:
        rows = await self.store.list_todos(False)
        return [todo_card(row, self.settings.household_timezone) for row in rows]

    async def _shopping_cards(self) -> list[ShareCard]:
        rows = await self.store.shop_list(False)
        return [shopping_card(row, self.settings.household_timezone) for row in rows]

    async def _event_cards(self) -> list[ShareCard]:
        rows = await self.calendar.list_events()
        return [
            event_card(row, self.settings.household_timezone)
            for row in rows
            if str(row.get("status") or "confirmed") != "cancelled"
        ]

    async def _cards_for_kind(self, kind: ShareKind) -> list[ShareCard]:
        if kind == "todo":
            return await self._todo_cards()
        if kind == "shopping":
            return await self._shopping_cards()
        if kind == "event":
            return await self._event_cards()
        return [help_card()]

    async def cards(self, actor_id: int, raw_query: str) -> list[ShareCard]:
        if not self.actor_may_use(actor_id):
            return []
        intent = parse_inline_intent(raw_query)
        if intent.kind and not self.policy.may_share(
            actor_id=actor_id, kind=intent.kind, surface="inline"
        ):
            return []

        if intent.kind:
            cards = await self._cards_for_kind(intent.kind)
        else:
            todo_rows, shopping_rows, event_rows = await asyncio.gather(
                self._todo_cards(), self._shopping_cards(), self._event_cards()
            )
            if not intent.term:
                cards = [*todo_rows[:5], *shopping_rows[:5], *event_rows[:5], help_card()]
            else:
                cards = [*todo_rows, *shopping_rows, *event_rows]

        needle = intent.term.casefold()
        if needle:
            cards = [card for card in cards if needle in card.search_text]
        if not cards and intent.kind != "help":
            return [help_card()]
        return cards

    async def page(
        self,
        *,
        actor_id: int,
        raw_query: str,
        offset: int,
    ) -> tuple[list[InlineQueryResultArticle], str]:
        cards = await self.cards(actor_id, raw_query)
        max_results = self.settings.telegram_inline_max_results
        page_cards = cards[offset : offset + max_results]
        next_offset = str(offset + len(page_cards)) if offset + len(page_cards) < len(cards) else ""
        results = [
            InlineQueryResultArticle(
                id=card.result_id,
                title=card.title,
                description=card.description,
                input_message_content=InputTextMessageContent(
                    card.message_html,
                    parse_mode=ParseMode.HTML,
                ),
            )
            for card in page_cards
        ]
        return results, next_offset

    async def record_chosen(self, *, actor_id: int, result_id: str, query: str) -> None:
        if not self.actor_may_use(actor_id) or not result_id.startswith("sh:"):
            return
        await self.ensure_schema()
        query_hash = hashlib.sha256((query or "").encode("utf-8")).hexdigest()[:24]
        await self.store.db.execute(
            """INSERT INTO telegram_inline_usage(
                   telegram_user_id, result_id, result_kind, query_hash, chosen_at
               ) VALUES(?, ?, ?, ?, ?)""",
            (actor_id, result_id[:128], _kind_from_result_id(result_id), query_hash, time.time()),
        )
        await self.store.db.commit()


async def on_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    inline_query = update.inline_query
    if not inline_query:
        return
    service: InlineShareService = context.application.bot_data["inline_share_service"]
    actor_id = inline_query.from_user.id
    if not service.actor_may_use(actor_id):
        await inline_query.answer([], cache_time=1, is_personal=True)
        return

    try:
        results, next_offset = await service.page(
            actor_id=actor_id,
            raw_query=inline_query.query,
            offset=_parse_offset(inline_query.offset),
        )
        await inline_query.answer(
            results,
            cache_time=service.settings.telegram_inline_cache_seconds,
            is_personal=True,
            next_offset=next_offset,
        )
    except TelegramError:
        log.warning("Telegram rejected inline answer query_id=%s", inline_query.id, exc_info=True)
    except Exception:
        log.exception("inline query failed user_id=%s", actor_id)
        try:
            await inline_query.answer([], cache_time=1, is_personal=True)
        except TelegramError:
            pass


async def on_chosen_inline_result(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chosen = update.chosen_inline_result
    if not chosen:
        return
    service: InlineShareService = context.application.bot_data["inline_share_service"]
    await service.record_chosen(
        actor_id=chosen.from_user.id,
        result_id=chosen.result_id,
        query=chosen.query,
    )


def install_inline_handlers(application: Application, settings: Settings, store: Store) -> None:
    if not settings.telegram_inline_enabled:
        return
    application.bot_data["inline_share_service"] = InlineShareService(settings, store)
    application.add_handler(InlineQueryHandler(on_inline_query))
    application.add_handler(ChosenInlineResultHandler(on_chosen_inline_result))
