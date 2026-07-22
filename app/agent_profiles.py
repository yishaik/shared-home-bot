from __future__ import annotations

import re
from dataclasses import dataclass

from app.telegram_models import TelegramEnvelope
from app.telegram_store import TelegramStore


@dataclass(frozen=True, slots=True)
class AgentProfile:
    id: str
    name: str
    description: str
    instructions: str
    triggers: tuple[str, ...] = ()


DEFAULT_AGENT_PROFILES: dict[str, AgentProfile] = {
    "coordinator": AgentProfile(
        id="coordinator",
        name="מנהל הבית",
        description="ניתוב, תיאום ושאלות כלליות",
        instructions=(
            "Act as the household coordinator. Resolve cross-domain requests, delegate mentally to the "
            "right specialty, and keep confirmations concise."
        ),
    ),
    "tasks": AgentProfile(
        id="tasks",
        name="משימות",
        description="מטלות, אחריות, דדליינים ומעקב",
        instructions=(
            "Focus on executable household tasks. Clarify owner and due date only when needed, prefer todo "
            "tools, and surface the next concrete action."
        ),
        triggers=("משימה", "מטלה", "todo", "לעשות", "דדליין", "אחריות"),
    ),
    "shopping": AgentProfile(
        id="shopping",
        name="קניות ומלאי",
        description="רשימות קניות, כמויות ומלאי ביתי",
        instructions=(
            "Focus on shopping and inventory. Normalize quantities, avoid duplicate open items, and use "
            "shopping or inventory tools instead of prose-only answers."
        ),
        triggers=("קניות", "לקנות", "סופר", "מלאי", "חסר", "shopping", "buy"),
    ),
    "calendar": AgentProfile(
        id="calendar",
        name="יומן",
        description="אירועים, תזכורות ותיאום זמן",
        instructions=(
            "Focus on calendar operations and scheduling. Use the Google-backed calendar tools when "
            "available and preserve timezone, attendees, recurrence and reminders."
        ),
        triggers=("יומן", "אירוע", "פגישה", "תזכיר", "מחר", "שעה", "calendar", "meeting"),
    ),
    "memory": AgentProfile(
        id="memory",
        name="ידע וזיכרון",
        description="מידע קבוע, הערות והעדפות של הבית",
        instructions=(
            "Focus on durable household knowledge, notes and retrieval. Keep sensitive data out of memory "
            "unless explicitly requested and distinguish facts from temporary conversation."
        ),
        triggers=("תזכור", "זיכרון", "הערה", "מסמך", "מידע", "memory", "note"),
    ),
}


class AgentRouter:
    def __init__(self, telegram_store: TelegramStore, profiles: dict[str, AgentProfile] | None = None):
        self.telegram_store = telegram_store
        self.profiles = profiles or DEFAULT_AGENT_PROFILES

    def get(self, agent_id: str | None) -> AgentProfile:
        return self.profiles.get(agent_id or "", self.profiles["coordinator"])

    async def select(self, envelope: TelegramEnvelope, text: str) -> AgentProfile:
        bound = await self.telegram_store.topic_agent(envelope.chat_id, envelope.topic_id)
        if bound in self.profiles:
            return self.profiles[bound]

        explicit = re.search(r"(?:^|\s)(?:@agent:|agent:)([a-z0-9_-]+)", text, re.IGNORECASE)
        if explicit and explicit.group(1).lower() in self.profiles:
            return self.profiles[explicit.group(1).lower()]

        lowered = text.lower()
        scores: dict[str, int] = {}
        for profile in self.profiles.values():
            if not profile.triggers:
                continue
            scores[profile.id] = sum(1 for trigger in profile.triggers if trigger.lower() in lowered)
        if scores:
            selected, score = max(scores.items(), key=lambda item: item[1])
            if score > 0:
                return self.profiles[selected]
        return self.profiles["coordinator"]
