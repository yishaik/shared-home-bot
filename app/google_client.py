"""Google integration for the shared-home bot.

Auth model C: a dedicated bot Google account, authorized once via OAuth
(scripts/google_oauth.py or the /google/oauth bootstrap route). We keep only a
long-lived refresh token in env — every call mints a fresh access token, so the
runtime is fully headless.

All functions here are synchronous (googleapiclient is blocking); callers run
them in a worker thread via asyncio.to_thread so the event loop is never held.
Google libraries are imported lazily so a missing dependency never breaks boot.
"""

from __future__ import annotations

from typing import Any

# calendar: full R/W on the bot's own calendars · documents: R/W docs ·
# drive.file: manage ONLY files this app creates (least privilege, non-restricted).
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",
]
TOKEN_URI = "https://oauth2.googleapis.com/token"

_services: dict[tuple[str, str], Any] = {}


def _credentials(settings):
    from google.oauth2.credentials import Credentials

    return Credentials(
        token=None,
        refresh_token=settings.google_refresh_token,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        token_uri=TOKEN_URI,
        scopes=SCOPES,
    )


def _service(settings, name: str, version: str):
    key = (name, version)
    if key not in _services:
        from googleapiclient.discovery import build

        _services[key] = build(name, version, credentials=_credentials(settings), cache_discovery=False)
    return _services[key]


def reset_services() -> None:
    """Drop cached clients (call after credentials change)."""
    _services.clear()


# ── Calendar ──────────────────────────────────────────────────────────────
def calendar_add(settings, *, summary: str, start: str, end: str | None = None,
                 all_day: bool = False, location: str = "", description: str = "") -> dict[str, Any]:
    svc = _service(settings, "calendar", "v3")
    tz = settings.household_timezone
    if all_day:
        # start/end are YYYY-MM-DD; Google's end date is exclusive.
        body: dict[str, Any] = {"summary": summary, "start": {"date": start}, "end": {"date": end or start}}
    else:
        body = {
            "summary": summary,
            "start": {"dateTime": start, "timeZone": tz},
            "end": {"dateTime": end or start, "timeZone": tz},
        }
    if location:
        body["location"] = location
    if description:
        body["description"] = description
    ev = svc.events().insert(calendarId=settings.google_calendar_id, body=body).execute()
    return {"id": ev.get("id"), "htmlLink": ev.get("htmlLink"), "summary": ev.get("summary"),
            "start": ev.get("start"), "end": ev.get("end")}


def calendar_list(settings, *, time_min: str | None = None, time_max: str | None = None, max_results: int = 10) -> list[dict[str, Any]]:
    import datetime as _dt

    svc = _service(settings, "calendar", "v3")
    if not time_min:
        time_min = _dt.datetime.now(_dt.timezone.utc).isoformat()
    params: dict[str, Any] = {
        "calendarId": settings.google_calendar_id,
        "timeMin": time_min,
        "singleEvents": True,
        "orderBy": "startTime",
        "maxResults": max(1, min(int(max_results or 10), 50)),
    }
    if time_max:
        params["timeMax"] = time_max
    items = svc.events().list(**params).execute().get("items", [])
    out = []
    for ev in items:
        start = ev.get("start", {})
        out.append({
            "id": ev.get("id"),
            "summary": ev.get("summary", "(ללא כותרת)"),
            "start": start.get("dateTime") or start.get("date"),
            "location": ev.get("location", ""),
            "htmlLink": ev.get("htmlLink"),
        })
    return out


def calendar_delete(settings, *, event_id: str) -> bool:
    svc = _service(settings, "calendar", "v3")
    svc.events().delete(calendarId=settings.google_calendar_id, eventId=event_id).execute()
    return True


# ── Docs ──────────────────────────────────────────────────────────────────
def _doc_url(doc_id: str) -> str:
    return f"https://docs.google.com/document/d/{doc_id}/edit"


def doc_create(settings, *, title: str, text: str = "") -> dict[str, Any]:
    docs = _service(settings, "docs", "v1")
    doc = docs.documents().create(body={"title": title}).execute()
    doc_id = doc["documentId"]
    if text:
        docs.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": [{"insertText": {"location": {"index": 1}, "text": text}}]},
        ).execute()
    if settings.google_docs_folder_id:
        drive = _service(settings, "drive", "v3")
        drive.files().update(fileId=doc_id, addParents=settings.google_docs_folder_id,
                             fields="id, parents").execute()
    return {"id": doc_id, "title": title, "url": _doc_url(doc_id)}


def _doc_end_index(docs, doc_id: str) -> int:
    doc = docs.documents().get(documentId=doc_id).execute()
    content = doc.get("body", {}).get("content", [])
    return content[-1]["endIndex"] if content else 1


def doc_append(settings, *, doc_id: str, text: str) -> dict[str, Any]:
    docs = _service(settings, "docs", "v1")
    end = _doc_end_index(docs, doc_id)
    docs.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": [{"insertText": {"location": {"index": max(1, end - 1)}, "text": ("\n" + text)}}]},
    ).execute()
    return {"id": doc_id, "url": _doc_url(doc_id), "appended_chars": len(text)}


def doc_read(settings, *, doc_id: str, max_chars: int = 6000) -> dict[str, Any]:
    docs = _service(settings, "docs", "v1")
    doc = docs.documents().get(documentId=doc_id).execute()
    parts: list[str] = []
    for element in doc.get("body", {}).get("content", []):
        para = element.get("paragraph")
        if not para:
            continue
        for run in para.get("elements", []):
            txt = run.get("textRun", {}).get("content")
            if txt:
                parts.append(txt)
    text = "".join(parts).strip()
    return {"id": doc_id, "title": doc.get("title", ""), "url": _doc_url(doc_id),
            "text": text[:max_chars], "truncated": len(text) > max_chars}


def doc_list(settings, *, max_results: int = 20) -> list[dict[str, Any]]:
    drive = _service(settings, "drive", "v3")
    q = "mimeType='application/vnd.google-apps.document' and trashed=false"
    if settings.google_docs_folder_id:
        q += f" and '{settings.google_docs_folder_id}' in parents"
    res = drive.files().list(
        q=q, orderBy="modifiedTime desc", pageSize=max(1, min(int(max_results or 20), 50)),
        fields="files(id, name, modifiedTime, webViewLink)",
    ).execute()
    return [{"id": f["id"], "title": f.get("name", ""), "modified": f.get("modifiedTime"),
             "url": f.get("webViewLink") or _doc_url(f["id"])} for f in res.get("files", [])]
