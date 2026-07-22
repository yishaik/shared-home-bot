from __future__ import annotations

from typing import Any

from app.notification_service import NotificationService
from app.work_service import WorkService


_original_create_task = WorkService.create_task
_original_update_task = WorkService.update_task


async def _create_task_with_notification(self: WorkService, actor_id: int, **payload: Any):
    task = await _original_create_task(self, actor_id, **payload)
    if task.get("assigned_to") and task.get("status") not in {"completed", "cancelled"}:
        await NotificationService(self.store, self.settings).enqueue_task(int(task["assigned_to"]), task)
    return task


async def _update_task_with_notification(self: WorkService, actor_id: int, task_id: int, **changes: Any):
    before = await self.get_task(task_id)
    task = await _original_update_task(self, actor_id, task_id, **changes)
    if not task:
        return None
    assignment_changed = "assigned_to" in changes and task.get("assigned_to") != (before or {}).get("assigned_to")
    reopened = (
        "status" in changes
        and task.get("status") in {"todo", "in_progress", "waiting"}
        and (before or {}).get("status") in {"completed", "cancelled"}
    )
    if task.get("assigned_to") and task.get("status") not in {"completed", "cancelled"} and (assignment_changed or reopened):
        await NotificationService(self.store, self.settings).enqueue_task(int(task["assigned_to"]), task, kind="task_updated")
    return task


WorkService.create_task = _create_task_with_notification
WorkService.update_task = _update_task_with_notification
