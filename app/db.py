"""Shared SQLite store — same memory for every user/session."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL,
    telegram_username TEXT,
    display_name TEXT,
    role TEXT NOT NULL,           -- user | assistant | system | tool
    content TEXT NOT NULL,
    tool_name TEXT,
    tool_call_id TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    updated_by INTEGER,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS todos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER,
    created_at REAL NOT NULL,
    done_at REAL
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '',
    updated_by INTEGER,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    when_text TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    created_by INTEGER,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS shopping (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item TEXT NOT NULL,
    qty TEXT NOT NULL DEFAULT '1',
    done INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER,
    created_at REAL NOT NULL,
    done_at REAL
);

CREATE TABLE IF NOT EXISTS inventory (
    item TEXT PRIMARY KEY,
    qty TEXT NOT NULL DEFAULT '1',
    location TEXT NOT NULL DEFAULT 'home',
    notes TEXT NOT NULL DEFAULT '',
    updated_by INTEGER,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS people (
    name TEXT PRIMARY KEY,
    relation TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    prefs TEXT NOT NULL DEFAULT '',
    updated_by INTEGER,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);
CREATE INDEX IF NOT EXISTS idx_todos_done ON todos(done);
CREATE INDEX IF NOT EXISTS idx_shopping_done ON shopping(done);
"""


class Store:
    def __init__(self, path: Path):
        self.path = path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "Store not connected"
        return self._db

    # ── messages (shared transcript) ──────────────────────────
    async def add_message(
        self,
        *,
        role: str,
        content: str,
        user_id: int | None = None,
        username: str | None = None,
        display_name: str | None = None,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
    ) -> None:
        await self.db.execute(
            """INSERT INTO messages
               (telegram_user_id, telegram_username, display_name, role, content,
                tool_name, tool_call_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                username,
                display_name,
                role,
                content,
                tool_name,
                tool_call_id,
                time.time(),
            ),
        )
        await self.db.commit()

    async def recent_messages(self, limit: int = 40) -> list[dict[str, Any]]:
        cur = await self.db.execute(
            """SELECT telegram_user_id, telegram_username, display_name, role, content,
                      tool_name, tool_call_id, created_at
               FROM messages ORDER BY id DESC LIMIT ?""",
            (limit,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in reversed(rows)]

    # ── shared memory KV ──────────────────────────────────────
    async def set_memory(
        self, key: str, value: str, category: str = "general", user_id: int | None = None
    ) -> None:
        await self.db.execute(
            """INSERT INTO memories(key, value, category, updated_by, updated_at)
               VALUES(?, ?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                 value=excluded.value,
                 category=excluded.category,
                 updated_by=excluded.updated_by,
                 updated_at=excluded.updated_at""",
            (key.strip().lower(), value.strip(), category.strip().lower(), user_id, time.time()),
        )
        await self.db.commit()

    async def get_memory(self, key: str) -> dict[str, Any] | None:
        cur = await self.db.execute(
            "SELECT key, value, category, updated_by, updated_at FROM memories WHERE key = ?",
            (key.strip().lower(),),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list_memories(self, category: str | None = None) -> list[dict[str, Any]]:
        if category:
            cur = await self.db.execute(
                """SELECT key, value, category, updated_by, updated_at
                   FROM memories WHERE category = ? ORDER BY key""",
                (category.strip().lower(),),
            )
        else:
            cur = await self.db.execute(
                "SELECT key, value, category, updated_by, updated_at FROM memories ORDER BY category, key"
            )
        return [dict(r) for r in await cur.fetchall()]

    async def delete_memory(self, key: str) -> bool:
        cur = await self.db.execute("DELETE FROM memories WHERE key = ?", (key.strip().lower(),))
        await self.db.commit()
        return cur.rowcount > 0

    # ── todos ─────────────────────────────────────────────────
    async def add_todo(self, title: str, user_id: int | None = None) -> int:
        cur = await self.db.execute(
            "INSERT INTO todos(title, created_by, created_at) VALUES (?, ?, ?)",
            (title.strip(), user_id, time.time()),
        )
        await self.db.commit()
        return cur.lastrowid or 0

    async def list_todos(self, include_done: bool = False) -> list[dict[str, Any]]:
        if include_done:
            cur = await self.db.execute(
                "SELECT id, title, done, created_by, created_at, done_at FROM todos ORDER BY done, id DESC"
            )
        else:
            cur = await self.db.execute(
                "SELECT id, title, done, created_by, created_at, done_at FROM todos WHERE done = 0 ORDER BY id DESC"
            )
        return [dict(r) for r in await cur.fetchall()]

    async def complete_todo(self, todo_id: int) -> bool:
        cur = await self.db.execute(
            "UPDATE todos SET done = 1, done_at = ? WHERE id = ? AND done = 0",
            (time.time(), todo_id),
        )
        await self.db.commit()
        return cur.rowcount > 0

    # ── notes ─────────────────────────────────────────────────
    async def upsert_note(
        self, title: str, body: str, tags: str = "", user_id: int | None = None
    ) -> None:
        key_title = title.strip()
        cur = await self.db.execute("SELECT id FROM notes WHERE lower(title) = lower(?)", (key_title,))
        row = await cur.fetchone()
        if row:
            await self.db.execute(
                "UPDATE notes SET body = ?, tags = ?, updated_by = ?, updated_at = ? WHERE id = ?",
                (body, tags, user_id, time.time(), row["id"]),
            )
        else:
            await self.db.execute(
                "INSERT INTO notes(title, body, tags, updated_by, updated_at) VALUES (?, ?, ?, ?, ?)",
                (key_title, body, tags, user_id, time.time()),
            )
        await self.db.commit()

    async def list_notes(self) -> list[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT id, title, body, tags, updated_by, updated_at FROM notes ORDER BY updated_at DESC"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_note(self, title: str) -> dict[str, Any] | None:
        cur = await self.db.execute(
            "SELECT id, title, body, tags, updated_by, updated_at FROM notes WHERE lower(title) = lower(?)",
            (title.strip(),),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    # ── events / calendar-ish ─────────────────────────────────
    async def add_event(
        self, title: str, when_text: str, notes: str = "", user_id: int | None = None
    ) -> int:
        cur = await self.db.execute(
            "INSERT INTO events(title, when_text, notes, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
            (title.strip(), when_text.strip(), notes.strip(), user_id, time.time()),
        )
        await self.db.commit()
        return cur.lastrowid or 0

    async def list_events(self) -> list[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT id, title, when_text, notes, created_by, created_at FROM events ORDER BY id DESC LIMIT 50"
        )
        return [dict(r) for r in await cur.fetchall()]

    # ── shopping ──────────────────────────────────────────────
    async def shop_add(self, item: str, qty: str = "1", user_id: int | None = None) -> int:
        cur = await self.db.execute(
            "INSERT INTO shopping(item, qty, created_by, created_at) VALUES (?, ?, ?, ?)",
            (item.strip(), (qty or "1").strip(), user_id, time.time()),
        )
        await self.db.commit()
        return cur.lastrowid or 0

    async def shop_list(self, include_done: bool = False) -> list[dict[str, Any]]:
        if include_done:
            cur = await self.db.execute(
                "SELECT id, item, qty, done, created_by, created_at, done_at FROM shopping ORDER BY done, id DESC"
            )
        else:
            cur = await self.db.execute(
                "SELECT id, item, qty, done, created_by, created_at, done_at FROM shopping WHERE done = 0 ORDER BY id DESC"
            )
        return [dict(r) for r in await cur.fetchall()]

    async def shop_done(self, item_id: int) -> bool:
        cur = await self.db.execute(
            "UPDATE shopping SET done = 1, done_at = ? WHERE id = ? AND done = 0",
            (time.time(), item_id),
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def shop_clear_done(self) -> int:
        cur = await self.db.execute("DELETE FROM shopping WHERE done = 1")
        await self.db.commit()
        return cur.rowcount

    # ── inventory ─────────────────────────────────────────────
    async def inventory_set(
        self,
        item: str,
        qty: str = "1",
        location: str = "home",
        notes: str = "",
        user_id: int | None = None,
    ) -> None:
        await self.db.execute(
            """INSERT INTO inventory(item, qty, location, notes, updated_by, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(item) DO UPDATE SET
                 qty=excluded.qty, location=excluded.location, notes=excluded.notes,
                 updated_by=excluded.updated_by, updated_at=excluded.updated_at""",
            (
                item.strip().lower(),
                (qty or "1").strip(),
                (location or "home").strip(),
                notes or "",
                user_id,
                time.time(),
            ),
        )
        await self.db.commit()

    async def inventory_list(self) -> list[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT item, qty, location, notes, updated_by, updated_at FROM inventory ORDER BY item"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def inventory_get(self, item: str) -> dict[str, Any] | None:
        cur = await self.db.execute(
            "SELECT item, qty, location, notes FROM inventory WHERE item = ?",
            (item.strip().lower(),),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def inventory_delete(self, item: str) -> bool:
        cur = await self.db.execute(
            "DELETE FROM inventory WHERE item = ?", (item.strip().lower(),)
        )
        await self.db.commit()
        return cur.rowcount > 0

    # ── people ────────────────────────────────────────────────
    async def person_set(
        self,
        name: str,
        relation: str = "",
        notes: str = "",
        prefs: str = "",
        user_id: int | None = None,
    ) -> None:
        await self.db.execute(
            """INSERT INTO people(name, relation, notes, prefs, updated_by, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                 relation=excluded.relation, notes=excluded.notes, prefs=excluded.prefs,
                 updated_by=excluded.updated_by, updated_at=excluded.updated_at""",
            (
                name.strip(),
                relation or "",
                notes or "",
                prefs or "",
                user_id,
                time.time(),
            ),
        )
        await self.db.commit()

    async def people_list(self) -> list[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT name, relation, notes, prefs, updated_by, updated_at FROM people ORDER BY name"
        )
        return [dict(r) for r in await cur.fetchall()]

    # ── settings ──────────────────────────────────────────────
    async def set_setting(self, key: str, value: str) -> None:
        await self.db.execute(
            """INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (key.strip().lower(), value, time.time()),
        )
        await self.db.commit()

    async def get_setting(self, key: str, default: str = "") -> str:
        cur = await self.db.execute(
            "SELECT value FROM settings WHERE key = ?", (key.strip().lower(),)
        )
        row = await cur.fetchone()
        return row["value"] if row else default

    async def list_settings(self) -> list[dict[str, Any]]:
        cur = await self.db.execute("SELECT key, value, updated_at FROM settings ORDER BY key")
        return [dict(r) for r in await cur.fetchall()]

    # ── search across store ───────────────────────────────────
    async def search_all(self, query: str, limit: int = 25) -> dict[str, list[dict[str, Any]]]:
        q = f"%{query.strip().lower()}%"
        out: dict[str, list[dict[str, Any]]] = {}
        cur = await self.db.execute(
            """SELECT key, value, category FROM memories
               WHERE lower(key) LIKE ? OR lower(value) LIKE ? LIMIT ?""",
            (q, q, limit),
        )
        out["memories"] = [dict(r) for r in await cur.fetchall()]
        cur = await self.db.execute(
            """SELECT id, title FROM todos
               WHERE done = 0 AND lower(title) LIKE ? LIMIT ?""",
            (q, limit),
        )
        out["todos"] = [dict(r) for r in await cur.fetchall()]
        cur = await self.db.execute(
            """SELECT id, item, qty FROM shopping
               WHERE done = 0 AND lower(item) LIKE ? LIMIT ?""",
            (q, limit),
        )
        out["shopping"] = [dict(r) for r in await cur.fetchall()]
        cur = await self.db.execute(
            """SELECT id, title, body FROM notes
               WHERE lower(title) LIKE ? OR lower(body) LIKE ? LIMIT ?""",
            (q, q, limit),
        )
        out["notes"] = [dict(r) for r in await cur.fetchall()]
        cur = await self.db.execute(
            """SELECT id, title, when_text FROM events
               WHERE lower(title) LIKE ? OR lower(when_text) LIKE ? LIMIT ?""",
            (q, q, limit),
        )
        out["events"] = [dict(r) for r in await cur.fetchall()]
        cur = await self.db.execute(
            """SELECT item, qty, location FROM inventory
               WHERE lower(item) LIKE ? OR lower(location) LIKE ? LIMIT ?""",
            (q, q, limit),
        )
        out["inventory"] = [dict(r) for r in await cur.fetchall()]
        cur = await self.db.execute(
            """SELECT name, relation, notes, prefs FROM people
               WHERE lower(name) LIKE ? OR lower(notes) LIKE ? OR lower(prefs) LIKE ? LIMIT ?""",
            (q, q, q, limit),
        )
        out["people"] = [dict(r) for r in await cur.fetchall()]
        return out

    async def snapshot_for_prompt(self) -> str:
        """Compact shared brain dump for the system prompt."""
        mems = await self.list_memories()
        todos = await self.list_todos(include_done=False)
        shop = await self.shop_list(include_done=False)
        notes = await self.list_notes()
        events = await self.list_events()
        inv = await self.inventory_list()
        people = await self.people_list()
        settings = await self.list_settings()

        lines = ["## Shared household memory (visible to both partners)"]
        if settings:
            lines.append("### Household settings")
            for s in settings:
                lines.append(f"- {s['key']}: {s['value']}")

        if mems:
            lines.append("### Facts")
            for m in mems:
                lines.append(f"- [{m['category']}] {m['key']}: {m['value']}")
        else:
            lines.append("### Facts\n- (empty — learn from conversation and save with tools)")

        lines.append("### Open todos")
        if todos:
            for t in todos:
                lines.append(f"- #{t['id']} {t['title']}")
        else:
            lines.append("- (none)")

        lines.append("### Shopping list")
        if shop:
            for s in shop:
                lines.append(f"- #{s['id']} {s['item']} × {s['qty']}")
        else:
            lines.append("- (empty)")

        lines.append("### Notes")
        if notes:
            for n in notes[:15]:
                body = (n["body"] or "")[:160]
                lines.append(f"- {n['title']}: {body}")
        else:
            lines.append("- (none)")

        lines.append("### Events")
        if events:
            for e in events[:15]:
                lines.append(
                    f"- {e['title']} @ {e['when_text']}"
                    + (f" — {e['notes']}" if e["notes"] else "")
                )
        else:
            lines.append("- (none)")

        lines.append("### Inventory (at home)")
        if inv:
            for i in inv[:30]:
                lines.append(f"- {i['item']}: {i['qty']} @ {i['location']}")
        else:
            lines.append("- (empty)")

        lines.append("### People")
        if people:
            for p in people:
                bits = [p["name"]]
                if p["relation"]:
                    bits.append(f"({p['relation']})")
                if p["prefs"]:
                    bits.append(f"prefs: {p['prefs']}")
                lines.append("- " + " ".join(bits))
        else:
            lines.append("- (none yet)")

        return "\n".join(lines)
