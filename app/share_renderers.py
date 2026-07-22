from __future__ import annotations

import datetime as dt
import hashlib
import html
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class ShareCard:
    result_id: str
    kind: str
    entity_id: str
    title: str
    description: str
    message_html: str
    search_text: str
    sort_key: str = ""


def _clean(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _stable_result_id(kind: str, entity_id: str, revision: str = "") -> str:
    payload = f"{kind}:{entity_id}:{revision}".encode("utf-8", errors="ignore")
    digest = hashlib.blake2s(payload, digest_size=10).hexdigest()
    return f"sh:{kind}:{digest}"


def _parse_datetime(value: Any, timezone_name: str) -> dt.datetime | None:
    raw = _clean(value, 100)
    if not raw:
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = dt.datetime.combine(dt.date.fromisoformat(raw), dt.time.min)
        except ValueError:
            return None
    timezone = ZoneInfo(timezone_name)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def _format_datetime(value: Any, timezone_name: str, *, all_day: bool = False) -> str:
    parsed = _parse_datetime(value, timezone_name)
    if not parsed:
        return ""
    if all_day:
        return parsed.strftime("%d.%m.%Y")
    return parsed.strftime("%d.%m.%Y · %H:%M")


def todo_card(row: dict[str, Any], timezone_name: str) -> ShareCard:
    entity_id = _clean(row.get("id"), 80) or "unknown"
    title = _clean(row.get("title"), 220) or "משימה ללא כותרת"
    due = _format_datetime(row.get("due_at"), timezone_name)
    priority = _clean(row.get("priority"), 20).lower()
    priority_text = {"high": "דחיפות גבוהה", "low": "דחיפות נמוכה"}.get(priority, "")
    metadata = " · ".join(part for part in (due, priority_text) if part)
    message_lines = ["✅ <b>משימה לבית</b>", html.escape(title)]
    if due:
        message_lines.append(f"🕒 {html.escape(due)}")
    if priority_text:
        message_lines.append(f"⚑ {html.escape(priority_text)}")
    revision = _clean(row.get("updated_at") or row.get("created_at") or row.get("due_at"), 100)
    return ShareCard(
        result_id=_stable_result_id("todo", entity_id, revision),
        kind="todo",
        entity_id=entity_id,
        title=f"משימה: {title}"[:256],
        description=metadata or "משימה פתוחה בבית",
        message_html="\n".join(message_lines),
        search_text=f"{title} {metadata}".casefold(),
        sort_key=due or "9999",
    )


def shopping_card(row: dict[str, Any], timezone_name: str) -> ShareCard:
    del timezone_name
    entity_id = _clean(row.get("id"), 80) or "unknown"
    item = _clean(row.get("item"), 220) or "פריט ללא שם"
    quantity = _clean(row.get("qty"), 40) or "1"
    category = _clean(row.get("category"), 80)
    description = f"כמות: {quantity}" + (f" · {category}" if category else "")
    message_lines = ["🛒 <b>פריט לקניות</b>", f"{html.escape(item)} × {html.escape(quantity)}"]
    if category:
        message_lines.append(f"קטגוריה: {html.escape(category)}")
    revision = _clean(row.get("updated_at") or row.get("created_at") or quantity, 100)
    return ShareCard(
        result_id=_stable_result_id("shopping", entity_id, revision),
        kind="shopping",
        entity_id=entity_id,
        title=f"קניות: {item}"[:256],
        description=description[:512],
        message_html="\n".join(message_lines),
        search_text=f"{item} {quantity} {category}".casefold(),
        sort_key=category.casefold(),
    )


def event_card(row: dict[str, Any], timezone_name: str) -> ShareCard:
    entity_id = _clean(row.get("id") or row.get("google_event_id"), 240) or "unknown"
    title = _clean(row.get("title") or row.get("summary"), 220) or "אירוע ללא כותרת"
    all_day = bool(row.get("all_day"))
    start_at = row.get("start_at") or row.get("start") or row.get("when_text")
    when = _format_datetime(start_at, timezone_name, all_day=all_day)
    message_lines = ["📅 <b>אירוע</b>", html.escape(title)]
    if when:
        message_lines.append(f"🕒 {html.escape(when)}")
    # Location, description, attendees and organizer data are intentionally not
    # included. Inline results can be posted into chats outside the household.
    revision = _clean(row.get("google_updated_at") or row.get("updated_at") or start_at, 100)
    return ShareCard(
        result_id=_stable_result_id("event", entity_id, revision),
        kind="event",
        entity_id=entity_id,
        title=f"אירוע: {title}"[:256],
        description=(when or "אירוע משותף")[:512],
        message_html="\n".join(message_lines),
        search_text=f"{title} {when}".casefold(),
        sort_key=when or "9999",
    )


def help_card() -> ShareCard:
    return ShareCard(
        result_id="sh:help:v1",
        kind="help",
        entity_id="help",
        title="איך משתמשים ב־Shared Home Inline",
        description="חפש קניות, משימות או אירועים מתוך כל צ׳אט",
        message_html=(
            "🏠 <b>Shared Home Bot</b>\n"
            "אפשר לחפש ולשתף מידע תפעולי בטוח מהבית:\n"
            "• <code>קניות חלב</code>\n"
            "• <code>משימות אינסטלטור</code>\n"
            "• <code>אירועים שישי</code>"
        ),
        search_text="help עזרה inline קניות משימות אירועים",
        sort_key="zzzz",
    )
