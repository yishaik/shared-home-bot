from __future__ import annotations

from pydantic import BaseModel, Field


class TelegramAuthRequest(BaseModel):
    init_data: str = Field(min_length=1)


class ShoppingCreate(BaseModel):
    item: str = Field(min_length=1, max_length=160)
    qty: str = Field(default="1", max_length=40)
    category: str = Field(default="", max_length=80)


class ShoppingUpdate(BaseModel):
    item: str | None = Field(default=None, min_length=1, max_length=160)
    qty: str | None = Field(default=None, max_length=40)
    category: str | None = Field(default=None, max_length=80)
    done: bool | None = None


class TodoCreate(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    assigned_to: int | None = None
    due_at: str | None = None
    priority: str = Field(default="normal", pattern="^(low|normal|high)$")


class TodoUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    assigned_to: int | None = None
    due_at: str | None = None
    priority: str | None = Field(default=None, pattern="^(low|normal|high)$")
    done: bool | None = None


class EventCreate(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    start_at: str
    end_at: str | None = None
    location: str = Field(default="", max_length=240)
    notes: str = Field(default="", max_length=2000)
    all_day: bool = False


class HouseholdUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str | None = Field(default=None, min_length=1, max_length=80)


class MemoryUpdate(BaseModel):
    value: str = Field(min_length=1, max_length=4000)
    category: str = Field(default="general", min_length=1, max_length=80)


class MemorySettingsUpdate(BaseModel):
    auto_memory_enabled: bool


class CoreMemoryUpdate(BaseModel):
    value: str = Field(default="", max_length=8000)
