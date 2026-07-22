from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict, deque
from typing import Any

from app import google_client
from app.calendar_service import CalendarService


WORK_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'planned',
    owner_id INTEGER,
    start_at TEXT,
    due_at TEXT,
    priority TEXT NOT NULL DEFAULT 'normal',
    drive_folder_id TEXT NOT NULL DEFAULT '',
    drive_folder_url TEXT NOT NULL DEFAULT '',
    created_by INTEGER,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_projects_household ON projects(household_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS task_relationships (
    source_task_id INTEGER NOT NULL,
    target_task_id INTEGER NOT NULL,
    relationship_type TEXT NOT NULL,
    created_by INTEGER,
    created_at REAL NOT NULL,
    PRIMARY KEY(source_task_id, target_task_id, relationship_type)
);

CREATE TABLE IF NOT EXISTS task_calendar_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    google_event_id TEXT NOT NULL UNIQUE,
    block_type TEXT NOT NULL DEFAULT 'work',
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    sync_status TEXT NOT NULL DEFAULT 'synced',
    created_by INTEGER,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_calendar_blocks_task ON task_calendar_blocks(task_id, start_at);

CREATE TABLE IF NOT EXISTS task_resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    project_id INTEGER,
    provider TEXT NOT NULL DEFAULT 'google_drive',
    google_file_id TEXT NOT NULL DEFAULT '',
    file_name TEXT NOT NULL,
    mime_type TEXT NOT NULL DEFAULT '',
    web_url TEXT NOT NULL,
    relationship TEXT NOT NULL DEFAULT 'attachment',
    created_by INTEGER,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_resources_task ON task_resources(task_id, created_at DESC);
"""

PROJECT_STATUSES = {"planned", "active", "paused", "completed", "cancelled"}
TASK_STATUSES = {"todo", "in_progress", "waiting", "completed", "cancelled"}
PRIORITIES = {"low", "normal", "high"}
RELATIONSHIPS = {"blocks", "related", "follows", "duplicates"}


class WorkService:
    """Projects and household tasks, with optional Google work blocks/resources."""

    def __init__(self, settings, store, calendar: CalendarService | None = None):
        self.settings = settings
        self.store = store
        self.calendar = calendar or CalendarService(settings, store)
        self._ready = False

    async def ensure_schema(self) -> None:
        if self._ready:
            return
        await self.store.db.executescript(WORK_SCHEMA)
        columns = {
            str(row["name"])
            for row in await (await self.store.db.execute("PRAGMA table_info(todos)")).fetchall()
        }
        additions = {
            "project_id": "INTEGER",
            "parent_task_id": "INTEGER",
            "description": "TEXT NOT NULL DEFAULT ''",
            "status": "TEXT NOT NULL DEFAULT 'todo'",
            "recurrence_rule": "TEXT NOT NULL DEFAULT ''",
            "estimate_minutes": "INTEGER",
            "completed_at": "REAL",
            "archived_at": "REAL",
            "updated_at": "REAL",
        }
        for name, definition in additions.items():
            if name not in columns:
                await self.store.db.execute(f"ALTER TABLE todos ADD COLUMN {name} {definition}")
        await self.store.db.execute(
            """UPDATE todos
               SET status=CASE WHEN done=1 THEN 'completed' ELSE COALESCE(NULLIF(status,''),'todo') END,
                   completed_at=CASE WHEN done=1 THEN COALESCE(completed_at, done_at, created_at) ELSE completed_at END,
                   updated_at=COALESCE(updated_at, created_at)"""
        )
        await self.store.db.commit()
        self._ready = True

    @staticmethod
    def _validate(value: str, allowed: set[str], field: str) -> str:
        if value not in allowed:
            raise ValueError(f"Invalid {field}: {value}")
        return value

    async def list_projects(self, *, include_closed: bool = False) -> list[dict[str, Any]]:
        await self.ensure_schema()
        where = "" if include_closed else "AND p.status NOT IN ('completed','cancelled')"
        rows = await (
            await self.store.db.execute(
                f"""SELECT p.*, m.display_name AS owner_name,
                           COUNT(t.id) AS task_count,
                           SUM(CASE WHEN t.status='completed' THEN 1 ELSE 0 END) AS completed_count
                    FROM projects p
                    LEFT JOIN household_members m
                      ON m.household_id=p.household_id AND m.telegram_user_id=p.owner_id
                    LEFT JOIN todos t ON t.project_id=p.id AND t.archived_at IS NULL
                    WHERE p.household_id=? {where}
                    GROUP BY p.id
                    ORDER BY CASE p.status WHEN 'active' THEN 0 WHEN 'planned' THEN 1 ELSE 2 END,
                             COALESCE(p.due_at, '9999') ASC, p.updated_at DESC""",
                (self.store.household_id,),
            )
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            total = int(item.get("task_count") or 0)
            completed = int(item.get("completed_count") or 0)
            item["progress"] = round(completed / total * 100) if total else 0
            result.append(item)
        return result

    async def get_project(self, project_id: int) -> dict[str, Any] | None:
        rows = await self.list_projects(include_closed=True)
        return next((row for row in rows if int(row["id"]) == int(project_id)), None)

    async def create_project(
        self,
        actor_id: int,
        *,
        name: str,
        description: str = "",
        status: str = "planned",
        owner_id: int | None = None,
        start_at: str | None = None,
        due_at: str | None = None,
        priority: str = "normal",
        create_drive_folder: bool = False,
    ) -> dict[str, Any]:
        await self.ensure_schema()
        self._validate(status, PROJECT_STATUSES, "project status")
        self._validate(priority, PRIORITIES, "priority")
        now = time.time()
        cur = await self.store.db.execute(
            """INSERT INTO projects(household_id, name, description, status, owner_id,
                   start_at, due_at, priority, created_by, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                self.store.household_id,
                name.strip(),
                description.strip(),
                status,
                owner_id,
                start_at,
                due_at,
                priority,
                actor_id,
                now,
                now,
            ),
        )
        project_id = int(cur.lastrowid or 0)
        await self.store.db.commit()
        if create_drive_folder and self.settings.google_enabled:
            await self.ensure_project_folder(actor_id, project_id)
        await self.store.add_activity(actor_id, "created", "project", str(project_id), f"נוצר פרויקט: {name.strip()}")
        return await self.get_project(project_id) or {}

    async def update_project(self, actor_id: int, project_id: int, **changes: Any) -> dict[str, Any] | None:
        await self.ensure_schema()
        allowed = {"name", "description", "status", "owner_id", "start_at", "due_at", "priority"}
        values = {k: v for k, v in changes.items() if k in allowed}
        if "status" in values:
            self._validate(str(values["status"]), PROJECT_STATUSES, "project status")
        if "priority" in values:
            self._validate(str(values["priority"]), PRIORITIES, "priority")
        if not values:
            return await self.get_project(project_id)
        values["updated_at"] = time.time()
        assignments = ", ".join(f"{key}=?" for key in values)
        cur = await self.store.db.execute(
            f"UPDATE projects SET {assignments} WHERE id=? AND household_id=?",
            (*values.values(), int(project_id), self.store.household_id),
        )
        await self.store.db.commit()
        if cur.rowcount == 0:
            return None
        await self.store.add_activity(actor_id, "updated", "project", str(project_id), "הפרויקט עודכן", values)
        return await self.get_project(project_id)

    async def delete_project(self, actor_id: int, project_id: int) -> bool:
        await self.ensure_schema()
        await self.store.db.execute("UPDATE todos SET project_id=NULL WHERE project_id=?", (int(project_id),))
        cur = await self.store.db.execute(
            "DELETE FROM projects WHERE id=? AND household_id=?",
            (int(project_id), self.store.household_id),
        )
        await self.store.db.commit()
        if cur.rowcount:
            await self.store.add_activity(actor_id, "deleted", "project", str(project_id), "פרויקט נמחק")
        return cur.rowcount > 0

    async def ensure_project_folder(self, actor_id: int, project_id: int) -> dict[str, Any]:
        project = await self.get_project(project_id)
        if not project:
            raise ValueError("Project not found")
        if project.get("drive_folder_id"):
            return {"id": project["drive_folder_id"], "url": project.get("drive_folder_url", "")}
        folder = await asyncio.to_thread(
            google_client.drive_create_folder,
            self.settings,
            name=project["name"],
            parent_id=self.settings.google_docs_folder_id or "",
            app_properties={"sharedHomeProjectId": str(project_id), "householdId": self.store.household_id},
        )
        await self.store.db.execute(
            "UPDATE projects SET drive_folder_id=?, drive_folder_url=?, updated_at=? WHERE id=?",
            (folder["id"], folder["url"], time.time(), int(project_id)),
        )
        await self.store.db.commit()
        await self.store.add_activity(actor_id, "created", "project_folder", str(project_id), f"נוצרה תיקיית Drive לפרויקט {project['name']}")
        return folder

    async def list_tasks(
        self,
        *,
        include_done: bool = False,
        project_id: int | None = None,
        parent_task_id: int | None = None,
    ) -> list[dict[str, Any]]:
        await self.ensure_schema()
        clauses = ["t.archived_at IS NULL"]
        params: list[Any] = []
        if not include_done:
            clauses.append("t.status NOT IN ('completed','cancelled')")
        if project_id is not None:
            clauses.append("t.project_id=?")
            params.append(int(project_id))
        if parent_task_id is not None:
            clauses.append("t.parent_task_id=?")
            params.append(int(parent_task_id))
        rows = await (
            await self.store.db.execute(
                f"""SELECT t.*, p.name AS project_name, m.display_name AS assigned_name
                    FROM todos t
                    LEFT JOIN projects p ON p.id=t.project_id
                    LEFT JOIN household_members m
                      ON m.household_id=? AND m.telegram_user_id=t.assigned_to
                    WHERE {' AND '.join(clauses)}
                    ORDER BY CASE t.status WHEN 'in_progress' THEN 0 WHEN 'waiting' THEN 1 ELSE 2 END,
                             CASE t.priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
                             COALESCE(t.due_at, '9999') ASC, t.id DESC""",
                (self.store.household_id, *params),
            )
        ).fetchall()
        return [await self._hydrate_task(dict(row)) for row in rows]

    async def get_task(self, task_id: int) -> dict[str, Any] | None:
        await self.ensure_schema()
        row = await (
            await self.store.db.execute(
                """SELECT t.*, p.name AS project_name, m.display_name AS assigned_name
                   FROM todos t
                   LEFT JOIN projects p ON p.id=t.project_id
                   LEFT JOIN household_members m
                     ON m.household_id=? AND m.telegram_user_id=t.assigned_to
                   WHERE t.id=?""",
                (self.store.household_id, int(task_id)),
            )
        ).fetchone()
        return await self._hydrate_task(dict(row)) if row else None

    async def _hydrate_task(self, item: dict[str, Any]) -> dict[str, Any]:
        task_id = int(item["id"])
        blockers = await (
            await self.store.db.execute(
                """SELECT source.id, source.title, source.status
                   FROM task_relationships r
                   JOIN todos source ON source.id=r.source_task_id
                   WHERE r.target_task_id=? AND r.relationship_type='blocks'
                     AND source.status NOT IN ('completed','cancelled')""",
                (task_id,),
            )
        ).fetchall()
        relationships = await (
            await self.store.db.execute(
                """SELECT r.source_task_id, r.target_task_id, r.relationship_type,
                          s.title AS source_title, t.title AS target_title
                   FROM task_relationships r
                   JOIN todos s ON s.id=r.source_task_id
                   JOIN todos t ON t.id=r.target_task_id
                   WHERE r.source_task_id=? OR r.target_task_id=?""",
                (task_id, task_id),
            )
        ).fetchall()
        blocks = await (
            await self.store.db.execute(
                "SELECT * FROM task_calendar_blocks WHERE task_id=? ORDER BY start_at",
                (task_id,),
            )
        ).fetchall()
        resources = await (
            await self.store.db.execute(
                "SELECT * FROM task_resources WHERE task_id=? ORDER BY created_at DESC",
                (task_id,),
            )
        ).fetchall()
        item["done"] = 1 if item.get("status") == "completed" else 0
        item["blocked"] = bool(blockers)
        item["blockers"] = [dict(row) for row in blockers]
        item["relationships"] = [dict(row) for row in relationships]
        item["calendar_blocks"] = [dict(row) for row in blocks]
        item["resources"] = [dict(row) for row in resources]
        return item

    async def create_task(
        self,
        actor_id: int,
        *,
        title: str,
        description: str = "",
        project_id: int | None = None,
        parent_task_id: int | None = None,
        status: str = "todo",
        priority: str = "normal",
        assigned_to: int | None = None,
        due_at: str | None = None,
        recurrence_rule: str = "",
        estimate_minutes: int | None = None,
    ) -> dict[str, Any]:
        await self.ensure_schema()
        self._validate(status, TASK_STATUSES, "task status")
        self._validate(priority, PRIORITIES, "priority")
        now = time.time()
        done = int(status == "completed")
        cur = await self.store.db.execute(
            """INSERT INTO todos(title, description, project_id, parent_task_id, status,
                   priority, assigned_to, due_at, recurrence_rule, estimate_minutes,
                   done, created_by, created_at, updated_at, completed_at, done_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                title.strip(), description.strip(), project_id, parent_task_id, status,
                priority, assigned_to, due_at, recurrence_rule.strip(), estimate_minutes,
                done, actor_id, now, now, now if done else None, now if done else None,
            ),
        )
        task_id = int(cur.lastrowid or 0)
        await self.store.db.commit()
        await self.store.add_activity(actor_id, "created", "todo", str(task_id), f"נוספה משימה: {title.strip()}")
        return await self.get_task(task_id) or {}

    async def update_task(self, actor_id: int, task_id: int, **changes: Any) -> dict[str, Any] | None:
        await self.ensure_schema()
        allowed = {
            "title", "description", "project_id", "parent_task_id", "status", "priority",
            "assigned_to", "due_at", "recurrence_rule", "estimate_minutes", "archived_at",
        }
        values = {k: v for k, v in changes.items() if k in allowed}
        if "done" in changes and "status" not in values:
            values["status"] = "completed" if changes["done"] else "todo"
        if "status" in values:
            self._validate(str(values["status"]), TASK_STATUSES, "task status")
            completed = values["status"] == "completed"
            values["done"] = int(completed)
            values["completed_at"] = time.time() if completed else None
            values["done_at"] = time.time() if completed else None
        if "priority" in values:
            self._validate(str(values["priority"]), PRIORITIES, "priority")
        values["updated_at"] = time.time()
        assignments = ", ".join(f"{key}=?" for key in values)
        cur = await self.store.db.execute(
            f"UPDATE todos SET {assignments} WHERE id=?",
            (*values.values(), int(task_id)),
        )
        await self.store.db.commit()
        if cur.rowcount == 0:
            return None
        task = await self.get_task(task_id)
        action = "completed" if values.get("status") == "completed" else "updated"
        summary = f"הושלמה המשימה: {task['title']}" if action == "completed" and task else "המשימה עודכנה"
        await self.store.add_activity(actor_id, action, "todo", str(task_id), summary, values)
        return task

    async def delete_task(self, actor_id: int, task_id: int) -> bool:
        await self.ensure_schema()
        blocks = await (
            await self.store.db.execute(
                "SELECT google_event_id FROM task_calendar_blocks WHERE task_id=?",
                (int(task_id),),
            )
        ).fetchall()
        for block in blocks:
            try:
                await self.calendar.delete_event(actor_id, str(block["google_event_id"]))
            except Exception:
                pass
        await self.store.db.execute("DELETE FROM task_relationships WHERE source_task_id=? OR target_task_id=?", (int(task_id), int(task_id)))
        await self.store.db.execute("DELETE FROM task_calendar_blocks WHERE task_id=?", (int(task_id),))
        await self.store.db.execute("DELETE FROM task_resources WHERE task_id=?", (int(task_id),))
        await self.store.db.execute("UPDATE todos SET parent_task_id=NULL WHERE parent_task_id=?", (int(task_id),))
        cur = await self.store.db.execute("DELETE FROM todos WHERE id=?", (int(task_id),))
        await self.store.db.commit()
        if cur.rowcount:
            await self.store.add_activity(actor_id, "deleted", "todo", str(task_id), "משימה נמחקה")
        return cur.rowcount > 0

    async def add_relationship(
        self,
        actor_id: int,
        *,
        source_task_id: int,
        target_task_id: int,
        relationship_type: str,
    ) -> dict[str, Any]:
        await self.ensure_schema()
        self._validate(relationship_type, RELATIONSHIPS, "relationship")
        if source_task_id == target_task_id:
            raise ValueError("A task cannot be related to itself")
        if relationship_type in {"blocks", "follows"} and await self._would_cycle(source_task_id, target_task_id, relationship_type):
            raise ValueError("This relationship would create a dependency cycle")
        await self.store.db.execute(
            """INSERT OR IGNORE INTO task_relationships(source_task_id, target_task_id,
                   relationship_type, created_by, created_at) VALUES(?,?,?,?,?)""",
            (int(source_task_id), int(target_task_id), relationship_type, actor_id, time.time()),
        )
        await self.store.db.commit()
        await self.store.add_activity(actor_id, "created", "task_relationship", f"{source_task_id}:{target_task_id}", "נוסף קשר בין משימות")
        return {
            "source_task_id": int(source_task_id),
            "target_task_id": int(target_task_id),
            "relationship_type": relationship_type,
        }

    async def _would_cycle(self, source: int, target: int, relationship_type: str) -> bool:
        rows = await (
            await self.store.db.execute(
                "SELECT source_task_id, target_task_id FROM task_relationships WHERE relationship_type=?",
                (relationship_type,),
            )
        ).fetchall()
        graph: dict[int, list[int]] = defaultdict(list)
        for row in rows:
            graph[int(row["source_task_id"])].append(int(row["target_task_id"]))
        queue: deque[int] = deque([int(target)])
        seen: set[int] = set()
        while queue:
            node = queue.popleft()
            if node == int(source):
                return True
            if node in seen:
                continue
            seen.add(node)
            queue.extend(graph[node])
        return False

    async def delete_relationship(self, source: int, target: int, relationship_type: str) -> bool:
        await self.ensure_schema()
        cur = await self.store.db.execute(
            "DELETE FROM task_relationships WHERE source_task_id=? AND target_task_id=? AND relationship_type=?",
            (int(source), int(target), relationship_type),
        )
        await self.store.db.commit()
        return cur.rowcount > 0

    async def create_calendar_block(
        self,
        actor_id: int,
        task_id: int,
        *,
        start_at: str,
        end_at: str,
        location: str = "",
        block_type: str = "work",
    ) -> dict[str, Any]:
        task = await self.get_task(task_id)
        if not task:
            raise ValueError("Task not found")
        if end_at <= start_at:
            raise ValueError("End time must be after start time")
        description = f"משימה ב־Shared Home: {task['title']}"
        if task.get("project_name"):
            description += f"\nפרויקט: {task['project_name']}"
        event = await self.calendar.create_event(
            actor_id,
            title=f"עבודה: {task['title']}",
            start_at=start_at,
            end_at=end_at,
            location=location,
            description=description,
            all_day=False,
            attendees=[],
            recurrence=[],
        )
        now = time.time()
        cur = await self.store.db.execute(
            """INSERT INTO task_calendar_blocks(task_id, google_event_id, block_type,
                   start_at, end_at, location, sync_status, created_by, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (int(task_id), event["id"], block_type, start_at, end_at, location, "synced", actor_id, now, now),
        )
        await self.store.db.commit()
        return dict((await (await self.store.db.execute("SELECT * FROM task_calendar_blocks WHERE id=?", (int(cur.lastrowid or 0),))).fetchone()))

    async def delete_calendar_block(self, actor_id: int, task_id: int, block_id: int) -> bool:
        await self.ensure_schema()
        row = await (
            await self.store.db.execute(
                "SELECT * FROM task_calendar_blocks WHERE id=? AND task_id=?",
                (int(block_id), int(task_id)),
            )
        ).fetchone()
        if not row:
            return False
        await self.calendar.delete_event(actor_id, str(row["google_event_id"]))
        await self.store.db.execute("DELETE FROM task_calendar_blocks WHERE id=?", (int(block_id),))
        await self.store.db.commit()
        return True

    async def add_resource(
        self,
        actor_id: int,
        task_id: int,
        *,
        file_name: str,
        web_url: str,
        google_file_id: str = "",
        mime_type: str = "",
        relationship: str = "attachment",
    ) -> dict[str, Any]:
        task = await self.get_task(task_id)
        if not task:
            raise ValueError("Task not found")
        cur = await self.store.db.execute(
            """INSERT INTO task_resources(task_id, project_id, google_file_id, file_name,
                   mime_type, web_url, relationship, created_by, created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (int(task_id), task.get("project_id"), google_file_id, file_name, mime_type, web_url, relationship, actor_id, time.time()),
        )
        await self.store.db.commit()
        row = await (await self.store.db.execute("SELECT * FROM task_resources WHERE id=?", (int(cur.lastrowid or 0),))).fetchone()
        return dict(row)

    async def create_task_doc(self, actor_id: int, task_id: int) -> dict[str, Any]:
        task = await self.get_task(task_id)
        if not task:
            raise ValueError("Task not found")
        project = await self.get_project(task["project_id"]) if task.get("project_id") else None
        body = [f"# {task['title']}"]
        if project:
            body.append(f"פרויקט: {project['name']}")
        if task.get("description"):
            body.append(task["description"])
        if task.get("due_at"):
            body.append(f"מועד: {task['due_at']}")
        doc = await asyncio.to_thread(google_client.doc_create, self.settings, title=task["title"], text="\n\n".join(body))
        if project:
            folder = await self.ensure_project_folder(actor_id, int(project["id"]))
            await asyncio.to_thread(google_client.drive_move_file, self.settings, file_id=doc["id"], folder_id=folder["id"])
        return await self.add_resource(actor_id, task_id, file_name=doc["title"], web_url=doc["url"], google_file_id=doc["id"], mime_type="application/vnd.google-apps.document", relationship="working_doc")

    async def create_task_sheet(self, actor_id: int, task_id: int, template: str = "tracker") -> dict[str, Any]:
        task = await self.get_task(task_id)
        if not task:
            raise ValueError("Task not found")
        templates = {
            "budget": ["פריט", "תקציב", "בפועל", "הערות"],
            "suppliers": ["ספק", "מחיר", "סטטוס", "איש קשר", "הערות"],
            "equipment": ["פריט", "כמות", "אחראי", "סטטוס"],
            "tracker": ["פריט", "אחראי", "סטטוס", "מועד", "הערות"],
        }
        sheet = await asyncio.to_thread(
            google_client.sheet_create,
            self.settings,
            title=task["title"],
            headers=templates.get(template, templates["tracker"]),
        )
        project = await self.get_project(task["project_id"]) if task.get("project_id") else None
        if project:
            folder = await self.ensure_project_folder(actor_id, int(project["id"]))
            await asyncio.to_thread(google_client.drive_move_file, self.settings, file_id=sheet["id"], folder_id=folder["id"])
        return await self.add_resource(actor_id, task_id, file_name=sheet["title"], web_url=sheet["url"], google_file_id=sheet["id"], mime_type="application/vnd.google-apps.spreadsheet", relationship="working_doc")
