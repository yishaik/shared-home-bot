from __future__ import annotations

from typing import Any

from app.notification_service import NotificationService
from app.work_service import WorkService


class HouseholdWorkService(WorkService):
    """WorkService plus durable Telegram assignment notifications."""

    def __init__(self, settings, store, calendar=None):
        super().__init__(settings, store, calendar)
        self.notifications = NotificationService(store, settings)

    async def create_task(self, actor_id: int, **payload: Any) -> dict[str, Any]:
        task = await super().create_task(actor_id, **payload)
        if task.get("assigned_to") and task.get("status") not in {"completed", "cancelled"}:
            await self.notifications.enqueue_task(int(task["assigned_to"]), task)
        return task

    async def update_task(self, actor_id: int, task_id: int, **changes: Any) -> dict[str, Any] | None:
        before = await self.get_task(task_id)
        task = await super().update_task(actor_id, task_id, **changes)
        if not task:
            return None
        assignment_changed = "assigned_to" in changes and task.get("assigned_to") != (before or {}).get("assigned_to")
        reopened = "status" in changes and task.get("status") in {"todo", "in_progress", "waiting"} and (before or {}).get("status") in {"completed", "cancelled"}
        if task.get("assigned_to") and task.get("status") not in {"completed", "cancelled"} and (assignment_changed or reopened):
            await self.notifications.enqueue_task(int(task["assigned_to"]), task, kind="task_updated")
        return task
