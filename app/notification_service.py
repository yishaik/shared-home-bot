from __future__ import annotations

import json
import time
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from app.member_service import MemberService


NOTIFICATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS telegram_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    available_at REAL NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    sent_at REAL
);
CREATE INDEX IF NOT EXISTS idx_telegram_notifications_pending
ON telegram_notifications(status, available_at, id);
"""


class NotificationService:
    def __init__(self, store, settings=None):
        self.store = store
        self.settings = settings
        self.members = MemberService(store)
        self._ready = False

    async def ensure_schema(self) -> None:
        if self._ready:
            return
        await self.store.db.executescript(NOTIFICATION_SCHEMA)
        await self.store.db.commit()
        self._ready = True

    async def enqueue_task(self, user_id: int | None, task: dict[str, Any], *, kind: str = "task_assigned") -> None:
        if not user_id:
            return
        await self.ensure_schema()
        payload = {
            "title": task.get("title", ""),
            "project_name": task.get("project_name", ""),
            "due_at": task.get("due_at"),
            "priority": task.get("priority", "normal"),
            "status": task.get("status", "todo"),
        }
        now = time.time()
        await self.store.db.execute(
            """INSERT INTO telegram_notifications(household_id, user_id, kind, entity_id,
                   payload_json, available_at, created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (
                self.store.household_id,
                int(user_id),
                kind,
                str(task.get("id", "")),
                json.dumps(payload, ensure_ascii=False),
                now,
                now,
            ),
        )
        await self.store.db.commit()

    async def deliver_pending(self, bot, *, limit: int = 20) -> int:
        await self.ensure_schema()
        rows = await (
            await self.store.db.execute(
                """SELECT * FROM telegram_notifications
                   WHERE household_id=? AND status='pending' AND available_at<=?
                   ORDER BY id LIMIT ?""",
                (self.store.household_id, time.time(), int(limit)),
            )
        ).fetchall()
        delivered = 0
        for raw in rows:
            row = dict(raw)
            member = await self.members.get(int(row["user_id"]))
            if not member or not member.get("private_chat_id"):
                await self._fail(row, "User has not opened a private chat with the bot", retry=False)
                continue
            try:
                payload = json.loads(row.get("payload_json") or "{}")
                text = self._task_text(payload)
                buttons = [[
                    InlineKeyboardButton("התחלתי", callback_data=f"todo_start:{row['entity_id']}"),
                    InlineKeyboardButton("הושלם", callback_data=f"todo_done:{row['entity_id']}"),
                ]]
                if self.settings and self.settings.resolved_mini_app_url:
                    buttons.append([
                        InlineKeyboardButton(
                            "פתיחת המשימה",
                            web_app=WebAppInfo(url=f"{self.settings.resolved_mini_app_url}?tab=tasks&task={row['entity_id']}"),
                        )
                    ])
                await bot.send_message(
                    chat_id=int(member["private_chat_id"]),
                    text=text,
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
                await self.store.db.execute(
                    "UPDATE telegram_notifications SET status='sent', sent_at=?, attempts=attempts+1 WHERE id=?",
                    (time.time(), int(row["id"])),
                )
                await self.store.db.commit()
                delivered += 1
            except Exception as exc:
                await self._fail(row, f"{type(exc).__name__}: {exc}", retry=True)
        return delivered

    @staticmethod
    def _task_text(payload: dict[str, Any]) -> str:
        lines = ["📌 משימה חדשה עבורך", "", str(payload.get("title") or "משימה")]
        if payload.get("project_name"):
            lines.append(f"פרויקט: {payload['project_name']}")
        if payload.get("due_at"):
            lines.append(f"מועד: {payload['due_at']}")
        if payload.get("priority") == "high":
            lines.append("עדיפות: גבוהה")
        return "\n".join(lines)

    async def _fail(self, row: dict[str, Any], error: str, *, retry: bool) -> None:
        attempts = int(row.get("attempts") or 0) + 1
        if retry and attempts < 5:
            status = "pending"
            available_at = time.time() + min(3600, 30 * (2 ** attempts))
        else:
            status = "failed"
            available_at = float(row.get("available_at") or time.time())
        await self.store.db.execute(
            """UPDATE telegram_notifications
               SET status=?, available_at=?, attempts=?, last_error=? WHERE id=?""",
            (status, available_at, attempts, error[:1000], int(row["id"])),
        )
        await self.store.db.commit()
