from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.db import Store as LegacyStore
from app.memory import MemoryIndex


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
        self.memory: MemoryIndex | None = None

    async def attach_memory(self, settings) -> None:
        """Wire the hybrid retrieval engine (embeddings + FTS5) and backfill it."""
        client = None
        if settings.openai_api_key:
            from openai import AsyncOpenAI
            kwargs: dict[str, Any] = {"api_key": settings.openai_api_key}
            if settings.openai_base_url:
                kwargs["base_url"] = settings.openai_base_url
            client = AsyncOpenAI(**kwargs)
        mi = MemoryIndex(self.db, client, settings.embedding_model)
        await mi.ensure_schema()      # only expose the engine once its tables exist
        self.memory = mi
        await self.reindex_all()

    async def reindex_all(self) -> None:
        if not self.memory:
            return
        items: list[tuple[str, str, str]] = []
        for m in await super().list_memories():
            items.append(("memory", m["key"], f"{m['key']}: {m['value']}"))
        for n in await super().list_notes():
            items.append(("note", n["title"].strip().lower(), f"{n['title']}\n{n['body']}"))
        for p in await self.people_list():
            items.append(("person", p["name"].strip().lower(),
                          f"{p['name']} {p.get('relation','')} {p.get('notes','')} {p.get('prefs','')}"))
        await self.memory.backfill(items)

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

    # ── keep the hybrid index in sync on every durable write ──────────────
    async def set_memory(self, key: str, value: str, category: str = "general", user_id: int | None = None) -> None:
        await super().set_memory(key, value, category, user_id)
        if self.memory:
            await self.memory.index("memory", key.strip().lower(), f"{key.strip()}: {value.strip()}")

    async def delete_memory(self, key: str) -> bool:
        ok = await super().delete_memory(key)
        if self.memory:
            await self.memory.unindex("memory", key.strip().lower())
        return ok

    async def upsert_note(self, title: str, body: str, tags: str = "", user_id: int | None = None) -> None:
        await super().upsert_note(title, body, tags, user_id)
        if self.memory:
            await self.memory.index("note", title.strip().lower(), f"{title.strip()}\n{body}")

    async def person_set(self, name: str, relation: str = "", notes: str = "", prefs: str = "", user_id: int | None = None) -> None:
        await super().person_set(name, relation, notes, prefs, user_id)
        if self.memory:
            await self.memory.index("person", name.strip().lower(), f"{name} {relation} {notes} {prefs}".strip())

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

    # ── tiered memory: core block + rolling conversation summary ──────────
    async def get_core_memory(self) -> str:
        return await self.get_setting("_core_memory", "")

    async def set_core_memory(self, text: str) -> None:
        await self.set_setting("_core_memory", (text or "").strip()[:2000])

    async def append_core_memory(self, text: str) -> str:
        cur = await self.get_core_memory()
        combined = (cur + "\n" + text.strip()).strip() if cur else text.strip()
        await self.set_core_memory(combined)
        return combined[:2000]

    async def replace_core_memory(self, find: str, replace: str) -> bool:
        cur = await self.get_core_memory()
        if not find or find not in cur:
            return False
        await self.set_core_memory(cur.replace(find, replace))
        return True

    async def get_conv_summary(self) -> str:
        return await self.get_setting("_conv_summary", "")

    async def get_conv_summary_last_id(self) -> int:
        try:
            return int(await self.get_setting("_conv_summary_last_id", "0") or 0)
        except ValueError:
            return 0

    async def set_conv_summary(self, text: str, last_id: int) -> None:
        await self.set_setting("_conv_summary", (text or "").strip()[:2500])
        await self.set_setting("_conv_summary_last_id", str(int(last_id)))

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
        todos, shopping = await self.list_todos(False), await self.shop_list(False)
        events, people = await self.list_events(), await self.people_list()
        lines = ["## Shared household context"]

        # Relevance-ranked retrieval — hybrid (embeddings + FTS5, RRF-fused),
        # covers facts AND notes/people by meaning, not just substring. Never let
        # a retrieval hiccup break a reply — fall back to the lexical search.
        hits = None
        if query and self.memory:
            try:
                hits = await self.memory.hybrid_search(query, k=8)
            except Exception:
                hits = None
        if hits:
            lines.append("### Most relevant to this request")
            lines.extend(f"- [{h['kind']}] {h['text'][:280]}" for h in hits)
        elif query:  # fallback when the engine is unavailable/empty
            matches = {k: v for k, v in (await self.search_all(query, limit=5)).items() if v}
            if matches:
                lines.extend(["### Relevant search results", json.dumps(matches, ensure_ascii=False)[:3000]])

        mems = await self.list_memories(limit=10)
        lines.append("### Household facts")
        lines.extend(f"- [{m['category']}] {m['key']}: {m['value']}" for m in mems[:10])
        lines.append("### Open todos")
        lines.extend(f"- #{item['id']} {item['title']}" for item in todos[:12])
        lines.append("### Shopping")
        lines.extend(f"- #{item['id']} {item['item']} × {item['qty']}" for item in shopping[:12])
        lines.append("### Events")
        lines.extend(f"- {item['title']} @ {item.get('start_at') or item.get('when_text')}" for item in events[:8])
        lines.append("### People")
        lines.extend(f"- {item['name']} {item['relation']} {item['prefs']}" for item in people[:10])
        return "\n".join(lines)
