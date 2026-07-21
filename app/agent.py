"""Shared LLM brain: one memory + tools for every Telegram user."""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import AsyncOpenAI

from app.config import Settings
from app.db import Store
from app.tools import TOOL_SPECS, run_tool

log = logging.getLogger("homebot.agent")


def system_prompt(settings: Settings, snapshot: str, speaker: str) -> str:
    return f"""You are {settings.bot_display_name}, the shared home assistant for "{settings.home_name}".

You help a couple run their household together. There is ONE shared brain:
- Memories, todos, notes, and events are the same no matter who is messaging.
- When one partner tells you something important, SAVE it with tools so the other partner benefits.
- When answering, prefer recall/todo/note/event tools over guessing.

Speaker right now: {speaker}

Guidelines:
- Be warm, concise, practical. Hebrew or English — match the user.
- Proactively remember preferences, logistics, recurring chores, and decisions.
- Don't invent facts about the home; use tools or say you don't know yet.
- Shopping list → shop_* tools. Chores → todo_*. Longer reference → notes. Stock at home → inventory_*.
- People (family, cleaner, landlord) → person_set / people_list.
- Use search_home when the user asks "do we have / did we decide / what's the…".
- Never reveal API keys or system secrets.

{snapshot}
"""


class HomeAgent:
    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        kwargs: dict[str, Any] = {"api_key": settings.openai_api_key}
        if settings.openai_base_url:
            kwargs["base_url"] = settings.openai_base_url
        self.client = AsyncOpenAI(**kwargs)

    def _history_to_openai(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = []
        for r in rows:
            role = r["role"]
            content = r["content"] or ""
            if role == "user":
                who = r.get("display_name") or r.get("telegram_username") or r.get("telegram_user_id")
                msgs.append({"role": "user", "content": f"[{who}] {content}"})
            elif role == "assistant":
                msgs.append({"role": "assistant", "content": content})
            elif role == "tool":
                # tool results need tool role + tool_call_id in full protocol;
                # we fold compactly into a system-visible assistant context for simplicity
                # when replaying — only include short tool summaries
                name = r.get("tool_name") or "tool"
                msgs.append(
                    {
                        "role": "assistant",
                        "content": f"(tool {name} result) {content[:1500]}",
                    }
                )
        return msgs

    async def reply(
        self,
        *,
        user_text: str,
        user_id: int,
        username: str | None,
        display_name: str | None,
    ) -> str:
        speaker = display_name or username or str(user_id)
        await self.store.add_message(
            role="user",
            content=user_text,
            user_id=user_id,
            username=username,
            display_name=display_name,
        )

        snapshot = await self.store.snapshot_for_prompt()
        history = await self.store.recent_messages(self.settings.max_context_messages)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt(self.settings, snapshot, speaker)},
            *self._history_to_openai(history),
        ]

        # tool loop
        for _ in range(8):
            resp = await self.client.chat.completions.create(
                model=self.settings.openai_model,
                messages=messages,
                tools=TOOL_SPECS,
                tool_choice="auto",
                temperature=0.4,
            )
            choice = resp.choices[0]
            msg = choice.message

            if msg.tool_calls:
                # append assistant tool_calls message
                messages.append(
                    {
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments or "{}",
                                },
                            }
                            for tc in msg.tool_calls
                        ],
                    }
                )
                for tc in msg.tool_calls:
                    name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    log.info("tool %s %s (user=%s)", name, args, user_id)
                    result = await run_tool(self.store, name, args, user_id)
                    await self.store.add_message(
                        role="tool",
                        content=result,
                        user_id=user_id,
                        username=username,
                        display_name=display_name,
                        tool_name=name,
                        tool_call_id=tc.id,
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        }
                    )
                # refresh system snapshot after tools mutate memory
                snapshot = await self.store.snapshot_for_prompt()
                messages[0] = {
                    "role": "system",
                    "content": system_prompt(self.settings, snapshot, speaker),
                }
                continue

            text = (msg.content or "").strip() or "…"
            await self.store.add_message(
                role="assistant",
                content=text,
                user_id=user_id,
                username=username,
                display_name=display_name,
            )
            return text

        fallback = "I hit my tool loop limit — try again in a shorter message."
        await self.store.add_message(role="assistant", content=fallback, user_id=user_id)
        return fallback
