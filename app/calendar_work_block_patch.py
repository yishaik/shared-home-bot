from __future__ import annotations

import time

from app.calendar_service import CalendarService

_original_save_event = CalendarService._save_event


async def _save_event_and_reconcile(self: CalendarService, event):
    row = await _original_save_event(self, event)
    try:
        await self.store.db.execute(
            """UPDATE task_calendar_blocks SET start_at=?,end_at=?,location=?,sync_status=?,updated_at=?
               WHERE google_event_id=?""",
            (row.get("start_at"), row.get("end_at"), row.get("location") or "",
             "deleted" if row.get("status") == "cancelled" else "synced", time.time(), row.get("id")),
        )
        await self.store.db.commit()
    except Exception:
        # The work schema may not exist during isolated calendar migrations/tests.
        pass
    return row


CalendarService._save_event = _save_event_and_reconcile
