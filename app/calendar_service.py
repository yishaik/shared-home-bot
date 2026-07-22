from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
import uuid
from typing import Any

from app import google_client

log = logging.getLogger("homebot.calendar")

CALENDAR_SCHEMA = """
CREATE TABLE IF NOT EXISTS calendar_events (
    google_event_id TEXT PRIMARY KEY,
    calendar_id TEXT NOT NULL,
    etag TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'confirmed',
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    start_at TEXT,
    end_at TEXT,
    all_day INTEGER NOT NULL DEFAULT 0,
    timezone TEXT NOT NULL DEFAULT '',
    recurrence_json TEXT NOT NULL DEFAULT '[]',
    recurring_event_id TEXT,
    original_start_at TEXT,
    attendees_json TEXT NOT NULL DEFAULT '[]',
    reminders_json TEXT NOT NULL DEFAULT '{}',
    creator_email TEXT NOT NULL DEFAULT '',
    organizer_email TEXT NOT NULL DEFAULT '',
    html_link TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'google',
    google_updated_at TEXT,
    synced_at REAL NOT NULL,
    deleted_at REAL,
    raw_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_calendar_events_start ON calendar_events(start_at);
CREATE INDEX IF NOT EXISTS idx_calendar_events_status ON calendar_events(status);

CREATE TABLE IF NOT EXISTS calendar_sync_state (
    calendar_id TEXT PRIMARY KEY,
    sync_token TEXT NOT NULL DEFAULT '',
    last_full_sync_at REAL,
    last_incremental_sync_at REAL,
    last_error TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL
);
"""


def _event_time(value: dict[str, Any] | None) -> tuple[str | None, bool, str]:
    value = value or {}
    if value.get("dateTime"):
        return str(value["dateTime"]), False, str(value.get("timeZone") or "")
    if value.get("date"):
        return str(value["date"]), True, str(value.get("timeZone") or "")
    return None, False, ""


def normalize_google_event(event: dict[str, Any], calendar_id: str) -> dict[str, Any]:
    start_at, all_day, timezone = _event_time(event.get("start"))
    end_at, _, end_timezone = _event_time(event.get("end"))
    original_start, _, _ = _event_time(event.get("originalStartTime"))
    creator = event.get("creator") or {}
    organizer = event.get("organizer") or {}
    return {
        "id": str(event.get("id") or ""),
        "google_event_id": str(event.get("id") or ""),
        "calendar_id": calendar_id,
        "etag": str(event.get("etag") or ""),
        "status": str(event.get("status") or "confirmed"),
        "title": str(event.get("summary") or "(ללא כותרת)"),
        "description": str(event.get("description") or ""),
        "notes": str(event.get("description") or ""),
        "location": str(event.get("location") or ""),
        "start_at": start_at,
        "end_at": end_at,
        "all_day": bool(all_day),
        "timezone": timezone or end_timezone,
        "recurrence": list(event.get("recurrence") or []),
        "recurring_event_id": event.get("recurringEventId"),
        "original_start_at": original_start,
        "attendees": list(event.get("attendees") or []),
        "reminders": dict(event.get("reminders") or {}),
        "creator_email": str(creator.get("email") or ""),
        "organizer_email": str(organizer.get("email") or ""),
        "html_link": str(event.get("htmlLink") or ""),
        "google_updated_at": event.get("updated"),
        "source": "google",
        "sync_status": "synced",
        "raw": event,
    }


class CalendarService:
    def __init__(self, settings, store):
        self.settings = settings
        self.store = store
        self.calendar_id = settings.google_calendar_id
        self._schema_ready = False
        self._sync_lock = asyncio.Lock()

    async def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        await self.store.db.executescript(CALENDAR_SCHEMA)
        await self.store.db.commit()
        self._schema_ready = True

    async def _save_event(self, event: dict[str, Any]) -> dict[str, Any]:
        row = normalize_google_event(event, self.calendar_id)
        deleted_at = time.time() if row["status"] == "cancelled" else None
        await self.store.db.execute(
            """INSERT INTO calendar_events(
                google_event_id, calendar_id, etag, status, title, description, location,
                start_at, end_at, all_day, timezone, recurrence_json, recurring_event_id,
                original_start_at, attendees_json, reminders_json, creator_email,
                organizer_email, html_link, source, google_updated_at, synced_at,
                deleted_at, raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(google_event_id) DO UPDATE SET
                calendar_id=excluded.calendar_id, etag=excluded.etag, status=excluded.status,
                title=excluded.title, description=excluded.description, location=excluded.location,
                start_at=excluded.start_at, end_at=excluded.end_at, all_day=excluded.all_day,
                timezone=excluded.timezone, recurrence_json=excluded.recurrence_json,
                recurring_event_id=excluded.recurring_event_id,
                original_start_at=excluded.original_start_at,
                attendees_json=excluded.attendees_json, reminders_json=excluded.reminders_json,
                creator_email=excluded.creator_email, organizer_email=excluded.organizer_email,
                html_link=excluded.html_link, source=excluded.source,
                google_updated_at=excluded.google_updated_at, synced_at=excluded.synced_at,
                deleted_at=excluded.deleted_at, raw_json=excluded.raw_json""",
            (
                row["google_event_id"], row["calendar_id"], row["etag"], row["status"],
                row["title"], row["description"], row["location"], row["start_at"],
                row["end_at"], int(row["all_day"]), row["timezone"],
                json.dumps(row["recurrence"], ensure_ascii=False), row["recurring_event_id"],
                row["original_start_at"], json.dumps(row["attendees"], ensure_ascii=False),
                json.dumps(row["reminders"], ensure_ascii=False), row["creator_email"],
                row["organizer_email"], row["html_link"], row["source"],
                row["google_updated_at"], time.time(), deleted_at,
                json.dumps(row["raw"], ensure_ascii=False),
            ),
        )
        await self.store.db.commit()
        return row

    async def _sync_state(self) -> dict[str, Any]:
        await self.ensure_schema()
        row = await (await self.store.db.execute(
            "SELECT * FROM calendar_sync_state WHERE calendar_id=?", (self.calendar_id,)
        )).fetchone()
        return dict(row) if row else {}

    async def _set_sync_state(self, **changes: Any) -> None:
        current = await self._sync_state()
        values = {
            "sync_token": current.get("sync_token", ""),
            "last_full_sync_at": current.get("last_full_sync_at"),
            "last_incremental_sync_at": current.get("last_incremental_sync_at"),
            "last_error": current.get("last_error", ""),
        }
        values.update(changes)
        await self.store.db.execute(
            """INSERT INTO calendar_sync_state(calendar_id, sync_token, last_full_sync_at,
                   last_incremental_sync_at, last_error, updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(calendar_id) DO UPDATE SET
                   sync_token=excluded.sync_token,
                   last_full_sync_at=excluded.last_full_sync_at,
                   last_incremental_sync_at=excluded.last_incremental_sync_at,
                   last_error=excluded.last_error,
                   updated_at=excluded.updated_at""",
            (self.calendar_id, values["sync_token"], values["last_full_sync_at"],
             values["last_incremental_sync_at"], values["last_error"], time.time()),
        )
        await self.store.db.commit()

    async def full_sync(self) -> dict[str, Any]:
        await self.ensure_schema()
        async with self._sync_lock:
            now = dt.datetime.now(dt.timezone.utc)
            time_min = (now - dt.timedelta(days=90)).isoformat()
            time_max = (now + dt.timedelta(days=365)).isoformat()
            try:
                result = await asyncio.to_thread(
                    google_client.calendar_sync,
                    self.settings,
                    calendar_id=self.calendar_id,
                    time_min=time_min,
                    time_max=time_max,
                )
                for event in result["events"]:
                    await self._save_event(event)
                await self._set_sync_state(
                    sync_token=result.get("next_sync_token") or "",
                    last_full_sync_at=time.time(),
                    last_error="",
                )
                return {"ok": True, "mode": "full", "count": len(result["events"])}
            except Exception as exc:
                await self._set_sync_state(last_error=f"{type(exc).__name__}: {exc}")
                raise

    async def incremental_sync(self) -> dict[str, Any]:
        state = await self._sync_state()
        token = state.get("sync_token") or ""
        if not token:
            return await self.full_sync()

        reset_required = False
        async with self._sync_lock:
            try:
                result = await asyncio.to_thread(
                    google_client.calendar_sync,
                    self.settings,
                    calendar_id=self.calendar_id,
                    sync_token=token,
                )
                for event in result["events"]:
                    await self._save_event(event)
                await self._set_sync_state(
                    sync_token=result.get("next_sync_token") or token,
                    last_incremental_sync_at=time.time(),
                    last_error="",
                )
                return {"ok": True, "mode": "incremental", "count": len(result["events"])}
            except Exception as exc:
                if getattr(exc, "resp", None) is not None and getattr(exc.resp, "status", None) == 410:
                    await self._set_sync_state(sync_token="")
                    reset_required = True
                else:
                    await self._set_sync_state(last_error=f"{type(exc).__name__}: {exc}")
                    raise

        # Run outside the incremental lock; full_sync acquires the same lock.
        if reset_required:
            return await self.full_sync()
        raise RuntimeError("incremental sync ended without a result")

    async def list_events(self, *, include_cancelled: bool = False) -> list[dict[str, Any]]:
        await self.ensure_schema()
        state = await self._sync_state()
        stale = not state.get("last_full_sync_at") and not state.get("last_incremental_sync_at")
        if self.settings.google_enabled and stale:
            try:
                await self.full_sync()
            except Exception:
                log.exception("initial calendar sync failed; serving cache")
        where = "" if include_cancelled else "WHERE status!='cancelled'"
        rows = await (await self.store.db.execute(
            f"SELECT * FROM calendar_events {where} ORDER BY all_day DESC, start_at ASC LIMIT 500"
        )).fetchall()
        out: list[dict[str, Any]] = []
        for raw in rows:
            row = dict(raw)
            out.append({
                "id": row["google_event_id"],
                "google_event_id": row["google_event_id"],
                "title": row["title"],
                "description": row["description"],
                "notes": row["description"],
                "location": row["location"],
                "start_at": row["start_at"],
                "end_at": row["end_at"],
                "all_day": bool(row["all_day"]),
                "status": row["status"],
                "recurrence": json.loads(row["recurrence_json"] or "[]"),
                "recurring_event_id": row["recurring_event_id"],
                "attendees": json.loads(row["attendees_json"] or "[]"),
                "reminders": json.loads(row["reminders_json"] or "{}"),
                "html_link": row["html_link"],
                "sync_status": "synced",
                "source": row["source"],
            })
        return out

    async def get_event(self, event_id: str) -> dict[str, Any] | None:
        if self.settings.google_enabled:
            try:
                event = await asyncio.to_thread(
                    google_client.calendar_get, self.settings,
                    calendar_id=self.calendar_id, event_id=event_id,
                )
                return await self._save_event(event)
            except Exception:
                log.exception("calendar get failed; falling back to cache")
        rows = await self.list_events(include_cancelled=True)
        return next((event for event in rows if event["id"] == event_id), None)

    async def create_event(self, actor_id: int, **payload: Any) -> dict[str, Any]:
        if not self.settings.google_enabled:
            raise RuntimeError("Google Calendar is not configured")
        payload.setdefault("request_id", uuid.uuid4().hex)
        event = await asyncio.to_thread(
            google_client.calendar_create,
            self.settings,
            calendar_id=self.calendar_id,
            household_id=self.settings.household_id,
            **payload,
        )
        saved = await self._save_event(event)
        await self.store.add_activity(actor_id, "created", "event", saved["id"], f"נוסף אירוע: {saved['title']}")
        return saved

    async def update_event(self, actor_id: int, event_id: str, **changes: Any) -> dict[str, Any]:
        if not self.settings.google_enabled:
            raise RuntimeError("Google Calendar is not configured")
        event = await asyncio.to_thread(
            google_client.calendar_update,
            self.settings,
            calendar_id=self.calendar_id,
            event_id=event_id,
            changes=changes,
        )
        saved = await self._save_event(event)
        await self.store.add_activity(actor_id, "updated", "event", event_id, f"עודכן אירוע: {saved['title']}")
        return saved

    async def delete_event(self, actor_id: int, event_id: str) -> bool:
        if not self.settings.google_enabled:
            raise RuntimeError("Google Calendar is not configured")
        await asyncio.to_thread(
            google_client.calendar_delete,
            self.settings,
            calendar_id=self.calendar_id,
            event_id=event_id,
        )
        await self.store.db.execute(
            "UPDATE calendar_events SET status='cancelled', deleted_at=?, synced_at=? WHERE google_event_id=?",
            (time.time(), time.time(), event_id),
        )
        await self.store.db.commit()
        await self.store.add_activity(actor_id, "deleted", "event", event_id, "אירוע נמחק")
        return True

    async def status(self) -> dict[str, Any]:
        state = await self._sync_state()
        count = await (await self.store.db.execute(
            "SELECT COUNT(*) AS n FROM calendar_events WHERE status!='cancelled'"
        )).fetchone()
        return {
            "configured": bool(self.settings.google_enabled),
            "calendar_id": self.calendar_id,
            "cached_events": int(count["n"] if count else 0),
            "last_full_sync_at": state.get("last_full_sync_at"),
            "last_incremental_sync_at": state.get("last_incremental_sync_at"),
            "last_error": state.get("last_error", ""),
        }
