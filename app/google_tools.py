"""Google Docs and Sheets tool specs + dispatch.

Calendar operations are intentionally owned by app.calendar_service so the bot
and Mini App use one source of truth and one cache/sync path.
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
    _tool("gdoc_create", "Create a new Google Doc with an optional initial body. Returns its shareable URL.",
          {"title": {"type": "string"}, "text": {"type": "string"}}, ["title"]),
    _tool("gdoc_append", "Append text to the end of an existing Google Doc (get the id from gdoc_list).",
          {"doc_id": {"type": "string"}, "text": {"type": "string"}}, ["doc_id", "text"]),
    _tool("gdoc_read", "Read the text content of a Google Doc by id.",
          {"doc_id": {"type": "string"}}, ["doc_id"]),
    _tool("gdoc_list", "List Google Docs the bot manages (most recently modified first).",
          {"max_results": {"type": "integer", "default": 20}}),
    _tool("gsheet_create", "Create a Google Sheet, optionally with a header row. Returns its URL.",
          {"title": {"type": "string"}, "headers": {"type": "array", "items": {"type": "string"}}}, ["title"]),
    _tool("gsheet_append_row", "Append a row of values to a Google Sheet (get the id from gsheet_list).",
          {"sheet_id": {"type": "string"}, "values": {"type": "array", "items": {"type": "string"}}}, ["sheet_id", "values"]),
    _tool("gsheet_read", "Read cells from a Google Sheet. range is A1 notation (default A1:Z100).",
          {"sheet_id": {"type": "string"}, "range": {"type": "string"}}, ["sheet_id"]),
    _tool("gsheet_list", "List Google Sheets the bot manages (most recently modified first).",
          {"max_results": {"type": "integer", "default": 20}}),
]

GOOGLE_TOOL_NAMES = {t["function"]["name"] for t in GOOGLE_TOOL_SPECS}


async def run_google_tool(settings, name: str, arguments: dict[str, Any]) -> str:
    try:
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
        if name == "gsheet_create":
            res = await asyncio.to_thread(gc.sheet_create, settings, title=arguments["title"], headers=arguments.get("headers"))
            return json.dumps({"ok": True, "sheet": res}, ensure_ascii=False)
        if name == "gsheet_append_row":
            res = await asyncio.to_thread(gc.sheet_append_row, settings, sheet_id=arguments["sheet_id"], values=arguments["values"])
            return json.dumps({"ok": True, "sheet": res}, ensure_ascii=False)
        if name == "gsheet_read":
            res = await asyncio.to_thread(gc.sheet_read, settings, sheet_id=arguments["sheet_id"], range_a1=arguments.get("range") or "A1:Z100")
            return json.dumps({"ok": True, "sheet": res}, ensure_ascii=False)
        if name == "gsheet_list":
            res = await asyncio.to_thread(gc.sheet_list, settings, max_results=int(arguments.get("max_results") or 20))
            return json.dumps({"ok": True, "sheets": res}, ensure_ascii=False)
        return json.dumps({"ok": False, "error": f"unknown google tool {name}"}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)
