from __future__ import annotations

import json

from app import tools
from app.work_service import WorkService


_original_run_tool = tools.run_tool


async def _run_tool(store, service, name, arguments, user_id, settings=None):
    if name == "todo_list" and settings is not None:
        work = WorkService(settings, store)
        rows = await work.list_tasks(
            include_done=bool(arguments.get("include_done")),
            project_id=arguments.get("project_id"),
        )
        return json.dumps({"ok": True, "todos": rows}, ensure_ascii=False)
    return await _original_run_tool(store, service, name, arguments, user_id, settings)


tools.run_tool = _run_tool
