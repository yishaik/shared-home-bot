from __future__ import annotations

import json
from typing import Any

from app import proactive
from app.calendar_service import CalendarService
from app.google_tools import GOOGLE_TOOL_NAMES, GOOGLE_TOOL_SPECS, run_google_tool
from app.services import HomeService
from app.store_v2 import Store
from app.work_service import WorkService


def _tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    params: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        params["required"] = required
    return {"type": "function", "function": {"name": name, "description": description, "parameters": params}}


TOOL_SPECS: list[dict[str, Any]] = [
    _tool("remember", "Save or update a durable household fact.", {"key": {"type": "string"}, "value": {"type": "string"}, "category": {"type": "string", "default": "general"}}, ["key", "value"]),
    _tool("recall", "Look up household memory.", {"key": {"type": "string"}, "category": {"type": "string"}, "list_all": {"type": "boolean", "default": True}}),
    _tool("forget", "Delete a household memory key.", {"key": {"type": "string"}}, ["key"]),
    _tool("search_home", "Search household memory and operational data.", {"query": {"type": "string"}}, ["query"]),
    _tool("project_add", "Create a household project, optionally with a managed Drive folder.", {"name": {"type": "string"}, "description": {"type": "string"}, "owner_id": {"type": "integer"}, "due_at": {"type": "string"}, "priority": {"type": "string", "enum": ["low", "normal", "high"]}, "create_drive_folder": {"type": "boolean", "default": False}}, ["name"]),
    _tool("project_list", "List active household projects and progress.", {}),
    _tool("todo_add", "Add a task, optionally in a project or below a parent task.", {"title": {"type": "string"}, "description": {"type": "string"}, "project_id": {"type": "integer"}, "parent_task_id": {"type": "integer"}, "assigned_to": {"type": "integer"}, "due_at": {"type": "string"}, "priority": {"type": "string", "enum": ["low", "normal", "high"]}, "status": {"type": "string", "enum": ["todo", "in_progress", "waiting", "completed", "cancelled"]}}, ["title"]),
    _tool("todo_list", "List household tasks, optionally for one project.", {"include_done": {"type": "boolean", "default": False}, "project_id": {"type": "integer"}}),
    _tool("todo_update", "Update a task.", {"id": {"type": "integer"}, "title": {"type": "string"}, "description": {"type": "string"}, "project_id": {"type": "integer"}, "parent_task_id": {"type": "integer"}, "assigned_to": {"type": "integer"}, "due_at": {"type": "string"}, "priority": {"type": "string", "enum": ["low", "normal", "high"]}, "status": {"type": "string", "enum": ["todo", "in_progress", "waiting", "completed", "cancelled"]}}, ["id"]),
    _tool("todo_done", "Complete a task by id.", {"id": {"type": "integer"}}, ["id"]),
    _tool("todo_link", "Relate two tasks or create a dependency.", {"source_task_id": {"type": "integer"}, "target_task_id": {"type": "integer"}, "relationship_type": {"type": "string", "enum": ["blocks", "related", "follows", "duplicates"]}}, ["source_task_id", "target_task_id", "relationship_type"]),
    _tool("todo_schedule", "Reserve a start/end work block for a task in Google Calendar.", {"id": {"type": "integer"}, "start_at": {"type": "string"}, "end_at": {"type": "string"}, "location": {"type": "string"}, "block_type": {"type": "string", "enum": ["work", "appointment", "review", "focus"]}}, ["id", "start_at", "end_at"]),
    _tool("todo_create_doc", "Create and attach a Google Doc to a task.", {"id": {"type": "integer"}}, ["id"]),
    _tool("todo_create_sheet", "Create and attach a Google Sheet to a task.", {"id": {"type": "integer"}, "template": {"type": "string", "enum": ["tracker", "budget", "suppliers", "equipment"]}}, ["id"]),
    _tool("shop_add", "Add an item to the shopping list.", {"item": {"type": "string"}, "qty": {"type": "string", "default": "1"}, "category": {"type": "string"}}, ["item"]),
    _tool("shop_list", "List shopping items.", {"include_done": {"type": "boolean", "default": False}}),
    _tool("shop_done", "Mark a shopping item purchased.", {"id": {"type": "integer"}}, ["id"]),
    _tool("shop_clear_done", "Remove completed shopping items.", {}),
    _tool("note_save", "Create or update a household note.", {"title": {"type": "string"}, "body": {"type": "string"}, "tags": {"type": "string"}}, ["title", "body"]),
    _tool("note_get", "Read a note by title or list notes.", {"title": {"type": "string"}}),
    _tool("event_add", "Create an event in the shared Google Calendar. Both start_at and end_at are required.", {"title": {"type": "string"}, "start_at": {"type": "string"}, "end_at": {"type": "string"}, "location": {"type": "string"}, "description": {"type": "string"}, "all_day": {"type": "boolean"}, "attendees": {"type": "array", "items": {"type": "string"}}, "recurrence": {"type": "array", "items": {"type": "string"}}}, ["title", "start_at", "end_at"]),
    _tool("event_list", "List synchronized shared Google Calendar events.", {}),
    _tool("event_update", "Update a shared Google Calendar event by id.", {"id": {"type": "string"}, "title": {"type": "string"}, "start_at": {"type": "string"}, "end_at": {"type": "string"}, "location": {"type": "string"}, "description": {"type": "string"}, "all_day": {"type": "boolean"}, "attendees": {"type": "array", "items": {"type": "string"}}, "recurrence": {"type": "array", "items": {"type": "string"}}}, ["id"]),
    _tool("event_delete", "Delete a shared Google Calendar event by id.", {"id": {"type": "string"}}, ["id"]),
    _tool("inventory_set", "Track an item at home.", {"item": {"type": "string"}, "qty": {"type": "string", "default": "1"}, "location": {"type": "string", "default": "home"}, "notes": {"type": "string"}}, ["item"]),
    _tool("inventory_get", "Get one inventory item or list inventory.", {"item": {"type": "string"}}),
    _tool("inventory_delete", "Remove an inventory item.", {"item": {"type": "string"}}, ["item"]),
    _tool("person_set", "Remember a person important to the household.", {"name": {"type": "string"}, "relation": {"type": "string"}, "notes": {"type": "string"}, "prefs": {"type": "string"}}, ["name"]),
    _tool("people_list", "List people in household memory.", {}),
    _tool("setting_set", "Set a household preference.", {"key": {"type": "string"}, "value": {"type": "string"}}, ["key", "value"]),
    _tool("setting_get", "Get one household preference or list all.", {"key": {"type": "string"}}),
    _tool("core_memory_append", "Append a lasting essential to Core memory.", {"text": {"type": "string"}}, ["text"]),
    _tool("core_memory_replace", "Replace an exact substring in Core memory.", {"find": {"type": "string"}, "replace": {"type": "string", "default": ""}}, ["find"]),
    _tool("remind_add", "Schedule a proactive reminder. recurrence: once, daily or weekly.", {"text": {"type": "string"}, "due_at": {"type": "string"}, "target": {"type": "string", "enum": ["me", "all"], "default": "all"}, "recurrence": {"type": "string", "enum": ["", "daily", "weekly"], "default": ""}}, ["text", "due_at"]),
    _tool("remind_list", "List pending reminders.", {}),
    _tool("remind_cancel", "Cancel a reminder by id.", {"id": {"type": "integer"}}, ["id"]),
]


def tool_specs(settings=None) -> list[dict[str, Any]]:
    if settings is not None and getattr(settings, "google_enabled", False):
        non_calendar_google = [tool for tool in GOOGLE_TOOL_SPECS if not tool["function"]["name"].startswith("gcal_")]
        return [*TOOL_SPECS, *non_calendar_google]
    return TOOL_SPECS


async def run_tool(store: Store, service: HomeService, name: str, arguments: dict[str, Any], user_id: int | None, settings=None) -> str:
    actor = int(user_id or 0)
    if name in GOOGLE_TOOL_NAMES and not name.startswith("gcal_"):
        if settings is None or not getattr(settings, "google_enabled", False):
            return json.dumps({"ok": False, "error": "Google integration is not configured"}, ensure_ascii=False)
        return await run_google_tool(settings, name, arguments)
    try:
        work = WorkService(settings, store) if settings is not None else None
        if name == "remember":
            await store.set_memory(arguments["key"], arguments["value"], arguments.get("category") or "general", user_id)
            await store.add_activity(user_id, "updated", "memory", arguments["key"], f"עודכן זיכרון: {arguments['key']}")
            return json.dumps({"ok": True, "saved": arguments["key"]}, ensure_ascii=False)
        if name == "recall":
            return json.dumps({"ok": True, "memory": await store.get_memory(arguments["key"])} if arguments.get("key") else {"ok": True, "memories": await store.list_memories(arguments.get("category"))}, ensure_ascii=False)
        if name == "forget":
            return json.dumps({"ok": await store.delete_memory(arguments["key"]), "deleted": arguments["key"]}, ensure_ascii=False)
        if name == "search_home":
            return json.dumps({"ok": True, "results": await store.search_all(arguments["query"])}, ensure_ascii=False)
        if name == "project_add" and work:
            return json.dumps({"ok": True, "project": await work.create_project(actor, **arguments)}, ensure_ascii=False)
        if name == "project_list" and work:
            return json.dumps({"ok": True, "projects": await work.list_projects()}, ensure_ascii=False)
        if name == "todo_add" and work:
            return json.dumps({"ok": True, "todo": await work.create_task(actor, **arguments)}, ensure_ascii=False)
        if name == "todo_list" and work:
            return json.dumps({"ok": True, "todos": await work.list_tasks(include_done=bool(arguments.get("include_done")), project_id=arguments.get("project_id"))}, ensure_ascii=False)
        if name == "todo_update" and work:
            changes = {key: value for key, value in arguments.items() if key != "id"}
            item = await work.update_task(actor, int(arguments["id"]), **changes)
            return json.dumps({"ok": bool(item), "todo": item}, ensure_ascii=False)
        if name == "todo_done" and work:
            item = await work.update_task(actor, int(arguments["id"]), status="completed")
            return json.dumps({"ok": bool(item), "todo": item}, ensure_ascii=False)
        if name == "todo_link" and work:
            return json.dumps({"ok": True, "relationship": await work.add_relationship(actor, **arguments)}, ensure_ascii=False)
        if name == "todo_schedule" and work:
            payload = dict(arguments); task_id = int(payload.pop("id"))
            return json.dumps({"ok": True, "calendar_block": await work.create_calendar_block(actor, task_id, **payload)}, ensure_ascii=False)
        if name == "todo_create_doc" and work:
            return json.dumps({"ok": True, "resource": await work.create_task_doc(actor, int(arguments["id"]))}, ensure_ascii=False)
        if name == "todo_create_sheet" and work:
            return json.dumps({"ok": True, "resource": await work.create_task_sheet(actor, int(arguments["id"]), arguments.get("template") or "tracker")}, ensure_ascii=False)
        if name == "shop_add":
            return json.dumps({"ok": True, "shopping": await service.add_shopping(actor, arguments["item"], arguments.get("qty") or "1", arguments.get("category") or "")}, ensure_ascii=False)
        if name == "shop_list":
            return json.dumps({"ok": True, "shopping": await store.shop_list(bool(arguments.get("include_done")))}, ensure_ascii=False)
        if name == "shop_done":
            item = await service.update_shopping(actor, int(arguments["id"]), done=True)
            return json.dumps({"ok": bool(item), "shopping": item}, ensure_ascii=False)
        if name == "shop_clear_done":
            return json.dumps({"ok": True, "removed": await store.shop_clear_done()}, ensure_ascii=False)
        if name == "note_save":
            await store.upsert_note(arguments["title"], arguments["body"], arguments.get("tags") or "", user_id)
            return json.dumps({"ok": True, "title": arguments["title"]}, ensure_ascii=False)
        if name == "note_get":
            if arguments.get("title"):
                return json.dumps({"ok": True, "note": await store.get_note(arguments["title"])}, ensure_ascii=False)
            return json.dumps({"ok": True, "notes": await store.list_notes()}, ensure_ascii=False)
        if name.startswith("event_"):
            if settings is None or not getattr(settings, "google_enabled", False):
                return json.dumps({"ok": False, "error": "Google Calendar is not configured"}, ensure_ascii=False)
            calendar = CalendarService(settings, store)
            if name == "event_add":
                return json.dumps({"ok": True, "event": await calendar.create_event(actor, title=arguments["title"], start_at=arguments["start_at"], end_at=arguments["end_at"], location=arguments.get("location") or "", description=arguments.get("description") or "", all_day=bool(arguments.get("all_day")), attendees=arguments.get("attendees") or [], recurrence=arguments.get("recurrence") or [])}, ensure_ascii=False)
            if name == "event_list":
                await calendar.incremental_sync()
                return json.dumps({"ok": True, "events": await calendar.list_events()}, ensure_ascii=False)
            if name == "event_update":
                changes = {key: value for key, value in arguments.items() if key != "id"}
                return json.dumps({"ok": True, "event": await calendar.update_event(actor, arguments["id"], **changes)}, ensure_ascii=False)
            await calendar.delete_event(actor, arguments["id"])
            return json.dumps({"ok": True, "deleted": arguments["id"]}, ensure_ascii=False)
        if name == "inventory_set":
            await store.inventory_set(arguments["item"], arguments.get("qty") or "1", arguments.get("location") or "home", arguments.get("notes") or "", user_id)
            return json.dumps({"ok": True, "item": arguments["item"]}, ensure_ascii=False)
        if name == "inventory_get":
            return json.dumps({"ok": True, "item": await store.inventory_get(arguments["item"])} if arguments.get("item") else {"ok": True, "inventory": await store.inventory_list()}, ensure_ascii=False)
        if name == "inventory_delete":
            return json.dumps({"ok": await store.inventory_delete(arguments["item"]), "item": arguments["item"]}, ensure_ascii=False)
        if name == "person_set":
            await store.person_set(arguments["name"], arguments.get("relation") or "", arguments.get("notes") or "", arguments.get("prefs") or "", user_id)
            return json.dumps({"ok": True, "name": arguments["name"]}, ensure_ascii=False)
        if name == "people_list":
            return json.dumps({"ok": True, "people": await store.people_list()}, ensure_ascii=False)
        if name == "setting_set":
            await store.set_setting(arguments["key"], arguments["value"])
            return json.dumps({"ok": True, "key": arguments["key"]}, ensure_ascii=False)
        if name == "setting_get":
            return json.dumps({"ok": True, "key": arguments["key"], "value": await store.get_setting(arguments["key"])} if arguments.get("key") else {"ok": True, "settings": await store.list_settings()}, ensure_ascii=False)
        if name == "remind_add":
            target_user = actor if arguments.get("target") == "me" else None
            item = await proactive.reminder_add(store, settings, text=arguments["text"], due_at=arguments["due_at"], created_by=actor, target_user_id=target_user, recurrence=arguments.get("recurrence") or "")
            return json.dumps({"ok": True, "reminder": item}, ensure_ascii=False)
        if name == "remind_list":
            return json.dumps({"ok": True, "reminders": await proactive.reminder_list(store, settings)}, ensure_ascii=False)
        if name == "remind_cancel":
            return json.dumps({"ok": await proactive.reminder_cancel(store, int(arguments["id"])), "cancelled": arguments["id"]}, ensure_ascii=False)
        if name == "core_memory_append":
            return json.dumps({"ok": True, "core_memory": await store.append_core_memory(arguments["text"])}, ensure_ascii=False)
        if name == "core_memory_replace":
            return json.dumps({"ok": await store.replace_core_memory(arguments["find"], arguments.get("replace") or "")}, ensure_ascii=False)
        return json.dumps({"ok": False, "error": f"unknown tool {name}"}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
