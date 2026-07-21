from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.db import Store as LegacyStore


EXTENSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS households (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'Asia/Jerusalem',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS household_members (
    household_id TEXT NOT NULL,
    telegram_user_id INTEGER NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    username TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'member',
    notification_mode TEXT NOT NULL DEFAULT 'digest',
    joined_at REAL NOT NULL,
    PRIMARY KEY (household_id, telegram_user_id)
);
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id TEXT NOT NULL,
    actor_id INTEGER,
    kind TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_created ON activity(household_id, created_at DESC);
"""


class Store(LegacyStore):
    """Backward-compatible extension of the original store for the Mini App."""

    def __init__(self, path: Path, household_id: str = "primary"):
        super().__init__(path)
        self.household_id = household_id

    async def connect(self) -> None:
        await super().connect()
        await self.db.executescript(EXTENSION_SCHEMA)
        await self._ensure_column("todos", "assigned_to", "INTEGER")
        await self._ensure_column("todos", "due_at", "TEXT")
        await self._ensure_column("todos", "priority", "TEXT NOT NULL DEFAULT 'normal'")
        await self._ensure_column("shopping", "category", "TEXT NOT NULL DEFAULT ''")
        await self._ensure_column("events", "start_at", "TEXT")
        await self._ensure_column("events", "end_at", "TEXT")
        await self._ensure_column("events", "location", "TEXT NOT NULL DEFAULT ''")
        await self._ensure_column("events", "all_day", "INTEGER NOT NULL DEFAULT 0")
        await self.db.commit()

    async def _columns(self, table: str) -> set[str]:
        rows = await (await self.db.execute(f"PRAGMA table_info({table})")).fetchall()
        return {str(row["name"]) for row in rows}

    async def _ensure_column(self, table: str, name: str, definition: str) -> None:
        if name not in await self._columns(table):
            await self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    async def bootstrap_household(self, name: str, timezone: str, allowed_user_ids: list[int]) -> None:
        now = time.time()
        await self.db.execute(
            """INSERT INTO households(id, name, timezone, created_at, updated_at) VALUES(?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name, timezone=excluded.timezone, updated_at=excluded.updated_at""",
            (self.household_id, name, timezone, now, now),
        )
        for user_id in allowed_user_ids:
            await self.db.execute(
                """INSERT INTO household_members(household_id, telegram_user_id, joined_at) VALUES(?, ?, ?)
                   ON CONFLICT(household_id, telegram_user_id) DO NOTHING""",
                (self.household_id, user_id, now),
            )
        await self.db.commit()

    async def is_member(self, user_id: int, household_id: str | None = None) -> bool:
        row = await (await self.db.execute(
            "SELECT 1 FROM household_members WHERE household_id=? AND telegram_user_id=?",
            (household_id or self.household_id, user_id),
        )).fetchone()
        return row is not None

    async def upsert_member_profile(self, user_id: int, display_name: str, username: str = "") -> None:
        await self.db.execute(
            "UPDATE household_members SET display_name=?, username=? WHERE household_id=? AND telegram_user_id=?",
            (display_name, username, self.household_id, user_id),
        )
        await self.db.commit()

    async def get_household(self) -> dict[str, Any]:
        row = await (await self.db.execute("SELECT * FROM households WHERE id=?", (self.household_id,))).fetchone()
        return dict(row) if row else {}

    async def update_household(self, *, name: str | None = None, timezone: str | None = None) -> dict[str, Any]:
        current = await self.get_household()
        await self.db.execute(
            "UPDATE households SET name=?, timezone=?, updated_at=? WHERE id=?",
            (name or current.get("name", "Home"), timezone or current.get("timezone", "Asia/Jerusalem"), time.time(), self.household_id),
        )
        await self.db.commit()
        return await self.get_household()

    async def list_members(self) -> list[dict[str, Any]]:
        rows = await (await self.db.execute(
            "SELECT telegram_user_id, display_name, username, role, notification_mode, joined_at FROM household_members WHERE household_id=? ORDER BY joined_at",
            (self.household_id,),
        )).fetchall()
        return [dict(row) for row in rows]

    async def add_activity(self, actor_id: int | None, kind: str, entity_type: str, entity_id: str, summary: str, metadata: dict[str, Any] | None = None) -> int:
        cur = await self.db.execute(
            "INSERT INTO activity(household_id, actor_id, kind, entity_type, entity_id, summary, metadata, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (self.household_id, actor_id, kind, entity_type, entity_id, summary, json.dumps(metadata or {}, ensure_ascii=False), time.time()),
        )
        await self.db.commit()
        return int(cur.lastrowid or 0)

    async def list_activity(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = await (await self.db.execute(
            """SELECT a.*, m.display_name AS actor_name FROM activity a
               LEFT JOIN household_members m ON m.household_id=a.household_id AND m.telegram_user_id=a.actor_id
               WHERE a.household_id=? ORDER BY a.id DESC LIMIT ?""",
            (self.household_id, limit),
        )).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(item.get("metadata") or "{}")
            except json.JSONDecodeError:
                item["metadata"] = {}
            result.append(item)
        return result

    async def list_memories(self, category: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        rows = await super().list_memories(category)
        return rows[:limit]

    async def list_notes(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = await super().list_notes()
        return rows[:limit]

    async def add_todo(self, title: str, user_id: int | None = None, *, assigned_to: int | None = None, due_at: str | None = None, priority: str = "normal") -> int:
        cur = await self.db.execute(
            "INSERT INTO todos(title, assigned_to, due_at, priority, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (title.strip(), assigned_to, due_at, priority, user_id, time.time()),
        )
        await self.db.commit()
        return int(cur.lastrowid or 0)

    async def list_todos(self, include_done: bool = False) -> list[dict[str, Any]]:
        where = "" if include_done else "WHERE done=0"
        rows = await (await self.db.execute(
            f"SELECT id, title, done, assigned_to, due_at, priority, created_by, created_at, done_at FROM todos {where} ORDER BY done, CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, id DESC"
        )).fetchall()
        return [dict(row) for row in rows]

    async def update_todo(self, todo_id: int, **changes: Any) -> dict[str, Any] | None:
        return await self._update_entity("todos", todo_id, {"title", "assigned_to", "due_at", "priority", "done"}, changes)

    async def complete_todo(self, todo_id: int) -> bool:
        return (await self.update_todo(todo_id, done=True)) is not None

    async def reopen_todo(self, todo_id: int) -> bool:
        return (await self.update_todo(todo_id, done=False)) is not None

    async def delete_todo(self, todo_id: int) -> bool:
        return await self._delete_by_id("todos", todo_id)

    async def add_event(self, title: str, when_text: str, notes: str = "", user_id: int | None = None, *, start_at: str | None = None, end_at: str | None = None, location: str = "", all_day: bool = False) -> int:
        cur = await self.db.execute(
            "INSERT INTO events(title, when_text, start_at, end_at, location, all_day, notes, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (title.strip(), when_text.strip(), start_at, end_at, location.strip(), int(all_day), notes.strip(), user_id, time.time()),
        )
        await self.db.commit()
        return int(cur.lastrowid or 0)

    async def list_events(self) -> list[dict[str, Any]]:
        rows = await (await self.db.execute(
            "SELECT id, title, when_text, start_at, end_at, location, all_day, notes, created_by, created_at FROM events ORDER BY CASE WHEN start_at IS NULL THEN 1 ELSE 0 END, start_at, id DESC LIMIT 100"
        )).fetchall()
        return [dict(row) for row in rows]

    async def delete_event(self, event_id: int) -> bool:
        return await self._delete_by_id("events", event_id)

    async def shop_add(self, item: str, qty: str = "1", user_id: int | None = None, category: str = "") -> int:
        existing = await (await self.db.execute("SELECT id FROM shopping WHERE done=0 AND lower(item)=lower(?)", (item.strip(),))).fetchone()
        if existing:
            await self.db.execute("UPDATE shopping SET qty=?, category=? WHERE id=?", ((qty or "1").strip(), category.strip(), existing["id"]))
            await self.db.commit()
            return int(existing["id"])
        cur = await self.db.execute(
            "INSERT INTO shopping(item, qty, category, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
            (item.strip(), (qty or "1").strip(), category.strip(), user_id, time.time()),
        )
        await self.db.commit()
        return int(cur.lastrowid or 0)

    async def shop_list(self, include_done: bool = False) -> list[dict[str, Any]]:
        where = "" if include_done else "WHERE done=0"
        rows = await (await self.db.execute(
            f"SELECT id, item, qty, category, done, created_by, created_at, done_at FROM shopping {where} ORDER BY done, category, id DESC"
        )).fetchall()
        return [dict(row) for row in rows]

    async def update_shopping(self, item_id: int, **changes: Any) -> dict[str, Any] | None:
        return await self._update_entity("shopping", item_id, {"item", "qty", "category", "done"}, changes)

    async def shop_done(self, item_id: int) -> bool:
        return (await self.update_shopping(item_id, done=True)) is not None

    async def shop_reopen(self, item_id: int) -> bool:
        return (await self.update_shopping(item_id, done=False)) is not None

    async def shop_delete(self, item_id: int) -> bool:
        return await self._delete_by_id("shopping", item_id)

    async def _update_entity(self, table: str, entity_id: int, allowed: set[str], changes: dict[str, Any]) -> dict[str, Any] | None:
        fields: list[str] = []
        values: list[Any] = []
        for key, value in changes.items():
            if key not in allowed or value is None:
                continue
            fields.append(f"{key}=?")
            values.append(int(value) if key == "done" else value)
            if key == "done":
                fields.append("done_at=?")
                values.append(time.time() if value else None)
        if fields:
            values.append(entity_id)
            await self.db.execute(f"UPDATE {table} SET {', '.join(fields)} WHERE id=?", tuple(values))
            await self.db.commit()
        row = await (await self.db.execute(f"SELECT * FROM {table} WHERE id=?", (entity_id,))).fetchone()
        return dict(row) if row else None

    async def _delete_by_id(self, table: str, entity_id: int) -> bool:
        cur = await self.db.execute(f"DELETE FROM {table} WHERE id=?", (entity_id,))
        await self.db.commit()
        return cur.rowcount > 0

    async def dashboard(self) -> dict[str, Any]:
        todos, shopping, events = await self.list_todos(False), await self.shop_list(False), await self.list_events()
        return {
            "household": await self.get_household(),
            "members": await self.list_members(),
            "counts": {"todos": len(todos), "shopping": len(shopping), "events": len(events)},
            "todos": todos[:5], "shopping": shopping[:6], "events": events[:5],
            "activity": await self.list_activity(8),
        }

    async def snapshot_for_prompt(self, query: str | None = None) -> str:
        mems, todos, shopping = await self.list_memories(limit=20), await self.list_todos(False), await self.shop_list(False)
        events, people = await self.list_events(), await self.people_list()
        lines = ["## Shared household context"]
        if query:
            matches = {key: value for key, value in (await self.search_all(query, limit=5)).items() if value}
            if matches:
                lines.extend(["### Relevant search results", json.dumps(matches, ensure_ascii=False)[:3500]])
        lines.append("### Facts")
        lines.extend(f"- [{m['category']}] {m['key']}: {m['value']}" for m in mems[:12])
        lines.append("### Open todos")
        lines.extend(f"- #{item['id']} {item['title']}" for item in todos[:12])
        lines.append("### Shopping")
        lines.extend(f"- #{item['id']} {item['item']} × {item['qty']}" for item in shopping[:12])
        lines.append("### Events")
        lines.extend(f"- {item['title']} @ {item.get('start_at') or item.get('when_text')}" for item in events[:8])
        lines.append("### People")
        lines.extend(f"- {item['name']} {item['relation']} {item['prefs']}" for item in people[:10])
        return "\n".join(lines)
