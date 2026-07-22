from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.config import Settings
from app.memory_control import (
    list_memory_audit,
    record_memory_audit,
    reflection_status,
    set_auto_memory_enabled,
)
from app.store_v2 import Store
from app.schemas import (
    CoreMemoryUpdate,
    EventCreate,
    HouseholdUpdate,
    MemorySettingsUpdate,
    MemoryUpdate,
    ShoppingCreate,
    ShoppingUpdate,
    TelegramAuthRequest,
    TodoCreate,
    TodoUpdate,
)
from app.security import AuthenticationError, SessionSigner, parse_telegram_user, validate_telegram_init_data
from app.services import HomeService


@dataclass(frozen=True)
class Actor:
    user_id: int
    household_id: str
    display_name: str


def build_api_router(settings: Settings, store: Store, service: HomeService) -> APIRouter:
    router = APIRouter(prefix="/api")
    signer = SessionSigner(settings.effective_session_secret, settings.session_ttl_seconds)

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
        await store.upsert_member_profile(user.id, user.display_name, user.username)
        return {
            "token": signer.issue(user_id=user.id, household_id=settings.household_id, display_name=user.display_name),
            "user": {"id": user.id, "name": user.display_name, "username": user.username},
            "household": await store.get_household(),
        }

    @router.get("/home")
    async def home(_: Actor = Depends(current_actor)) -> dict[str, Any]:
        return await store.dashboard()

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

    @router.get("/tasks")
    async def tasks(_: Actor = Depends(current_actor), include_done: bool = False) -> list[dict[str, Any]]:
        return await store.list_todos(include_done)

    @router.post("/tasks", status_code=status.HTTP_201_CREATED)
    async def create_task(body: TodoCreate, actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        return await service.add_todo(actor.user_id, body.title, assigned_to=body.assigned_to, due_at=body.due_at, priority=body.priority)

    @router.patch("/tasks/{todo_id}")
    async def update_task(todo_id: int, body: TodoUpdate, actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        item = await service.update_todo(actor.user_id, todo_id, **body.model_dump(exclude_none=True))
        if not item:
            raise HTTPException(status_code=404, detail="Task not found")
        return item

    @router.delete("/tasks/{todo_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_task(todo_id: int, actor: Actor = Depends(current_actor)) -> None:
        if not await service.delete_todo(actor.user_id, todo_id):
            raise HTTPException(status_code=404, detail="Task not found")

    @router.get("/events")
    async def events(_: Actor = Depends(current_actor)) -> list[dict[str, Any]]:
        return await store.list_events()

    @router.post("/events", status_code=status.HTTP_201_CREATED)
    async def create_event(body: EventCreate, actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        return await service.add_event(actor.user_id, title=body.title, start_at=body.start_at, end_at=body.end_at, location=body.location, notes=body.notes, all_day=body.all_day)

    @router.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_event(event_id: int, actor: Actor = Depends(current_actor)) -> None:
        if not await service.delete_event(actor.user_id, event_id):
            raise HTTPException(status_code=404, detail="Event not found")

    @router.get("/memory")
    async def memory(_: Actor = Depends(current_actor)) -> list[dict[str, Any]]:
        return await store.list_memories(limit=100)

    @router.get("/memory/control")
    async def memory_control(_: Actor = Depends(current_actor)) -> dict[str, Any]:
        return {
            "status": await reflection_status(store),
            "core_memory": await store.get_core_memory(),
            "memories": await store.list_memories(limit=200),
            "audit": await list_memory_audit(store, limit=50),
        }

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
        await record_memory_audit(
            store,
            action="updated",
            memory_key=memory_key,
            old_value=old.get("value", ""),
            new_value=body.value,
            source="manual",
            actor_id=actor.user_id,
        )
        return await store.get_memory(memory_key) or {}

    @router.delete("/memory/{memory_key}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_memory(memory_key: str, actor: Actor = Depends(current_actor)) -> None:
        old = await store.get_memory(memory_key)
        if not old or not await store.delete_memory(memory_key):
            raise HTTPException(status_code=404, detail="Memory not found")
        await record_memory_audit(
            store,
            action="deleted",
            memory_key=memory_key,
            old_value=old.get("value", ""),
            source="manual",
            actor_id=actor.user_id,
            metadata={"deleted_memory": old},
        )

    @router.put("/memory/core")
    async def update_core_memory(body: CoreMemoryUpdate, actor: Actor = Depends(current_actor)) -> dict[str, str]:
        old = await store.get_core_memory()
        await store.set_setting("core_memory", body.value.strip())
        await record_memory_audit(
            store,
            action="core_replaced",
            old_value=old,
            new_value=body.value.strip(),
            source="manual",
            actor_id=actor.user_id,
        )
        return {"core_memory": body.value.strip()}

    @router.get("/notes")
    async def notes(_: Actor = Depends(current_actor)) -> list[dict[str, Any]]:
        return await store.list_notes(limit=100)

    @router.get("/household")
    async def household(_: Actor = Depends(current_actor)) -> dict[str, Any]:
        return {"household": await store.get_household(), "members": await store.list_members()}

    @router.patch("/household")
    async def update_household(body: HouseholdUpdate, actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        updated = await store.update_household(**body.model_dump(exclude_none=True))
        await store.add_activity(actor.user_id, "updated", "household", settings.household_id, "הגדרות הבית עודכנו")
        return updated

    return router
