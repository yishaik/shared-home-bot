from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from openai import AsyncOpenAI

from app.config import Settings
from app.store_v2 import Store
from app.services import HomeService
from app.tools import tool_specs, run_tool

log = logging.getLogger("homebot.agent")


def system_prompt(settings: Settings, snapshot: str, speaker: str, core: str = "", summary: str = "") -> str:
    core_block = f"\n## Core memory (always-true household essentials — keep it curated)\n{core}\n" if core else ""
    summary_block = f"\n## Conversation so far (rolling summary of earlier messages)\n{summary}\n" if summary else ""
    google_line = (
        "\n- Google is connected: gcal_* for the shared Google Calendar, gdoc_* for Google Docs, gsheet_* for Google Sheets "
        "(e.g. an expense tracker or list). The local event tools store household events; when the user wants something on their "
        "real Google Calendar, use gcal_add_event."
        if settings.google_enabled else ""
    )
    return f"""You are {settings.bot_display_name}, the premium shared-home assistant for \"{settings.home_name}\".

The household has one shared operational brain. Facts, tasks, shopping, notes, events, inventory and people are shared.
Current speaker: {speaker}

Rules:
- Match the user's language. Default to warm, concise Hebrew.
- Use tools for household state and never pretend a mutation succeeded without a successful tool result.{google_line}
- Save durable logistics, decisions and preferences when useful, but do not save casual conversation.
- Shopping requests use shop tools; chores use todo tools; dates use event tools; long reference content uses notes.
- For destructive or ambiguous actions, explain what you need instead of guessing.
- After a successful action, confirm it in one short line and mention the most useful next action only when relevant.
- Never expose secrets, raw tool output or internal errors.
- Keep Core memory curated: when you learn a lasting essential (a household member/name, a recurring routine, a major ongoing situation), use core_memory_append; use core_memory_replace to fix or prune it. Keep it short — details belong in facts/notes.
{core_block}{summary_block}
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

    async def _build_system(self, speaker: str, query: str) -> str:
        snapshot = await self.store.snapshot_for_prompt(query)
        core = await self.store.get_core_memory()
        summary = await self.store.get_conv_summary()
        return system_prompt(self.settings, snapshot, speaker, core=core, summary=summary)

    def _render_for_summary(self, rows: list[dict[str, Any]]) -> str:
        out: list[str] = []
        for r in rows:
            who = r.get("display_name") or r.get("telegram_username") or r.get("telegram_user_id")
            content = (r["content"] or "")[:600]
            if r["role"] == "user":
                out.append(f"{who}: {content}")
            elif r["role"] == "assistant":
                out.append(f"assistant: {content}")
            elif r["role"] == "tool":
                out.append(f"(tool {r.get('tool_name') or 'tool'}) {content[:200]}")
        return "\n".join(out)[:8000]

    async def _summarize(self, prev: str, block: str) -> str:
        prompt = (
            "You maintain a rolling summary of a shared household-assistant conversation. "
            "Fold the NEW MESSAGES into the CURRENT SUMMARY. Preserve durable facts, decisions, plans, "
            "names and open threads; drop small talk and resolved trivia. Keep it tight (<= ~1200 chars). "
            "Write in the conversation's language (Hebrew if it is Hebrew).\n\n"
            f"CURRENT SUMMARY:\n{prev or '(none yet)'}\n\nNEW MESSAGES:\n{block}\n\nReturn only the updated summary."
        )
        try:
            resp = await self.client.chat.completions.create(
                model=self.settings.summary_model,
                messages=[{"role": "user", "content": prompt}], temperature=0.2)
            return (resp.choices[0].message.content or "").strip()[:2400]
        except Exception:
            log.exception("summary fold failed")
            return ""

    async def _maybe_fold_summary(self) -> None:
        """Fold messages older than the verbatim window into the rolling summary."""
        try:
            keep = max(4, self.settings.verbatim_messages)
            last_id = await self.store.get_conv_summary_last_id()
            pending = await self.store.messages_after(last_id)  # oldest first
            foldable = pending[:-keep] if len(pending) > keep else []
            if len(foldable) < 8:  # amortise — summarise only once a batch builds up
                return
            new_summary = await self._summarize(await self.store.get_conv_summary(), self._render_for_summary(foldable))
            if new_summary:
                await self.store.set_conv_summary(new_summary, foldable[-1]["id"])
                log.info("summary folded through message id=%s", foldable[-1]["id"])
        except Exception:
            log.exception("maybe_fold_summary failed — continuing")

    async def _reflect_if_due(self, every: int = 20) -> None:
        """Every `every` replies, run reflection in the background (serialised)."""
        try:
            reflector = getattr(self.store, "reflector", None)
            if not reflector:
                return
            count = 0
            try:
                count = int(await self.store.get_setting("_msgs_since_reflect", "0") or 0)
            except ValueError:
                count = 0
            count += 1
            if count < every:
                await self.store.set_setting("_msgs_since_reflect", str(count))
                return
            await self.store.set_setting("_msgs_since_reflect", "0")
            async with self._household_lock:   # never overlap a live reply
                await reflector.reflect()
        except Exception:
            log.exception("reflect_if_due failed — continuing")

    async def _reply_locked(self, *, user_text: str, user_id: int, username: str | None, display_name: str | None) -> str:
        speaker = display_name or username or str(user_id)
        await self.store.add_message(role="user", content=user_text, user_id=user_id, username=username, display_name=display_name)
        await self._maybe_fold_summary()
        history = await self.store.recent_messages(self.settings.verbatim_messages)
        messages: list[dict[str, Any]] = [{"role": "system", "content": await self._build_system(speaker, user_text)}, *self._history_to_openai(history)]

        async with asyncio.timeout(55):
            for _ in range(8):
                response = await self.client.chat.completions.create(
                    model=self.settings.openai_model,
                    messages=messages,
                    tools=tool_specs(self.settings),
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
                        result = await run_tool(self.store, self.service, call.function.name, args, user_id, settings=self.settings)
                        await self.store.add_message(role="tool", content=result, user_id=user_id, username=username, display_name=display_name, tool_name=call.function.name, tool_call_id=call.id)
                        messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
                    messages[0] = {"role": "system", "content": await self._build_system(speaker, user_text)}
                    continue

                text = (message.content or "").strip() or "הפעולה הושלמה."
                await self.store.add_message(role="assistant", content=text, user_id=user_id, username=username, display_name=display_name)
                asyncio.create_task(self._reflect_if_due())  # background self-maintenance
                return text

        fallback = "לא הצלחתי להשלים את הפעולה בבטחה. נסה לנסח אותה בקצרה יותר."
        await self.store.add_message(role="assistant", content=fallback, user_id=user_id)
        return fallback
