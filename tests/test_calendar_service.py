from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.calendar_service import CalendarService, normalize_google_event
from app.store_v2 import Store


def settings(**overrides):
    values = {
        "google_calendar_id": "primary",
        "google_enabled": False,
        "household_id": "primary",
        "household_timezone": "Asia/Jerusalem",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_normalize_google_event() -> None:
    row = normalize_google_event(
        {
            "id": "abc",
            "etag": '"v1"',
            "status": "confirmed",
            "summary": "פגישה",
            "description": "פרטים",
            "location": "תל אביב",
            "start": {"dateTime": "2026-07-25T10:00:00+03:00", "timeZone": "Asia/Jerusalem"},
            "end": {"dateTime": "2026-07-25T11:00:00+03:00", "timeZone": "Asia/Jerusalem"},
            "attendees": [{"email": "a@example.com"}],
            "htmlLink": "https://calendar.google.com/event?eid=abc",
        },
        "primary",
    )
    assert row["id"] == "abc"
    assert row["title"] == "פגישה"
    assert row["all_day"] is False
    assert row["start_at"].endswith("+03:00")
    assert row["attendees"][0]["email"] == "a@example.com"


def test_normalize_all_day_event() -> None:
    row = normalize_google_event(
        {"id": "day", "summary": "חופשה", "start": {"date": "2026-08-01"}, "end": {"date": "2026-08-02"}},
        "primary",
    )
    assert row["all_day"] is True
    assert row["start_at"] == "2026-08-01"
    assert row["end_at"] == "2026-08-02"


@pytest.mark.asyncio
async def test_calendar_cache_and_status(tmp_path: Path) -> None:
    store = Store(tmp_path / "home.db")
    await store.connect()
    await store.bootstrap_household("Home", "Asia/Jerusalem", [1])
    calendar = CalendarService(settings(), store)
    await calendar.ensure_schema()
    await calendar._save_event({
        "id": "cached-1",
        "status": "confirmed",
        "summary": "אירוע שמור",
        "start": {"dateTime": "2026-07-26T09:00:00+03:00"},
        "end": {"dateTime": "2026-07-26T10:00:00+03:00"},
    })

    events = await calendar.list_events()
    status = await calendar.status()
    assert [event["id"] for event in events] == ["cached-1"]
    assert status["cached_events"] == 1
    assert status["configured"] is False
    await store.close()
