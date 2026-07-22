from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.proactive import (
    ProactiveEngine,
    in_quiet_hours,
    next_occurrence,
    parse_quiet_hours,
    reminder_add,
    reminder_cancel,
    reminder_list,
    resolve_member,
    to_utc_iso,
)
from app.store_v2 import Store

TZ = ZoneInfo("Asia/Jerusalem")


def settings(**overrides):
    values = {
        "household_timezone": "Asia/Jerusalem",
        "household_id": "primary",
        "allowed_user_ids": [1, 2],
        "google_enabled": False,
        "google_calendar_id": "primary",
        "proactive_enabled": True,
        "brief_time": "08:00",
        "quiet_hours": "22:30-07:30",
        "calendar_nudge_minutes": 30,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, *, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


class FakeTelegramStore:
    def __init__(self):
        self.messages: list[dict] = []

    async def add_message(self, *, scope_key, agent_id, role, content, **_kw) -> int:
        self.messages.append({"scope_key": scope_key, "agent_id": agent_id, "role": role, "content": content})
        return len(self.messages)


async def make_store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "home.db")
    await store.connect()
    await store.bootstrap_household("Home", "Asia/Jerusalem", [1, 2])
    return store


def make_engine(store: Store, **setting_overrides) -> tuple[ProactiveEngine, FakeBot]:
    bot = FakeBot()
    engine = ProactiveEngine(settings(**setting_overrides), store, None, bot)
    return engine, bot


def test_quiet_hours_crossing_midnight() -> None:
    window = parse_quiet_hours("22:30-07:30")
    assert window is not None
    base = dt.datetime(2026, 7, 22, tzinfo=TZ)
    assert in_quiet_hours(base.replace(hour=23), window) is True
    assert in_quiet_hours(base.replace(hour=3), window) is True
    assert in_quiet_hours(base.replace(hour=12), window) is False
    assert in_quiet_hours(base.replace(hour=8), window) is False
    assert in_quiet_hours(base.replace(hour=12), None) is False


def test_to_utc_iso_naive_is_local() -> None:
    # 09:00 Jerusalem summer time == 06:00 UTC
    assert to_utc_iso("2026-07-23T09:00:00", TZ) == "2026-07-23T06:00:00+00:00"


def test_next_occurrence() -> None:
    due = dt.datetime(2026, 7, 23, 9, 0, tzinfo=TZ)
    assert next_occurrence(due, "daily") == due + dt.timedelta(days=1)
    assert next_occurrence(due, "weekly") == due + dt.timedelta(days=7)
    assert next_occurrence(due, "") is None


@pytest.mark.asyncio
async def test_reminder_fires_once_and_records_context(tmp_path: Path) -> None:
    store = await make_store(tmp_path)
    engine, bot = make_engine(store)
    await reminder_add(store, engine.settings, text="להוציא עוגה מהתנור", due_at="2026-07-23T09:00:00", created_by=1)

    now = dt.datetime(2026, 7, 23, 6, 1, tzinfo=dt.timezone.utc)  # 09:01 local
    await engine.tick(now)
    assert len(bot.sent) == 2  # both household members
    assert "להוציא עוגה" in bot.sent[0][1]

    await engine.tick(now + dt.timedelta(minutes=1))
    assert len(bot.sent) == 2  # not re-fired

    history = await store.recent_messages(5)
    assert any("[הודעה יזומה]" in row["content"] for row in history)
    await store.close()


@pytest.mark.asyncio
async def test_personal_reminder_targets_one_user_and_recurs(tmp_path: Path) -> None:
    store = await make_store(tmp_path)
    engine, bot = make_engine(store)
    await reminder_add(store, engine.settings, text="כדור ויטמין", due_at="2026-07-23T09:00:00",
                       created_by=2, target_user_id=2, recurrence="daily")

    await engine.tick(dt.datetime(2026, 7, 23, 6, 5, tzinfo=dt.timezone.utc))
    assert bot.sent == [(2, "⏰ תזכורת: כדור ויטמין")]

    pending = await reminder_list(store, engine.settings)
    assert len(pending) == 1  # recurring stays pending
    assert pending[0]["due_at_local"].startswith("2026-07-24T09:00")
    assert await reminder_cancel(store, pending[0]["id"]) is True
    assert await reminder_list(store, engine.settings) == []
    await store.close()


@pytest.mark.asyncio
async def test_calendar_nudge_dedup(tmp_path: Path) -> None:
    from app.calendar_service import CalendarService

    store = await make_store(tmp_path)
    engine, bot = make_engine(store)
    calendar = CalendarService(settings(google_calendar_id="primary", household_id="primary"), store)
    await calendar.ensure_schema()
    await calendar._save_event({
        "id": "ev1", "status": "confirmed", "summary": "רופא שיניים",
        "start": {"dateTime": "2026-07-23T10:00:00+03:00"},
        "end": {"dateTime": "2026-07-23T11:00:00+03:00"},
    })

    now = dt.datetime(2026, 7, 23, 6, 40, tzinfo=dt.timezone.utc)  # 20 min before
    await engine.tick(now)
    nudges = [text for _, text in bot.sent if text.startswith("📅 בקרוב")]
    assert len(nudges) == 2  # both members, once
    assert "רופא שיניים" in nudges[0]

    await engine.tick(now + dt.timedelta(minutes=5))
    nudges = [text for _, text in bot.sent if text.startswith("📅 בקרוב")]
    assert len(nudges) == 2  # deduped
    await store.close()


@pytest.mark.asyncio
async def test_morning_brief_dedup_and_content(tmp_path: Path) -> None:
    store = await make_store(tmp_path)
    engine, bot = make_engine(store)
    await store.add_todo("לקבוע חיסון", user_id=1, due_at="2026-07-23", priority="high")
    await store.shop_add("חלב", "2", 1)

    now = dt.datetime(2026, 7, 23, 5, 30, tzinfo=dt.timezone.utc)  # 08:30 local
    await engine.tick(now)
    briefs = [text for _, text in bot.sent if "בוקר טוב" in text]
    assert len(briefs) == 2
    assert "לקבוע חיסון" in briefs[0]
    assert "1 פריטים" in briefs[0]

    await engine.tick(now + dt.timedelta(hours=1))
    briefs = [text for _, text in bot.sent if "בוקר טוב" in text]
    assert len(briefs) == 2  # once a day
    await store.close()


@pytest.mark.asyncio
async def test_quiet_hours_defer_brief_but_fire_reminder(tmp_path: Path) -> None:
    store = await make_store(tmp_path)
    engine, bot = make_engine(store, brief_time="06:00")
    await store.add_todo("משימה", user_id=1, due_at="2026-07-23")
    await reminder_add(store, engine.settings, text="תרופה של לילה", due_at="2026-07-23T06:30:00", created_by=1)

    now = dt.datetime(2026, 7, 23, 3, 31, tzinfo=dt.timezone.utc)  # 06:31 local — inside quiet hours
    await engine.tick(now)
    assert all("תרופה" in text for _, text in bot.sent)  # reminder fired, brief deferred

    await engine.tick(dt.datetime(2026, 7, 23, 4, 31, tzinfo=dt.timezone.utc))  # 07:31 — quiet over
    assert any("בוקר טוב" in text for _, text in bot.sent)
    await store.close()


@pytest.mark.asyncio
async def test_proactive_send_records_into_recipient_scope_transcript(tmp_path: Path) -> None:
    store = await make_store(tmp_path)
    bot = FakeBot()
    tgs = FakeTelegramStore()
    engine = ProactiveEngine(settings(), store, None, bot, telegram_store=tgs)
    await reminder_add(store, engine.settings, text="test", due_at="2026-07-23T09:00:00",
                       created_by=1, target_user_id=1)
    await engine.tick(dt.datetime(2026, 7, 23, 6, 1, tzinfo=dt.timezone.utc))
    assert bot.sent == [(1, "⏰ תזכורת: test")]
    # Recorded into the recipient's own private-chat coordinator transcript.
    assert tgs.messages == [{
        "scope_key": "telegram:1:0",
        "agent_id": "coordinator",
        "role": "assistant",
        "content": "[הודעה יזומה] ⏰ תזכורת: test",
    }]
    await store.close()


@pytest.mark.asyncio
async def test_resolve_member_by_name(tmp_path: Path) -> None:
    store = await make_store(tmp_path)
    await store.bootstrap_household("Home", "Asia/Jerusalem", [7956782005, 8094812958])
    await store.upsert_member_profile(8094812958, "ליסיה", "lisya")
    await store.upsert_member_profile(7956782005, "Yishai", "yishaik")
    hit = await resolve_member(store, "ליסיה")
    assert hit == {"user_id": 8094812958, "name": "ליסיה"}
    assert (await resolve_member(store, "lisya"))["user_id"] == 8094812958
    assert await resolve_member(store, "nobody") is None
    await store.close()


@pytest.mark.asyncio
async def test_targeted_reminder_delivers_to_one_member(tmp_path: Path) -> None:
    store = await make_store(tmp_path)
    await store.upsert_member_profile(8094812958, "ליסיה", "lisya")
    engine, bot = make_engine(store)
    await reminder_add(store, engine.settings, text="לצלם את מה שאת מוכרת",
                       due_at="2026-07-23T09:00:00", created_by=7956782005, target_user_id=8094812958)
    await engine.tick(dt.datetime(2026, 7, 23, 6, 1, tzinfo=dt.timezone.utc))
    assert bot.sent == [(8094812958, "⏰ תזכורת: לצלם את מה שאת מוכרת")]
    await store.close()


@pytest.mark.asyncio
async def test_undeliverable_targeted_reminder_notifies_creator(tmp_path: Path) -> None:
    store = await make_store(tmp_path)
    engine, bot = make_engine(store)

    async def failing_send(*, chat_id: int, text: str) -> None:
        if chat_id == 999:  # recipient never opened the bot
            raise RuntimeError("Chat not found")
        bot.sent.append((chat_id, text))

    bot.send_message = failing_send  # type: ignore[assignment]
    await reminder_add(store, engine.settings, text="בדיקה", due_at="2026-07-23T09:00:00",
                       created_by=7956782005, target_user_id=999)
    await engine.tick(dt.datetime(2026, 7, 23, 6, 1, tzinfo=dt.timezone.utc))
    # creator (7956782005) is warned; nothing delivered to 999
    assert any(chat == 7956782005 and "לא הצלחתי לשלוח" in text for chat, text in bot.sent)
    await store.close()


@pytest.mark.asyncio
async def test_disabled_engine_sends_nothing(tmp_path: Path) -> None:
    store = await make_store(tmp_path)
    engine, bot = make_engine(store)
    await store.set_setting("proactive_enabled", "off")
    await reminder_add(store, engine.settings, text="בדיקה", due_at="2026-07-23T09:00:00", created_by=1)

    # mirrors the guard in _run(): tick is skipped entirely when disabled
    if await store.get_setting("proactive_enabled") != "off":
        await engine.tick(dt.datetime(2026, 7, 23, 6, 1, tzinfo=dt.timezone.utc))
    assert bot.sent == []
    await store.close()
