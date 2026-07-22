from __future__ import annotations

import time
from typing import Any

from app.store_v2 import Store


TELEGRAM_SCHEMA = """
CREATE TABLE IF NOT EXISTS telegram_chats (
    chat_id INTEGER PRIMARY KEY,
    chat_type TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    username TEXT NOT NULL DEFAULT '',
    is_forum INTEGER NOT NULL DEFAULT 0,
    is_direct_messages INTEGER NOT NULL DEFAULT 0,
    parent_chat_id INTEGER,
    active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS telegram_topics (
    chat_id INTEGER NOT NULL,
    thread_id INTEGER NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    icon_custom_emoji_id TEXT,
    agent_id TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_by INTEGER,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (chat_id, thread_id)
);
CREATE INDEX IF NOT EXISTS idx_telegram_topics_agent
    ON telegram_topics(chat_id, agent_id, status);

CREATE TABLE IF NOT EXISTS telegram_memberships (
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    updated_at REAL NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS telegram_conversation_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_key TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    telegram_user_id INTEGER,
    telegram_username TEXT,
    display_name TEXT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_name TEXT,
    tool_call_id TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_telegram_conversation_scope
    ON telegram_conversation_messages(scope_key, agent_id, id DESC);

CREATE TABLE IF NOT EXISTS telegram_conversation_summaries (
    scope_key TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    last_message_id INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY (scope_key, agent_id)
);

CREATE TABLE IF NOT EXISTS telegram_updates (
    update_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 1,
    last_error TEXT NOT NULL DEFAULT '',
    received_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""


class TelegramStore:
    def __init__(self, store: Store):
        self.store = store

    async def ensure_schema(self) -> None:
        await self.store.db.executescript(TELEGRAM_SCHEMA)
        await self.store.db.commit()

    async def begin_update(self, update_id: int) -> bool:
        if update_id < 0:
            return True
        now = time.time()
        row = await (
            await self.store.db.execute(
                "SELECT status, updated_at FROM telegram_updates WHERE update_id=?", (update_id,)
            )
        ).fetchone()
        if row and row["status"] == "done":
            return False
        if row and row["status"] == "processing" and now - float(row["updated_at"]) < 120:
            return False
        await self.store.db.execute(
            """INSERT INTO telegram_updates(update_id, status, attempts, received_at, updated_at)
               VALUES(?, 'processing', 1, ?, ?)
               ON CONFLICT(update_id) DO UPDATE SET
                 status='processing', attempts=telegram_updates.attempts+1,
                 last_error='', updated_at=excluded.updated_at""",
            (update_id, now, now),
        )
        await self.store.db.commit()
        return True

    async def complete_update(self, update_id: int) -> None:
        if update_id < 0:
            return
        await self.store.db.execute(
            "UPDATE telegram_updates SET status='done', updated_at=? WHERE update_id=?",
            (time.time(), update_id),
        )
        await self.store.db.commit()

    async def fail_update(self, update_id: int, error: str) -> None:
        if update_id < 0:
            return
        await self.store.db.execute(
            "UPDATE telegram_updates SET status='failed', last_error=?, updated_at=? WHERE update_id=?",
            ((error or "")[:500], time.time(), update_id),
        )
        await self.store.db.commit()

    async def prune_updates(self, older_than_seconds: int = 7 * 86400) -> None:
        await self.store.db.execute(
            "DELETE FROM telegram_updates WHERE updated_at < ?", (time.time() - older_than_seconds,)
        )
        await self.store.db.commit()

    async def upsert_chat(
        self,
        *,
        chat_id: int,
        chat_type: str,
        title: str = "",
        username: str = "",
        is_forum: bool = False,
        is_direct_messages: bool = False,
        parent_chat_id: int | None = None,
        active: bool = True,
    ) -> None:
        now = time.time()
        await self.store.db.execute(
            """INSERT INTO telegram_chats(
                   chat_id, chat_type, title, username, is_forum, is_direct_messages,
                   parent_chat_id, active, created_at, updated_at
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(chat_id) DO UPDATE SET
                 chat_type=excluded.chat_type, title=excluded.title, username=excluded.username,
                 is_forum=excluded.is_forum, is_direct_messages=excluded.is_direct_messages,
                 parent_chat_id=excluded.parent_chat_id, active=excluded.active,
                 updated_at=excluded.updated_at""",
            (
                chat_id,
                chat_type,
                title,
                username,
                int(is_forum),
                int(is_direct_messages),
                parent_chat_id,
                int(active),
                now,
                now,
            ),
        )
        await self.store.db.commit()

    async def upsert_topic(
        self,
        *,
        chat_id: int,
        thread_id: int,
        name: str = "",
        icon_custom_emoji_id: str | None = None,
        created_by: int | None = None,
        status: str = "open",
    ) -> None:
        now = time.time()
        await self.store.db.execute(
            """INSERT INTO telegram_topics(
                   chat_id, thread_id, name, icon_custom_emoji_id, status,
                   created_by, created_at, updated_at
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(chat_id, thread_id) DO UPDATE SET
                 name=CASE WHEN excluded.name='' THEN telegram_topics.name ELSE excluded.name END,
                 icon_custom_emoji_id=COALESCE(excluded.icon_custom_emoji_id, telegram_topics.icon_custom_emoji_id),
                 status=excluded.status, updated_at=excluded.updated_at""",
            (chat_id, thread_id, name, icon_custom_emoji_id, status, created_by, now, now),
        )
        await self.store.db.commit()

    async def set_topic_status(self, chat_id: int, thread_id: int, status: str) -> None:
        await self.store.db.execute(
            "UPDATE telegram_topics SET status=?, updated_at=? WHERE chat_id=? AND thread_id=?",
            (status, time.time(), chat_id, thread_id),
        )
        await self.store.db.commit()

    async def rename_topic(self, chat_id: int, thread_id: int, name: str) -> None:
        await self.store.db.execute(
            "UPDATE telegram_topics SET name=?, updated_at=? WHERE chat_id=? AND thread_id=?",
            (name, time.time(), chat_id, thread_id),
        )
        await self.store.db.commit()

    async def bind_topic(self, chat_id: int, thread_id: int, agent_id: str | None) -> None:
        await self.upsert_topic(chat_id=chat_id, thread_id=thread_id)
        await self.store.db.execute(
            "UPDATE telegram_topics SET agent_id=?, updated_at=? WHERE chat_id=? AND thread_id=?",
            (agent_id, time.time(), chat_id, thread_id),
        )
        await self.store.db.commit()

    async def topic_agent(self, chat_id: int, thread_id: int | None) -> str | None:
        if thread_id is None:
            return None
        row = await (
            await self.store.db.execute(
                "SELECT agent_id FROM telegram_topics WHERE chat_id=? AND thread_id=?",
                (chat_id, thread_id),
            )
        ).fetchone()
        return str(row["agent_id"]) if row and row["agent_id"] else None

    async def get_topic(self, chat_id: int, thread_id: int) -> dict[str, Any] | None:
        row = await (
            await self.store.db.execute(
                "SELECT * FROM telegram_topics WHERE chat_id=? AND thread_id=?",
                (chat_id, thread_id),
            )
        ).fetchone()
        return dict(row) if row else None

    async def list_topics(self, chat_id: int) -> list[dict[str, Any]]:
        rows = await (
            await self.store.db.execute(
                "SELECT * FROM telegram_topics WHERE chat_id=? ORDER BY status, updated_at DESC",
                (chat_id,),
            )
        ).fetchall()
        return [dict(row) for row in rows]

    async def record_membership(self, chat_id: int, user_id: int, status: str, role: str = "member") -> None:
        await self.store.db.execute(
            """INSERT INTO telegram_memberships(chat_id, user_id, status, role, updated_at)
               VALUES(?, ?, ?, ?, ?)
               ON CONFLICT(chat_id, user_id) DO UPDATE SET
                 status=excluded.status, role=excluded.role, updated_at=excluded.updated_at""",
            (chat_id, user_id, status, role, time.time()),
        )
        await self.store.db.commit()

    async def add_message(
        self,
        *,
        scope_key: str,
        agent_id: str,
        role: str,
        content: str,
        user_id: int | None = None,
        username: str | None = None,
        display_name: str | None = None,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
    ) -> int:
        cur = await self.store.db.execute(
            """INSERT INTO telegram_conversation_messages(
                   scope_key, agent_id, telegram_user_id, telegram_username,
                   display_name, role, content, tool_name, tool_call_id, created_at
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                scope_key,
                agent_id,
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
        await self.store.db.commit()
        return int(cur.lastrowid or 0)

    async def recent_messages(self, scope_key: str, agent_id: str, limit: int) -> list[dict[str, Any]]:
        rows = await (
            await self.store.db.execute(
                """SELECT * FROM telegram_conversation_messages
                   WHERE scope_key=? AND agent_id=? ORDER BY id DESC LIMIT ?""",
                (scope_key, agent_id, limit),
            )
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    async def messages_after(
        self, scope_key: str, agent_id: str, after_id: int, limit: int = 400
    ) -> list[dict[str, Any]]:
        rows = await (
            await self.store.db.execute(
                """SELECT * FROM telegram_conversation_messages
                   WHERE scope_key=? AND agent_id=? AND id>? ORDER BY id ASC LIMIT ?""",
                (scope_key, agent_id, after_id, limit),
            )
        ).fetchall()
        return [dict(row) for row in rows]

    async def get_summary(self, scope_key: str, agent_id: str) -> tuple[str, int]:
        row = await (
            await self.store.db.execute(
                """SELECT summary, last_message_id FROM telegram_conversation_summaries
                   WHERE scope_key=? AND agent_id=?""",
                (scope_key, agent_id),
            )
        ).fetchone()
        if not row:
            return "", 0
        return str(row["summary"] or ""), int(row["last_message_id"] or 0)

    async def set_summary(self, scope_key: str, agent_id: str, summary: str, last_message_id: int) -> None:
        await self.store.db.execute(
            """INSERT INTO telegram_conversation_summaries(
                   scope_key, agent_id, summary, last_message_id, updated_at
               ) VALUES(?, ?, ?, ?, ?)
               ON CONFLICT(scope_key, agent_id) DO UPDATE SET
                 summary=excluded.summary, last_message_id=excluded.last_message_id,
                 updated_at=excluded.updated_at""",
            (scope_key, agent_id, summary[:2500], last_message_id, time.time()),
        )
        await self.store.db.commit()
