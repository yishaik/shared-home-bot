from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


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
    description: str = Field(default="", max_length=8000)
    project_id: int | None = None
    parent_task_id: int | None = None
    status: str = Field(default="todo", pattern="^(todo|in_progress|waiting|completed|cancelled)$")
    assigned_to: int | None = None
    due_at: str | None = None
    priority: str = Field(default="normal", pattern="^(low|normal|high)$")
    recurrence_rule: str = Field(default="", max_length=500)
    estimate_minutes: int | None = Field(default=None, ge=1, le=100000)


class TodoUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=8000)
    project_id: int | None = None
    parent_task_id: int | None = None
    status: str | None = Field(default=None, pattern="^(todo|in_progress|waiting|completed|cancelled)$")
    assigned_to: int | None = None
    due_at: str | None = None
    priority: str | None = Field(default=None, pattern="^(low|normal|high)$")
    recurrence_rule: str | None = Field(default=None, max_length=500)
    estimate_minutes: int | None = Field(default=None, ge=1, le=100000)
    done: bool | None = None


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=8000)
    status: str = Field(default="planned", pattern="^(planned|active|paused|completed|cancelled)$")
    owner_id: int | None = None
    start_at: str | None = None
    due_at: str | None = None
    priority: str = Field(default="normal", pattern="^(low|normal|high)$")
    create_drive_folder: bool = False


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=8000)
    status: str | None = Field(default=None, pattern="^(planned|active|paused|completed|cancelled)$")
    owner_id: int | None = None
    start_at: str | None = None
    due_at: str | None = None
    priority: str | None = Field(default=None, pattern="^(low|normal|high)$")


class TaskRelationshipCreate(BaseModel):
    source_task_id: int
    target_task_id: int
    relationship_type: str = Field(pattern="^(blocks|related|follows|duplicates)$")


class TaskCalendarBlockCreate(BaseModel):
    start_at: str
    end_at: str
    location: str = Field(default="", max_length=500)
    block_type: str = Field(default="work", pattern="^(work|appointment|review|focus)$")

    @model_validator(mode="after")
    def end_after_start(self):
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        return self


class TaskResourceLinkCreate(BaseModel):
    file_name: str = Field(min_length=1, max_length=500)
    web_url: str = Field(min_length=1, max_length=4000)
    google_file_id: str = Field(default="", max_length=500)
    mime_type: str = Field(default="", max_length=500)
    relationship: str = Field(default="attachment", pattern="^(attachment|working_doc|source|output)$")


class TaskSheetCreate(BaseModel):
    template: str = Field(default="tracker", pattern="^(tracker|budget|suppliers|equipment)$")


class EventCreate(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    start_at: str
    end_at: str
    location: str = Field(default="", max_length=500)
    description: str = Field(default="", max_length=8000)
    notes: str = Field(default="", max_length=8000)
    all_day: bool = False
    attendees: list[str] = Field(default_factory=list, max_length=50)
    recurrence: list[str] = Field(default_factory=list, max_length=10)
    reminders: dict[str, Any] | None = None

    @model_validator(mode="after")
    def end_after_start(self):
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        return self


class EventUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    start_at: str | None = None
    end_at: str | None = None
    location: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=8000)
    notes: str | None = Field(default=None, max_length=8000)
    all_day: bool | None = None
    attendees: list[str] | None = Field(default=None, max_length=50)
    recurrence: list[str] | None = Field(default=None, max_length=10)
    reminders: dict[str, Any] | None = None


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
