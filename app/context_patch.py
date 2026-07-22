from __future__ import annotations

from app.calendar_service import CalendarService
from app.config import get_settings
from app.store_v2 import Store
from app.work_service import WorkService


_original_snapshot = Store.snapshot_for_prompt


async def _work_aware_snapshot(self: Store, query: str | None = None) -> str:
    original = await _original_snapshot(self, query)
    # Preserve the memory/search portion but replace legacy task/event sections.
    prefix = original.split("### Open todos", 1)[0].rstrip()
    settings = get_settings()
    work = WorkService(settings, self)
    calendar = CalendarService(settings, self)
    tasks = await work.list_tasks()
    projects = await work.list_projects()
    events = await calendar.list_events()
    shopping = await self.shop_list(False)
    people = await self.people_list()

    lines = [prefix, "### Active projects"]
    lines.extend(
        f"- #{project['id']} {project['name']} [{project['status']}] {project['progress']}%"
        for project in projects[:10]
    )
    lines.append("### Open tasks")
    lines.extend(
        f"- #{task['id']} {task['title']} [{task['status']}]"
        + (f" project={task['project_name']}" if task.get('project_name') else "")
        + (f" assigned={task['assigned_name']}" if task.get('assigned_name') else "")
        + (f" due={task['due_at']}" if task.get('due_at') else "")
        + (f" BLOCKED_BY={','.join(blocker['title'] for blocker in task['blockers'])}" if task.get('blocked') else "")
        for task in tasks[:20]
    )
    lines.append("### Shopping")
    lines.extend(f"- #{item['id']} {item['item']} × {item['qty']}" for item in shopping[:12])
    lines.append("### Google Calendar events")
    lines.extend(
        f"- id={event['id']} {event['title']} @ {event.get('start_at')} → {event.get('end_at')}"
        for event in events[:12]
    )
    lines.append("### People")
    lines.extend(f"- {item['name']} {item['relation']} {item['prefs']}" for item in people[:10])
    return "\n".join(lines)


Store.snapshot_for_prompt = _work_aware_snapshot
