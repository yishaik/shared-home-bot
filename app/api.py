from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.calendar_service import CalendarService
from app.config import Settings
from app.member_service import MemberService
from app.memory_control import list_memory_audit, record_memory_audit, reflection_status, set_auto_memory_enabled
from app.schemas import (
    CoreMemoryUpdate, EventCreate, EventUpdate, HouseholdUpdate, MemorySettingsUpdate, MemoryUpdate,
    ProjectCreate, ProjectUpdate, ShoppingCreate, ShoppingUpdate, TaskCalendarBlockCreate,
    TaskRelationshipCreate, TaskResourceLinkCreate, TaskSheetCreate, TelegramAuthRequest,
    TodoCreate, TodoUpdate,
)
from app.security import AuthenticationError, SessionSigner, parse_telegram_user, validate_telegram_init_data
from app.services import HomeService
from app.store_v2 import Store
from app.work_service import WorkService


@dataclass(frozen=True)
class Actor:
    user_id: int
    household_id: str
    display_name: str


def build_api_router(settings: Settings, store: Store, service: HomeService) -> APIRouter:
    router = APIRouter(prefix="/api")
    signer = SessionSigner(settings.effective_session_secret, settings.session_ttl_seconds)
    calendar = CalendarService(settings, store)
    work = WorkService(settings, store, calendar)
    members = MemberService(store)

    async def current_actor(authorization: Annotated[str | None, Header()] = None) -> Actor:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing session")
        try:
            payload = signer.verify(authorization.removeprefix("Bearer ").strip())
            actor = Actor(user_id=int(payload["sub"]), household_id=str(payload["household_id"]), display_name=str(payload.get("name") or ""))
        except (AuthenticationError, KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session") from exc
        if actor.household_id != settings.household_id or not await store.is_member(actor.user_id, actor.household_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a household member")
        return actor

    @router.post("/auth/telegram")
    async def authenticate(body: TelegramAuthRequest) -> dict[str, Any]:
        try:
            payload = validate_telegram_init_data(body.init_data, settings.telegram_bot_token, max_age_seconds=settings.max_init_data_age_seconds)
            user = parse_telegram_user(payload)
        except AuthenticationError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
        if user.id not in settings.allowed_user_ids or not await store.is_member(user.id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a household member")
        await members.touch(user.id, user.display_name, user.username, started=True)
        return {
            "token": signer.issue(user_id=user.id, household_id=settings.household_id, display_name=user.display_name),
            "user": {"id": user.id, "name": user.display_name, "username": user.username},
            "household": await store.get_household(),
        }

    @router.get("/home")
    async def home(_: Actor = Depends(current_actor)) -> dict[str, Any]:
        dashboard = await store.dashboard()
        task_rows = await work.list_tasks()
        project_rows = await work.list_projects()
        dashboard["todos"] = task_rows[:5]
        dashboard["projects"] = project_rows[:5]
        dashboard["members"] = await members.list_active()
        dashboard["counts"]["todos"] = len(task_rows)
        dashboard["counts"]["projects"] = len(project_rows)
        try:
            events = await calendar.list_events()
            dashboard["events"] = events[:5]
            dashboard["counts"]["events"] = len(events)
            dashboard["calendar_status"] = await calendar.status()
        except Exception:
            dashboard["calendar_status"] = {"configured": settings.google_enabled, "last_error": "Calendar unavailable"}
        return dashboard

    @router.get("/activity")
    async def activity(_: Actor = Depends(current_actor), limit: int = Query(default=30, ge=1, le=100)) -> list[dict[str, Any]]:
        return await store.list_activity(limit)

    @router.get("/shopping")
    async def shopping(_: Actor = Depends(current_actor), include_done: bool = False) -> list[dict[str, Any]]:
        return await store.shop_list(include_done)

    @router.post("/shopping", status_code=status.HTTP_201_CREATED)
    async def create_shopping(body: ShoppingCreate, actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        return await service.add_shopping(actor.user_id, body.item, body.qty, body.category)

    @router.patch("/shopping/{item_id}")
    async def update_shopping(item_id: int, body: ShoppingUpdate, actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        item = await service.update_shopping(actor.user_id, item_id, **body.model_dump(exclude_none=True))
        if not item:
            raise HTTPException(status_code=404, detail="Shopping item not found")
        return item

    @router.delete("/shopping/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_shopping(item_id: int, actor: Actor = Depends(current_actor)) -> None:
        if not await service.delete_shopping(actor.user_id, item_id):
            raise HTTPException(status_code=404, detail="Shopping item not found")

    @router.get("/projects")
    async def projects(_: Actor = Depends(current_actor), include_closed: bool = False) -> list[dict[str, Any]]:
        return await work.list_projects(include_closed=include_closed)

    @router.get("/projects/{project_id}")
    async def get_project(project_id: int, _: Actor = Depends(current_actor)) -> dict[str, Any]:
        project = await work.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        project["tasks"] = await work.list_tasks(include_done=True, project_id=project_id)
        return project

    @router.post("/projects", status_code=status.HTTP_201_CREATED)
    async def create_project(body: ProjectCreate, actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        try:
            return await work.create_project(actor.user_id, **body.model_dump())
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.patch("/projects/{project_id}")
    async def update_project(project_id: int, body: ProjectUpdate, actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        try:
            project = await work.update_project(actor.user_id, project_id, **body.model_dump(exclude_unset=True))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        return project

    @router.post("/projects/{project_id}/drive-folder")
    async def create_project_folder(project_id: int, actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        try:
            return await work.ensure_project_folder(actor.user_id, project_id)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_project(project_id: int, actor: Actor = Depends(current_actor)) -> None:
        if not await work.delete_project(actor.user_id, project_id):
            raise HTTPException(status_code=404, detail="Project not found")

    @router.get("/tasks")
    async def tasks(_: Actor = Depends(current_actor), include_done: bool = False,
                    project_id: int | None = None, parent_task_id: int | None = None) -> list[dict[str, Any]]:
        return await work.list_tasks(include_done=include_done, project_id=project_id, parent_task_id=parent_task_id)

    @router.get("/tasks/{todo_id}")
    async def get_task(todo_id: int, _: Actor = Depends(current_actor)) -> dict[str, Any]:
        item = await work.get_task(todo_id)
        if not item:
            raise HTTPException(status_code=404, detail="Task not found")
        return item

    @router.post("/tasks", status_code=status.HTTP_201_CREATED)
    async def create_task(body: TodoCreate, actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        try:
            return await work.create_task(actor.user_id, **body.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.patch("/tasks/{todo_id}")
    async def update_task(todo_id: int, body: TodoUpdate, actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        try:
            item = await work.update_task(actor.user_id, todo_id, **body.model_dump(exclude_unset=True))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not item:
            raise HTTPException(status_code=404, detail="Task not found")
        return item

    @router.delete("/tasks/{todo_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_task(todo_id: int, actor: Actor = Depends(current_actor)) -> None:
        if not await work.delete_task(actor.user_id, todo_id):
            raise HTTPException(status_code=404, detail="Task not found")

    @router.post("/tasks/{todo_id}/relationships", status_code=status.HTTP_201_CREATED)
    async def add_task_relationship(todo_id: int, body: TaskRelationshipCreate,
                                    actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        if todo_id not in {body.source_task_id, body.target_task_id}:
            raise HTTPException(status_code=400, detail="Route task must participate in the relationship")
        try:
            return await work.add_relationship(actor.user_id, **body.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete("/tasks/{todo_id}/relationships/{source_id}/{target_id}/{relationship_type}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_task_relationship(todo_id: int, source_id: int, target_id: int, relationship_type: str,
                                       _: Actor = Depends(current_actor)) -> None:
        if todo_id not in {source_id, target_id} or not await work.delete_relationship(source_id, target_id, relationship_type):
            raise HTTPException(status_code=404, detail="Relationship not found")

    @router.post("/tasks/{todo_id}/calendar-blocks", status_code=status.HTTP_201_CREATED)
    async def create_task_calendar_block(todo_id: int, body: TaskCalendarBlockCreate,
                                         actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        try:
            return await work.create_calendar_block(actor.user_id, todo_id, **body.model_dump())
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete("/tasks/{todo_id}/calendar-blocks/{block_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_task_calendar_block(todo_id: int, block_id: int, actor: Actor = Depends(current_actor)) -> None:
        if not await work.delete_calendar_block(actor.user_id, todo_id, block_id):
            raise HTTPException(status_code=404, detail="Calendar block not found")

    @router.post("/tasks/{todo_id}/resources/link", status_code=status.HTTP_201_CREATED)
    async def add_task_resource_link(todo_id: int, body: TaskResourceLinkCreate,
                                     actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        try:
            return await work.add_resource(actor.user_id, todo_id, **body.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/tasks/{todo_id}/resources/doc", status_code=status.HTTP_201_CREATED)
    async def create_task_doc(todo_id: int, actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        try:
            return await work.create_task_doc(actor.user_id, todo_id)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/tasks/{todo_id}/resources/sheet", status_code=status.HTTP_201_CREATED)
    async def create_task_sheet(todo_id: int, body: TaskSheetCreate, actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        try:
            return await work.create_task_sheet(actor.user_id, todo_id, body.template)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/events")
    async def events(_: Actor = Depends(current_actor), sync: bool = False) -> list[dict[str, Any]]:
        if sync and settings.google_enabled:
            await calendar.incremental_sync()
        return await calendar.list_events()

    @router.get("/events/status")
    async def calendar_status(_: Actor = Depends(current_actor)) -> dict[str, Any]:
        return await calendar.status()

    @router.post("/events/sync")
    async def sync_events(_: Actor = Depends(current_actor), full: bool = False) -> dict[str, Any]:
        if not settings.google_enabled:
            raise HTTPException(status_code=503, detail="Google Calendar is not configured")
        return await (calendar.full_sync() if full else calendar.incremental_sync())

    @router.get("/events/{event_id}")
    async def get_event(event_id: str, _: Actor = Depends(current_actor)) -> dict[str, Any]:
        event = await calendar.get_event(event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")
        return event

    @router.post("/events", status_code=status.HTTP_201_CREATED)
    async def create_event(body: EventCreate, actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        try:
            return await calendar.create_event(actor.user_id, title=body.title, start_at=body.start_at,
                end_at=body.end_at, location=body.location, description=body.description or body.notes,
                all_day=body.all_day, attendees=body.attendees, recurrence=body.recurrence, reminders=body.reminders)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.patch("/events/{event_id}")
    async def update_event(event_id: str, body: EventUpdate, actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        changes = body.model_dump(exclude_unset=True)
        if "notes" in changes and "description" not in changes:
            changes["description"] = changes.pop("notes")
        try:
            return await calendar.update_event(actor.user_id, event_id, **changes)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_event(event_id: str, actor: Actor = Depends(current_actor)) -> None:
        try:
            await calendar.delete_event(actor.user_id, event_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.get("/memory")
    async def memory(_: Actor = Depends(current_actor)) -> list[dict[str, Any]]:
        return await store.list_memories(limit=100)

    @router.get("/memory/control")
    async def memory_control(_: Actor = Depends(current_actor)) -> dict[str, Any]:
        return {"status": await reflection_status(store), "core_memory": await store.get_core_memory(),
                "memories": await store.list_memories(limit=200), "audit": await list_memory_audit(store, limit=50)}

    @router.patch("/memory/settings")
    async def update_memory_settings(body: MemorySettingsUpdate, actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        await set_auto_memory_enabled(store, body.auto_memory_enabled, actor.user_id)
        await store.add_activity(actor.user_id, "updated", "memory", "settings", "הגדרות הזיכרון עודכנו")
        return await reflection_status(store)

    @router.patch("/memory/{memory_key}")
    async def update_memory(memory_key: str, body: MemoryUpdate, actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        old = await store.get_memory(memory_key)
        if not old:
            raise HTTPException(status_code=404, detail="Memory not found")
        await store.set_memory(memory_key, body.value, body.category, actor.user_id)
        await record_memory_audit(store, action="updated", memory_key=memory_key, old_value=old.get("value", ""),
                                  new_value=body.value, source="manual", actor_id=actor.user_id)
        return await store.get_memory(memory_key) or {}

    @router.delete("/memory/{memory_key}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_memory(memory_key: str, actor: Actor = Depends(current_actor)) -> None:
        old = await store.get_memory(memory_key)
        if not old or not await store.delete_memory(memory_key):
            raise HTTPException(status_code=404, detail="Memory not found")
        await record_memory_audit(store, action="deleted", memory_key=memory_key, old_value=old.get("value", ""),
                                  source="manual", actor_id=actor.user_id, metadata={"deleted_memory": old})

    @router.put("/memory/core")
    async def update_core_memory(body: CoreMemoryUpdate, actor: Actor = Depends(current_actor)) -> dict[str, str]:
        old = await store.get_core_memory()
        await store.set_core_memory(body.value.strip())
        await record_memory_audit(store, action="core_replaced", old_value=old, new_value=body.value.strip(),
                                  source="manual", actor_id=actor.user_id)
        return {"core_memory": await store.get_core_memory()}

    @router.get("/notes")
    async def notes(_: Actor = Depends(current_actor)) -> list[dict[str, Any]]:
        return await store.list_notes(limit=100)

    @router.get("/household")
    async def household(_: Actor = Depends(current_actor)) -> dict[str, Any]:
        return {"household": await store.get_household(), "members": await members.list_active()}

    @router.patch("/household")
    async def update_household(body: HouseholdUpdate, actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        updated = await store.update_household(**body.model_dump(exclude_none=True))
        await store.add_activity(actor.user_id, "updated", "household", settings.household_id, "הגדרות הבית עודכנו")
        return updated

    return router
