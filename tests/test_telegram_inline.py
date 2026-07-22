from __future__ import annotations

import pytest

from app.config import Settings
from app.share_policy import SharePolicy
from app.share_renderers import event_card
from app.telegram_inline import InlineShareService, parse_inline_intent


class FakeStore:
    async def list_todos(self, include_done=False):
        assert include_done is False
        return [
            {"id": 1, "title": "להזמין אינסטלטור", "priority": "high", "due_at": "2026-07-25T10:00:00+03:00"},
            {"id": 2, "title": "לקפל כביסה", "priority": "normal"},
            {"id": 3, "title": "לקנות פילטר", "priority": "low"},
        ]

    async def shop_list(self, include_done=False):
        assert include_done is False
        return [
            {"id": 10, "item": "חלב", "qty": "2", "category": "מקרר"},
            {"id": 11, "item": "לחם", "qty": "1", "category": "מאפייה"},
        ]


class FakeCalendar:
    async def list_events(self):
        return [
            {
                "id": "event-1",
                "title": "ארוחת שישי",
                "start_at": "2026-07-24T19:30:00+03:00",
                "location": "כתובת פרטית 12",
                "description": "קוד כניסה סודי",
                "attendees": [{"email": "private@example.com"}],
                "status": "confirmed",
            },
            {
                "id": "event-2",
                "title": "אירוע שבוטל",
                "start_at": "2026-07-25T20:00:00+03:00",
                "status": "cancelled",
            },
        ]


def settings(**overrides):
    values = {
        "_env_file": None,
        "ALLOWED_USER_IDS": "10",
        "TELEGRAM_INLINE_ENABLED": True,
        "TELEGRAM_INLINE_MAX_RESULTS": 2,
        "HOUSEHOLD_TIMEZONE": "Asia/Jerusalem",
    }
    values.update(overrides)
    return Settings(**values)


def test_inline_intent_parser_supports_hebrew_and_generic_search() -> None:
    assert parse_inline_intent("קניות חלב").kind == "shopping"
    assert parse_inline_intent("קניות חלב").term == "חלב"
    assert parse_inline_intent("tasks plumber").kind == "todo"
    assert parse_inline_intent("ארוחת שישי").kind is None
    assert parse_inline_intent("ארוחת שישי").term == "ארוחת שישי"


def test_share_policy_blocks_private_kinds_on_external_surfaces() -> None:
    policy = SharePolicy(settings())

    assert policy.may_share(actor_id=10, kind="todo", surface="inline") is True
    assert policy.may_share(actor_id=10, kind="memory", surface="inline") is False
    assert policy.may_share(actor_id=999, kind="todo", surface="inline") is False


def test_event_renderer_omits_sensitive_fields() -> None:
    card = event_card(
        {
            "id": "event-private",
            "title": "פגישה",
            "start_at": "2026-07-24T19:30:00+03:00",
            "location": "רחוב סודי 5",
            "description": "קוד 1234",
            "attendees": [{"email": "person@example.com"}],
        },
        "Asia/Jerusalem",
    )

    rendered = card.message_html + card.description + card.search_text
    assert "פגישה" in rendered
    assert "רחוב סודי" not in rendered
    assert "1234" not in rendered
    assert "person@example.com" not in rendered


@pytest.mark.asyncio
async def test_inline_service_filters_by_domain_and_term() -> None:
    service = InlineShareService(settings(), FakeStore(), calendar=FakeCalendar())

    cards = await service.cards(10, "קניות חלב")

    assert [card.kind for card in cards] == ["shopping"]
    assert cards[0].entity_id == "10"
    assert "חלב" in cards[0].message_html


@pytest.mark.asyncio
async def test_inline_service_is_fail_closed_for_unknown_users() -> None:
    service = InlineShareService(settings(), FakeStore(), calendar=FakeCalendar())

    assert await service.cards(999, "") == []


@pytest.mark.asyncio
async def test_inline_results_are_personal_and_paginated_by_service_limit() -> None:
    service = InlineShareService(settings(TELEGRAM_INLINE_MAX_RESULTS=2), FakeStore(), calendar=FakeCalendar())

    first_page, next_offset = await service.page(actor_id=10, raw_query="משימות", offset=0)
    second_page, final_offset = await service.page(
        actor_id=10, raw_query="משימות", offset=int(next_offset)
    )

    assert len(first_page) == 2
    assert next_offset == "2"
    assert len(second_page) == 1
    assert final_offset == ""
    assert all(result.id.startswith("sh:todo:") for result in [*first_page, *second_page])


def test_inline_configuration_is_clamped_to_telegram_limits() -> None:
    configured = settings(
        TELEGRAM_INLINE_MAX_RESULTS=500,
        TELEGRAM_INLINE_CACHE_SECONDS=-1,
        TELEGRAM_INLINE_USAGE_RETENTION_DAYS=1000,
    )

    assert configured.telegram_inline_max_results == 50
    assert configured.telegram_inline_cache_seconds == 0
    assert configured.telegram_inline_usage_retention_days == 365
