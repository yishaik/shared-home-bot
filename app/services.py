from __future__ import annotations

from typing import Any

from app.store_v2 import Store


class HomeService:
    def __init__(self, store: Store):
        self.store = store

    async def add_todo(self, actor_id: int, title: str, *, assigned_to: int | None = None, due_at: str | None = None, priority: str = "normal") -> dict[str, Any]:
        todo_id = await self.store.add_todo(title, actor_id, assigned_to=assigned_to, due_at=due_at, priority=priority)
        await self.store.add_activity(actor_id, "created", "todo", str(todo_id), f"נוספה משימה: {title}")
        return next(item for item in await self.store.list_todos(True) if item["id"] == todo_id)

    async def update_todo(self, actor_id: int, todo_id: int, **changes: Any) -> dict[str, Any] | None:
        item = await self.store.update_todo(todo_id, **changes)
        if item:
            action = "completed" if changes.get("done") is True else "updated"
            summary = f"הושלמה המשימה: {item['title']}" if action == "completed" else f"עודכנה המשימה: {item['title']}"
            await self.store.add_activity(actor_id, action, "todo", str(todo_id), summary, changes)
        return item

    async def delete_todo(self, actor_id: int, todo_id: int) -> bool:
        ok = await self.store.delete_todo(todo_id)
        if ok:
            await self.store.add_activity(actor_id, "deleted", "todo", str(todo_id), "משימה נמחקה")
        return ok

    async def add_shopping(self, actor_id: int, item: str, qty: str = "1", category: str = "") -> dict[str, Any]:
        item_id = await self.store.shop_add(item, qty, actor_id, category)
        await self.store.add_activity(actor_id, "created", "shopping", str(item_id), f"נוסף לקניות: {item}")
        return next(row for row in await self.store.shop_list(True) if row["id"] == item_id)

    async def update_shopping(self, actor_id: int, item_id: int, **changes: Any) -> dict[str, Any] | None:
        item = await self.store.update_shopping(item_id, **changes)
        if item:
            action = "completed" if changes.get("done") is True else "updated"
            summary = f"סומן כנרכש: {item['item']}" if action == "completed" else f"עודכן פריט: {item['item']}"
            await self.store.add_activity(actor_id, action, "shopping", str(item_id), summary, changes)
        return item

    async def delete_shopping(self, actor_id: int, item_id: int) -> bool:
        ok = await self.store.shop_delete(item_id)
        if ok:
            await self.store.add_activity(actor_id, "deleted", "shopping", str(item_id), "פריט קניות נמחק")
        return ok

    async def add_event(self, actor_id: int, *, title: str, start_at: str, end_at: str | None = None, location: str = "", notes: str = "", all_day: bool = False, when_text: str | None = None) -> dict[str, Any]:
        event_id = await self.store.add_event(title, when_text or start_at, notes, actor_id, start_at=start_at, end_at=end_at, location=location, all_day=all_day)
        await self.store.add_activity(actor_id, "created", "event", str(event_id), f"נוסף אירוע: {title}")
        return next(row for row in await self.store.list_events() if row["id"] == event_id)

    async def delete_event(self, actor_id: int, event_id: int) -> bool:
        ok = await self.store.delete_event(event_id)
        if ok:
            await self.store.add_activity(actor_id, "deleted", "event", str(event_id), "אירוע נמחק")
        return ok
