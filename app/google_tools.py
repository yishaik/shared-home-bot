"""Google tool specs + dispatch, exposed to the agent only when google_enabled.

Each tool runs the blocking google_client call in a worker thread. Errors are
returned as JSON (never raised) so one failed call can't crash the agent loop.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app import google_client as gc


def _tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    params: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        params["required"] = required
    return {"type": "function", "function": {"name": name, "description": description, "parameters": params}}


GOOGLE_TOOL_SPECS: list[dict[str, Any]] = [
    _tool("gcal_add_event", "Add an event to the shared Google Calendar. Use ISO 8601 for start/end (e.g. 2026-07-25T19:00:00). For a full-day event set all_day=true and pass dates as YYYY-MM-DD.",
          {"summary": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"},
           "all_day": {"type": "boolean", "default": False}, "location": {"type": "string"}, "description": {"type": "string"}},
          ["summary", "start"]),
    _tool("gcal_list_events", "List upcoming events from the shared Google Calendar.",
          {"time_min": {"type": "string", "description": "ISO 8601 lower bound; defaults to now"},
           "time_max": {"type": "string"}, "max_results": {"type": "integer", "default": 10}}),
    _tool("gcal_delete_event", "Delete a Google Calendar event by its id (get the id from gcal_list_events).",
          {"event_id": {"type": "string"}}, ["event_id"]),
    _tool("gdoc_create", "Create a new Google Doc with an optional initial body. Returns its shareable URL.",
          {"title": {"type": "string"}, "text": {"type": "string"}}, ["title"]),
    _tool("gdoc_append", "Append text to the end of an existing Google Doc (get the id from gdoc_list).",
          {"doc_id": {"type": "string"}, "text": {"type": "string"}}, ["doc_id", "text"]),
    _tool("gdoc_read", "Read the text content of a Google Doc by id.",
          {"doc_id": {"type": "string"}}, ["doc_id"]),
    _tool("gdoc_list", "List Google Docs the bot manages (most recently modified first).",
          {"max_results": {"type": "integer", "default": 20}}),
]

GOOGLE_TOOL_NAMES = {t["function"]["name"] for t in GOOGLE_TOOL_SPECS}


async def run_google_tool(settings, name: str, arguments: dict[str, Any]) -> str:
    try:
        if name == "gcal_add_event":
            res = await asyncio.to_thread(
                gc.calendar_add, settings, summary=arguments["summary"], start=arguments["start"],
                end=arguments.get("end"), all_day=bool(arguments.get("all_day")),
                location=arguments.get("location") or "", description=arguments.get("description") or "")
            return json.dumps({"ok": True, "event": res}, ensure_ascii=False)
        if name == "gcal_list_events":
            res = await asyncio.to_thread(
                gc.calendar_list, settings, time_min=arguments.get("time_min"),
                time_max=arguments.get("time_max"), max_results=int(arguments.get("max_results") or 10))
            return json.dumps({"ok": True, "events": res}, ensure_ascii=False)
        if name == "gcal_delete_event":
            await asyncio.to_thread(gc.calendar_delete, settings, event_id=arguments["event_id"])
            return json.dumps({"ok": True, "deleted": arguments["event_id"]}, ensure_ascii=False)
        if name == "gdoc_create":
            res = await asyncio.to_thread(gc.doc_create, settings, title=arguments["title"], text=arguments.get("text") or "")
            return json.dumps({"ok": True, "doc": res}, ensure_ascii=False)
        if name == "gdoc_append":
            res = await asyncio.to_thread(gc.doc_append, settings, doc_id=arguments["doc_id"], text=arguments["text"])
            return json.dumps({"ok": True, "doc": res}, ensure_ascii=False)
        if name == "gdoc_read":
            res = await asyncio.to_thread(gc.doc_read, settings, doc_id=arguments["doc_id"])
            return json.dumps({"ok": True, "doc": res}, ensure_ascii=False)
        if name == "gdoc_list":
            res = await asyncio.to_thread(gc.doc_list, settings, max_results=int(arguments.get("max_results") or 20))
            return json.dumps({"ok": True, "docs": res}, ensure_ascii=False)
        return json.dumps({"ok": False, "error": f"unknown google tool {name}"}, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001 — surface a clean message to the model
        return json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)
