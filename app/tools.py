"""Tool definitions + execution against the shared Store."""

from __future__ import annotations

import json
from typing import Any

from app.db import Store


def _tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    params: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        params["required"] = required
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": params},
    }


TOOL_SPECS: list[dict[str, Any]] = [
    _tool(
        "remember",
        "Save or update a durable household fact both partners share (Wi‑Fi, preferences, decisions, addresses).",
        {
            "key": {"type": "string", "description": "snake_case key e.g. wifi_password"},
            "value": {"type": "string"},
            "category": {
                "type": "string",
                "description": "home|people|prefs|finance|health|other",
                "default": "general",
            },
        },
        ["key", "value"],
    ),
    _tool(
        "recall",
        "Look up one memory key, or list memories (optionally by category).",
        {
            "key": {"type": "string"},
            "category": {"type": "string"},
            "list_all": {"type": "boolean", "default": True},
        },
    ),
    _tool("forget", "Delete a memory key.", {"key": {"type": "string"}}, ["key"]),
    _tool(
        "search_home",
        "Full-text search across memories, todos, shopping, notes, events, inventory, people.",
        {"query": {"type": "string"}},
        ["query"],
    ),
    _tool("todo_add", "Add a shared chore/todo.", {"title": {"type": "string"}}, ["title"]),
    _tool(
        "todo_list",
        "List shared todos.",
        {"include_done": {"type": "boolean", "default": False}},
    ),
    _tool("todo_done", "Complete a todo by id.", {"id": {"type": "integer"}}, ["id"]),
    _tool(
        "shop_add",
        "Add item to the shared shopping list.",
        {
            "item": {"type": "string"},
            "qty": {"type": "string", "default": "1", "description": "quantity e.g. 2 or 1L"},
        },
        ["item"],
    ),
    _tool(
        "shop_list",
        "Show open shopping list items.",
        {"include_done": {"type": "boolean", "default": False}},
    ),
    _tool("shop_done", "Check off a shopping item by id.", {"id": {"type": "integer"}}, ["id"]),
    _tool("shop_clear_done", "Remove completed shopping items.", {}),
    _tool(
        "note_save",
        "Create/update a shared note (recipes, how-tos, vendor contacts).",
        {
            "title": {"type": "string"},
            "body": {"type": "string"},
            "tags": {"type": "string", "description": "comma tags"},
        },
        ["title", "body"],
    ),
    _tool(
        "note_get",
        "Read a note by title, or list titles if omitted.",
        {"title": {"type": "string"}},
    ),
    _tool(
        "event_add",
        "Save a shared event (free-text when is fine).",
        {
            "title": {"type": "string"},
            "when": {"type": "string"},
            "notes": {"type": "string"},
        },
        ["title", "when"],
    ),
    _tool("event_list", "List shared events.", {}),
    _tool(
        "inventory_set",
        "Track something at home (qty + location).",
        {
            "item": {"type": "string"},
            "qty": {"type": "string", "default": "1"},
            "location": {"type": "string", "default": "home", "description": "e.g. pantry, fridge, closet"},
            "notes": {"type": "string"},
        },
        ["item"],
    ),
    _tool(
        "inventory_get",
        "Get one inventory item or list all if item omitted.",
        {"item": {"type": "string"}},
    ),
    _tool("inventory_delete", "Remove an inventory item.", {"item": {"type": "string"}}, ["item"]),
    _tool(
        "person_set",
        "Remember a person important to the household (family, cleaner, landlord) + prefs.",
        {
            "name": {"type": "string"},
            "relation": {"type": "string"},
            "notes": {"type": "string"},
            "prefs": {"type": "string", "description": "dietary prefs, allergies, etc."},
        },
        ["name"],
    ),
    _tool("people_list", "List people in household memory.", {}),
    _tool(
        "setting_set",
        "Set household setting e.g. timezone, language, grocery_store, default_city.",
        {"key": {"type": "string"}, "value": {"type": "string"}},
        ["key", "value"],
    ),
    _tool(
        "setting_get",
        "Get one setting or list all if key omitted.",
        {"key": {"type": "string"}},
    ),
]


async def run_tool(
    store: Store,
    name: str,
    arguments: dict[str, Any],
    user_id: int | None,
) -> str:
    try:
        if name == "remember":
            await store.set_memory(
                arguments["key"],
                arguments["value"],
                arguments.get("category") or "general",
                user_id,
            )
            return json.dumps({"ok": True, "saved": arguments["key"]})

        if name == "recall":
            if arguments.get("key"):
                return json.dumps({"ok": True, "memory": await store.get_memory(arguments["key"])})
            return json.dumps(
                {"ok": True, "memories": await store.list_memories(arguments.get("category"))}
            )

        if name == "forget":
            return json.dumps(
                {"ok": await store.delete_memory(arguments["key"]), "deleted": arguments["key"]}
            )

        if name == "search_home":
            return json.dumps(
                {"ok": True, "results": await store.search_all(arguments["query"])}
            )

        if name == "todo_add":
            tid = await store.add_todo(arguments["title"], user_id)
            return json.dumps({"ok": True, "id": tid, "title": arguments["title"]})

        if name == "todo_list":
            return json.dumps(
                {
                    "ok": True,
                    "todos": await store.list_todos(bool(arguments.get("include_done"))),
                }
            )

        if name == "todo_done":
            return json.dumps(
                {"ok": await store.complete_todo(int(arguments["id"])), "id": arguments["id"]}
            )

        if name == "shop_add":
            sid = await store.shop_add(
                arguments["item"], arguments.get("qty") or "1", user_id
            )
            return json.dumps({"ok": True, "id": sid, "item": arguments["item"]})

        if name == "shop_list":
            return json.dumps(
                {
                    "ok": True,
                    "shopping": await store.shop_list(bool(arguments.get("include_done"))),
                }
            )

        if name == "shop_done":
            return json.dumps(
                {"ok": await store.shop_done(int(arguments["id"])), "id": arguments["id"]}
            )

        if name == "shop_clear_done":
            n = await store.shop_clear_done()
            return json.dumps({"ok": True, "removed": n})

        if name == "note_save":
            await store.upsert_note(
                arguments["title"],
                arguments["body"],
                arguments.get("tags") or "",
                user_id,
            )
            return json.dumps({"ok": True, "title": arguments["title"]})

        if name == "note_get":
            if arguments.get("title"):
                return json.dumps({"ok": True, "note": await store.get_note(arguments["title"])})
            rows = await store.list_notes()
            slim = [{"id": n["id"], "title": n["title"], "tags": n["tags"]} for n in rows]
            return json.dumps({"ok": True, "notes": slim})

        if name == "event_add":
            eid = await store.add_event(
                arguments["title"],
                arguments["when"],
                arguments.get("notes") or "",
                user_id,
            )
            return json.dumps({"ok": True, "id": eid})

        if name == "event_list":
            return json.dumps({"ok": True, "events": await store.list_events()})

        if name == "inventory_set":
            await store.inventory_set(
                arguments["item"],
                arguments.get("qty") or "1",
                arguments.get("location") or "home",
                arguments.get("notes") or "",
                user_id,
            )
            return json.dumps({"ok": True, "item": arguments["item"]})

        if name == "inventory_get":
            if arguments.get("item"):
                return json.dumps(
                    {"ok": True, "item": await store.inventory_get(arguments["item"])}
                )
            return json.dumps({"ok": True, "inventory": await store.inventory_list()})

        if name == "inventory_delete":
            return json.dumps(
                {
                    "ok": await store.inventory_delete(arguments["item"]),
                    "item": arguments["item"],
                }
            )

        if name == "person_set":
            await store.person_set(
                arguments["name"],
                arguments.get("relation") or "",
                arguments.get("notes") or "",
                arguments.get("prefs") or "",
                user_id,
            )
            return json.dumps({"ok": True, "name": arguments["name"]})

        if name == "people_list":
            return json.dumps({"ok": True, "people": await store.people_list()})

        if name == "setting_set":
            await store.set_setting(arguments["key"], arguments["value"])
            return json.dumps({"ok": True, "key": arguments["key"]})

        if name == "setting_get":
            if arguments.get("key"):
                return json.dumps(
                    {
                        "ok": True,
                        "key": arguments["key"],
                        "value": await store.get_setting(arguments["key"]),
                    }
                )
            return json.dumps({"ok": True, "settings": await store.list_settings()})

        return json.dumps({"ok": False, "error": f"unknown tool {name}"})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})
