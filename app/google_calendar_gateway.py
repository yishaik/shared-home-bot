from __future__ import annotations

import datetime as dt
from typing import Any

from app.google_client import _service


def _calendar(settings):
    return _service(settings, "calendar", "v3")


def _all_day_end(start: str, end: str | None) -> str:
    if end and end > start:
        return end
    day = dt.date.fromisoformat(start)
    return (day + dt.timedelta(days=1)).isoformat()


def _event_body(
    settings,
    *,
    title: str,
    start_at: str,
    end_at: str | None = None,
    all_day: bool = False,
    location: str = "",
    description: str = "",
    attendees: list[str] | None = None,
    recurrence: list[str] | None = None,
    reminders: dict[str, Any] | None = None,
    household_id: str = "",
    request_id: str = "",
) -> dict[str, Any]:
    if all_day:
        body: dict[str, Any] = {
            "summary": title,
            "start": {"date": start_at},
            "end": {"date": _all_day_end(start_at, end_at)},
        }
    else:
        body = {
            "summary": title,
            "start": {"dateTime": start_at, "timeZone": settings.household_timezone},
            "end": {"dateTime": end_at or start_at, "timeZone": settings.household_timezone},
        }
    if location:
        body["location"] = location
    if description:
        body["description"] = description
    if attendees:
        body["attendees"] = [{"email": email} for email in attendees if email]
    if recurrence:
        body["recurrence"] = recurrence
    if reminders:
        body["reminders"] = reminders
    body["extendedProperties"] = {
        "private": {
            "sharedHome": "true",
            "householdId": household_id,
            "requestId": request_id,
        }
    }
    return body


def calendar_create(
    settings,
    *,
    calendar_id: str,
    household_id: str,
    title: str,
    start_at: str,
    end_at: str | None = None,
    all_day: bool = False,
    location: str = "",
    description: str = "",
    attendees: list[str] | None = None,
    recurrence: list[str] | None = None,
    reminders: dict[str, Any] | None = None,
    request_id: str = "",
) -> dict[str, Any]:
    body = _event_body(
        settings,
        title=title,
        start_at=start_at,
        end_at=end_at,
        all_day=all_day,
        location=location,
        description=description,
        attendees=attendees,
        recurrence=recurrence,
        reminders=reminders,
        household_id=household_id,
        request_id=request_id,
    )
    return _calendar(settings).events().insert(
        calendarId=calendar_id,
        body=body,
        sendUpdates="all" if attendees else "none",
    ).execute()


def calendar_get(settings, *, calendar_id: str, event_id: str) -> dict[str, Any]:
    return _calendar(settings).events().get(calendarId=calendar_id, eventId=event_id).execute()


def calendar_update(
    settings,
    *,
    calendar_id: str,
    event_id: str,
    changes: dict[str, Any],
) -> dict[str, Any]:
    current = calendar_get(settings, calendar_id=calendar_id, event_id=event_id)
    title = str(changes.get("title", current.get("summary") or "(ללא כותרת)"))
    current_start = current.get("start") or {}
    current_end = current.get("end") or {}
    all_day = bool(changes.get("all_day", "date" in current_start))
    start_at = str(changes.get("start_at") or current_start.get("dateTime") or current_start.get("date") or "")
    end_at = changes.get("end_at") or current_end.get("dateTime") or current_end.get("date")
    attendees = changes.get("attendees")
    if attendees is None:
        attendees = [item.get("email", "") for item in current.get("attendees", [])]
    body = _event_body(
        settings,
        title=title,
        start_at=start_at,
        end_at=end_at,
        all_day=all_day,
        location=str(changes.get("location", current.get("location") or "")),
        description=str(changes.get("description", changes.get("notes", current.get("description") or ""))),
        attendees=attendees,
        recurrence=changes.get("recurrence", current.get("recurrence")),
        reminders=changes.get("reminders", current.get("reminders")),
        household_id=settings.household_id,
        request_id=str((current.get("extendedProperties") or {}).get("private", {}).get("requestId") or ""),
    )
    body["extendedProperties"] = current.get("extendedProperties", body.get("extendedProperties"))
    return _calendar(settings).events().update(
        calendarId=calendar_id,
        eventId=event_id,
        body=body,
        sendUpdates="all" if attendees else "none",
    ).execute()


def calendar_delete(settings, *, calendar_id: str, event_id: str) -> bool:
    _calendar(settings).events().delete(
        calendarId=calendar_id,
        eventId=event_id,
        sendUpdates="all",
    ).execute()
    return True


def calendar_sync(
    settings,
    *,
    calendar_id: str,
    sync_token: str | None = None,
    time_min: str | None = None,
    time_max: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "calendarId": calendar_id,
        "showDeleted": True,
        "singleEvents": True,
        "maxResults": 250,
    }
    if sync_token:
        params["syncToken"] = sync_token
    else:
        params["timeMin"] = time_min
        params["timeMax"] = time_max
        params["orderBy"] = "startTime"

    events: list[dict[str, Any]] = []
    page_token: str | None = None
    next_sync_token = ""
    while True:
        if page_token:
            params["pageToken"] = page_token
        response = _calendar(settings).events().list(**params).execute()
        events.extend(response.get("items", []))
        page_token = response.get("nextPageToken")
        next_sync_token = response.get("nextSyncToken") or next_sync_token
        if not page_token:
            break
    return {"events": events, "next_sync_token": next_sync_token}
