from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from openai import AsyncOpenAI

from app.config import Settings
from app.store_v2 import Store
from app.services import HomeService
from app.tools import TOOL_SPECS, run_tool

log = logging.getLogger("homebot.agent")


def system_prompt(settings: Settings, snapshot: str, speaker: str) -> str:
    return f"""You are {settings.bot_display_name}, the premium shared-home assistant for \"{settings.home_name}\".

The household has one shared operational brain. Facts, tasks, shopping, notes, events, inventory and people are shared.
Current speaker: {speaker}

Rules:
- Match the user's language. Default to warm, concise Hebrew.
- Use tools for household state and never pretend a mutation succeeded without a successful tool result.
- Save durable logistics, decisions and preferences when useful, but do not save casual conversation.
- Shopping requests use shop tools; chores use todo tools; dates use event tools; long reference content uses notes.
- For destructive or ambiguous actions, explain what you need instead of guessing.
- After a successful action, confirm it in one short line and mention the most useful next action only when relevant.
- Never expose secrets, raw tool output or internal errors.

{snapshot}
"""


class HomeAgent:
    def __init__(self, settings: Settings, store: Store, service: HomeService):
        self.settings = settings
        self.store = store
        self.service = service
        kwargs: dict[str, Any] = {"api_key": settings.openai_api_key}
        if settings.openai_base_url:
            kwargs["base_url"] = settings.openai_base_url
        self.client = AsyncOpenAI(**kwargs)
        self._household_lock = asyncio.Lock()

    def _history_to_openai(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for row in rows:
            content = row["content"] or ""
            if row["role"] == "user":
                who = row.get("display_name") or row.get("telegram_username") or row.get("telegram_user_id")
                messages.append({"role": "user", "content": f"[{who}] {content}"})
            elif row["role"] == "assistant":
                messages.append({"role": "assistant", "content": content})
            elif row["role"] == "tool":
                messages.append({"role": "assistant", "content": f"(tool {row.get('tool_name') or 'tool'} result) {content[:1200]}"})
        return messages

    async def reply(self, *, user_text: str, user_id: int, username: str | None, display_name: str | None) -> str:
        async with self._household_lock:
            return await self._reply_locked(user_text=user_text, user_id=user_id, username=username, display_name=display_name)

    async def _reply_locked(self, *, user_text: str, user_id: int, username: str | None, display_name: str | None) -> str:
        speaker = display_name or username or str(user_id)
        await self.store.add_message(role="user", content=user_text, user_id=user_id, username=username, display_name=display_name)
        snapshot = await self.store.snapshot_for_prompt(user_text)
        history = await self.store.recent_messages(self.settings.max_context_messages)
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt(self.settings, snapshot, speaker)}, *self._history_to_openai(history)]

        async with asyncio.timeout(55):
            for _ in range(8):
                response = await self.client.chat.completions.create(
                    model=self.settings.openai_model,
                    messages=messages,
                    tools=TOOL_SPECS,
                    tool_choice="auto",
                    temperature=0.25,
                )
                message = response.choices[0].message
                if message.tool_calls:
                    messages.append({
                        "role": "assistant",
                        "content": message.content or "",
                        "tool_calls": [{"id": call.id, "type": "function", "function": {"name": call.function.name, "arguments": call.function.arguments or "{}"}} for call in message.tool_calls],
                    })
                    for call in message.tool_calls:
                        try:
                            args = json.loads(call.function.arguments or "{}")
                        except json.JSONDecodeError:
                            args = {}
                        result = await run_tool(self.store, self.service, call.function.name, args, user_id)
                        await self.store.add_message(role="tool", content=result, user_id=user_id, username=username, display_name=display_name, tool_name=call.function.name, tool_call_id=call.id)
                        messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
                    snapshot = await self.store.snapshot_for_prompt(user_text)
                    messages[0] = {"role": "system", "content": system_prompt(self.settings, snapshot, speaker)}
                    continue

                text = (message.content or "").strip() or "הפעולה הושלמה."
                await self.store.add_message(role="assistant", content=text, user_id=user_id, username=username, display_name=display_name)
                return text

        fallback = "לא הצלחתי להשלים את הפעולה בבטחה. נסה לנסח אותה בקצרה יותר."
        await self.store.add_message(role="assistant", content=fallback, user_id=user_id)
        return fallback
