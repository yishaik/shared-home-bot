from __future__ import annotations

import time
from typing import Any


class MemberService:
    """Telegram-backed household identity.

    Telegram ids and chat ids remain internal. The Mini App only receives active
    members that have actually interacted with the bot and have a display name.
    """

    def __init__(self, store):
        self.store = store
        self._ready = False

    async def ensure_schema(self) -> None:
        if self._ready:
            return
        columns = {
            str(row["name"])
            for row in await (await self.store.db.execute("PRAGMA table_info(household_members)")).fetchall()
        }
        additions = {
            "private_chat_id": "INTEGER",
            "bot_started_at": "REAL",
            "last_seen_at": "REAL",
            "is_active": "INTEGER NOT NULL DEFAULT 0",
            "google_email": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in additions.items():
            if name not in columns:
                await self.store.db.execute(
                    f"ALTER TABLE household_members ADD COLUMN {name} {definition}"
                )
        await self.store.db.commit()
        self._ready = True

    async def touch(
        self,
        user_id: int,
        display_name: str,
        username: str = "",
        *,
        private_chat_id: int | None = None,
        started: bool = False,
    ) -> None:
        await self.ensure_schema()
        now = time.time()
        await self.store.db.execute(
            """UPDATE household_members
               SET display_name=?, username=?,
                   private_chat_id=COALESCE(?, private_chat_id),
                   bot_started_at=CASE
                       WHEN ?=1 THEN COALESCE(bot_started_at, ?)
                       ELSE bot_started_at
                   END,
                   last_seen_at=?, is_active=1
               WHERE household_id=? AND telegram_user_id=?""",
            (
                (display_name or "").strip(),
                (username or "").strip(),
                private_chat_id,
                int(started),
                now,
                now,
                self.store.household_id,
                int(user_id),
            ),
        )
        await self.store.db.commit()

    async def list_active(self) -> list[dict[str, Any]]:
        await self.ensure_schema()
        rows = await (
            await self.store.db.execute(
                """SELECT telegram_user_id, display_name, username, role,
                          notification_mode, bot_started_at, last_seen_at,
                          CASE WHEN private_chat_id IS NOT NULL THEN 1 ELSE 0 END AS can_receive_dm,
                          google_email
                   FROM household_members
                   WHERE household_id=? AND is_active=1 AND TRIM(display_name)!=''
                   ORDER BY COALESCE(last_seen_at, joined_at) DESC""",
                (self.store.household_id,),
            )
        ).fetchall()
        return [dict(row) for row in rows]

    async def get(self, user_id: int) -> dict[str, Any] | None:
        await self.ensure_schema()
        row = await (
            await self.store.db.execute(
                """SELECT telegram_user_id, display_name, username, role,
                          notification_mode, bot_started_at, last_seen_at,
                          private_chat_id, google_email
                   FROM household_members
                   WHERE household_id=? AND telegram_user_id=?""",
                (self.store.household_id, int(user_id)),
            )
        ).fetchone()
        return dict(row) if row else None
