import json

from app.smart_inbox import MARKER_PREFIX, parse_marker, requires_confirmation


def test_simple_capture_stays_immediate():
    assert not requires_confirmation("shop_add", {"item": "חלב", "qty": "1"})
    assert not requires_confirmation("todo_add", {"title": "להוציא זבל"})
    assert not requires_confirmation(
        "remind_add",
        {"text": "להתקשר לרופא", "due_at": "2026-07-23T09:00:00+03:00", "target": "me"},
    )


def test_complex_or_destructive_actions_wait_for_approval():
    assert requires_confirmation(
        "todo_add",
        {"title": "להתקשר לרופא", "due_at": "2026-07-23T09:00:00+03:00"},
    )
    assert requires_confirmation(
        "todo_add",
        {"title": "להזמין אינסטלטור", "assigned_to": 123, "priority": "high"},
    )
    assert requires_confirmation(
        "event_add",
        {
            "title": "רופא",
            "start_at": "2026-07-23T09:00:00+03:00",
            "end_at": "2026-07-23T10:00:00+03:00",
        },
    )
    assert requires_confirmation(
        "remind_add",
        {
            "text": "להזמין תור",
            "due_at": "2026-07-23T09:00:00+03:00",
            "target_name": "ליסיה",
        },
    )
    assert requires_confirmation("event_delete", {"id": "event-123"})


def test_pending_marker_is_strictly_parsed():
    payload = {"id": "abc123", "summary": "📥 לפני ביצוע"}
    assert parse_marker(MARKER_PREFIX + json.dumps(payload, ensure_ascii=False)) == payload
    assert parse_marker("ordinary reply") is None
    assert parse_marker(MARKER_PREFIX + "not-json") is None
    assert parse_marker(MARKER_PREFIX + json.dumps({"id": "missing-summary"})) is None
