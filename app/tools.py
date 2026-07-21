from __future__ import annotations

import json
from typing import Any

from app.store_v2 import Store
from app.services import HomeService
from app.google_tools import GOOGLE_TOOL_SPECS, GOOGLE_TOOL_NAMES, run_google_tool


def _tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    params: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        params["required"] = required
    return {"type": "function", "function": {"name": name, "description": description, "parameters": params}}


TOOL_SPECS: list[dict[str, Any]] = [
    _tool("remember", "Save or update a durable household fact shared by household members.", {"key": {"type": "string"}, "value": {"type": "string"}, "category": {"type": "string", "default": "general"}}, ["key", "value"]),
    _tool("recall", "Look up household memory.", {"key": {"type": "string"}, "category": {"type": "string"}, "list_all": {"type": "boolean", "default": True}}),
    _tool("forget", "Delete a household memory key.", {"key": {"type": "string"}}, ["key"]),
    _tool("search_home", "Search across household memory, tasks, shopping, notes, events, inventory and people.", {"query": {"type": "string"}}, ["query"]),
    _tool("todo_add", "Add a shared task.", {"title": {"type": "string"}, "assigned_to": {"type": "integer"}, "due_at": {"type": "string"}, "priority": {"type": "string", "enum": ["low", "normal", "high"]}}, ["title"]),
    _tool("todo_list", "List household tasks.", {"include_done": {"type": "boolean", "default": False}}),
    _tool("todo_done", "Complete a task by id.", {"id": {"type": "integer"}}, ["id"]),
    _tool("shop_add", "Add an item to the household shopping list.", {"item": {"type": "string"}, "qty": {"type": "string", "default": "1"}, "category": {"type": "string"}}, ["item"]),
    _tool("shop_list", "List shopping items.", {"include_done": {"type": "boolean", "default": False}}),
    _tool("shop_done", "Mark a shopping item as purchased.", {"id": {"type": "integer"}}, ["id"]),
    _tool("shop_clear_done", "Remove completed shopping items.", {}),
    _tool("note_save", "Create or update a household note.", {"title": {"type": "string"}, "body": {"type": "string"}, "tags": {"type": "string"}}, ["title", "body"]),
    _tool("note_get", "Read a note by title or list notes.", {"title": {"type": "string"}}),
    _tool("event_add", "Save a household event. Use ISO 8601 for start_at when possible.", {"title": {"type": "string"}, "when": {"type": "string"}, "start_at": {"type": "string"}, "end_at": {"type": "string"}, "location": {"type": "string"}, "notes": {"type": "string"}, "all_day": {"type": "boolean"}}, ["title", "when"]),
    _tool("event_list", "List household events.", {}),
    _tool("inventory_set", "Track an item at home.", {"item": {"type": "string"}, "qty": {"type": "string", "default": "1"}, "location": {"type": "string", "default": "home"}, "notes": {"type": "string"}}, ["item"]),
    _tool("inventory_get", "Get one inventory item or list inventory.", {"item": {"type": "string"}}),
    _tool("inventory_delete", "Remove an inventory item.", {"item": {"type": "string"}}, ["item"]),
    _tool("person_set", "Remember a person important to the household.", {"name": {"type": "string"}, "relation": {"type": "string"}, "notes": {"type": "string"}, "prefs": {"type": "string"}}, ["name"]),
    _tool("people_list", "List people in household memory.", {}),
    _tool("setting_set", "Set a household preference.", {"key": {"type": "string"}, "value": {"type": "string"}}, ["key", "value"]),
    _tool("setting_get", "Get one household preference or list all.", {"key": {"type": "string"}}),
    _tool("core_memory_append", "Append a lasting essential to Core memory (a household member/name, a recurring routine, a major ongoing situation). Keep it short — details go in facts/notes.", {"text": {"type": "string"}}, ["text"]),
    _tool("core_memory_replace", "Fix or prune Core memory by replacing an exact substring (use an empty replacement to delete it).", {"find": {"type": "string"}, "replace": {"type": "string", "default": ""}}, ["find"]),
]


def tool_specs(settings=None) -> list[dict[str, Any]]:
    """The tool list offered to the model — Google tools appear only when configured."""
    if settings is not None and getattr(settings, "google_enabled", False):
        return [*TOOL_SPECS, *GOOGLE_TOOL_SPECS]
    return TOOL_SPECS


async def run_tool(store: Store, service: HomeService, name: str, arguments: dict[str, Any], user_id: int | None, settings=None) -> str:
    actor = int(user_id or 0)
    if name in GOOGLE_TOOL_NAMES:
        if settings is None or not getattr(settings, "google_enabled", False):
            return json.dumps({"ok": False, "error": "Google integration is not configured"}, ensure_ascii=False)
        return await run_google_tool(settings, name, arguments)
    try:
        if name == "remember":
            await store.set_memory(arguments["key"], arguments["value"], arguments.get("category") or "general", user_id)
            await store.add_activity(user_id, "updated", "memory", arguments["key"], f"עודכן זיכרון: {arguments['key']}")
            return json.dumps({"ok": True, "saved": arguments["key"]}, ensure_ascii=False)
        if name == "recall":
            if arguments.get("key"):
                return json.dumps({"ok": True, "memory": await store.get_memory(arguments["key"])}, ensure_ascii=False)
            return json.dumps({"ok": True, "memories": await store.list_memories(arguments.get("category"))}, ensure_ascii=False)
        if name == "forget":
            ok = await store.delete_memory(arguments["key"])
            if ok:
                await store.add_activity(user_id, "deleted", "memory", arguments["key"], f"נמחק זיכרון: {arguments['key']}")
            return json.dumps({"ok": ok, "deleted": arguments["key"]}, ensure_ascii=False)
        if name == "search_home":
            return json.dumps({"ok": True, "results": await store.search_all(arguments["query"])}, ensure_ascii=False)
        if name == "todo_add":
            item = await service.add_todo(actor, arguments["title"], assigned_to=arguments.get("assigned_to"), due_at=arguments.get("due_at"), priority=arguments.get("priority") or "normal")
            return json.dumps({"ok": True, "todo": item}, ensure_ascii=False)
        if name == "todo_list":
            return json.dumps({"ok": True, "todos": await store.list_todos(bool(arguments.get("include_done")))}, ensure_ascii=False)
        if name == "todo_done":
            item = await service.update_todo(actor, int(arguments["id"]), done=True)
            return json.dumps({"ok": bool(item), "todo": item}, ensure_ascii=False)
        if name == "shop_add":
            item = await service.add_shopping(actor, arguments["item"], arguments.get("qty") or "1", arguments.get("category") or "")
            return json.dumps({"ok": True, "shopping": item}, ensure_ascii=False)
        if name == "shop_list":
            return json.dumps({"ok": True, "shopping": await store.shop_list(bool(arguments.get("include_done")))}, ensure_ascii=False)
        if name == "shop_done":
            item = await service.update_shopping(actor, int(arguments["id"]), done=True)
            return json.dumps({"ok": bool(item), "shopping": item}, ensure_ascii=False)
        if name == "shop_clear_done":
            return json.dumps({"ok": True, "removed": await store.shop_clear_done()}, ensure_ascii=False)
        if name == "note_save":
            await store.upsert_note(arguments["title"], arguments["body"], arguments.get("tags") or "", user_id)
            await store.add_activity(user_id, "updated", "note", arguments["title"], f"עודכנה הערה: {arguments['title']}")
            return json.dumps({"ok": True, "title": arguments["title"]}, ensure_ascii=False)
        if name == "note_get":
            if arguments.get("title"):
                return json.dumps({"ok": True, "note": await store.get_note(arguments["title"])}, ensure_ascii=False)
            rows = await store.list_notes()
            return json.dumps({"ok": True, "notes": [{"id": n["id"], "title": n["title"], "tags": n["tags"]} for n in rows]}, ensure_ascii=False)
        if name == "event_add":
            item = await service.add_event(actor, title=arguments["title"], start_at=arguments.get("start_at") or arguments["when"], end_at=arguments.get("end_at"), location=arguments.get("location") or "", notes=arguments.get("notes") or "", all_day=bool(arguments.get("all_day")), when_text=arguments["when"])
            return json.dumps({"ok": True, "event": item}, ensure_ascii=False)
        if name == "event_list":
            return json.dumps({"ok": True, "events": await store.list_events()}, ensure_ascii=False)
        if name == "inventory_set":
            await store.inventory_set(arguments["item"], arguments.get("qty") or "1", arguments.get("location") or "home", arguments.get("notes") or "", user_id)
            await store.add_activity(user_id, "updated", "inventory", arguments["item"], f"עודכן מלאי: {arguments['item']}")
            return json.dumps({"ok": True, "item": arguments["item"]}, ensure_ascii=False)
        if name == "inventory_get":
            if arguments.get("item"):
                return json.dumps({"ok": True, "item": await store.inventory_get(arguments["item"])}, ensure_ascii=False)
            return json.dumps({"ok": True, "inventory": await store.inventory_list()}, ensure_ascii=False)
        if name == "inventory_delete":
            return json.dumps({"ok": await store.inventory_delete(arguments["item"]), "item": arguments["item"]}, ensure_ascii=False)
        if name == "person_set":
            await store.person_set(arguments["name"], arguments.get("relation") or "", arguments.get("notes") or "", arguments.get("prefs") or "", user_id)
            await store.add_activity(user_id, "updated", "person", arguments["name"], f"עודכן איש קשר: {arguments['name']}")
            return json.dumps({"ok": True, "name": arguments["name"]}, ensure_ascii=False)
        if name == "people_list":
            return json.dumps({"ok": True, "people": await store.people_list()}, ensure_ascii=False)
        if name == "setting_set":
            await store.set_setting(arguments["key"], arguments["value"])
            return json.dumps({"ok": True, "key": arguments["key"]}, ensure_ascii=False)
        if name == "setting_get":
            if arguments.get("key"):
                return json.dumps({"ok": True, "key": arguments["key"], "value": await store.get_setting(arguments["key"])}, ensure_ascii=False)
            return json.dumps({"ok": True, "settings": await store.list_settings()}, ensure_ascii=False)
        if name == "core_memory_append":
            return json.dumps({"ok": True, "core_memory": await store.append_core_memory(arguments["text"])}, ensure_ascii=False)
        if name == "core_memory_replace":
            ok = await store.replace_core_memory(arguments["find"], arguments.get("replace") or "")
            return json.dumps({"ok": ok}, ensure_ascii=False)
        return json.dumps({"ok": False, "error": f"unknown tool {name}"})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
