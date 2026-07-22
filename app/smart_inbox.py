from __future__ import annotations

import datetime as dt
import json
import logging
import time
import uuid
from contextvars import ContextVar
from typing import Any
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ChatAction, ParseMode, ReactionEmoji

from app import bot as bot_module
from app import telegram_agent as telegram_agent_module
from app.telegram_agent import TelegramAgentRuntime
from app.telegram_models import TelegramEnvelope
from app.telegram_store import TelegramStore
from app.tools import run_tool as execute_tool


log = logging.getLogger("homebot.smart_inbox")

MARKER_PREFIX = "__SMART_INBOX__:"
ACTION_TTL_SECONDS = 24 * 60 * 60

_current_envelope: ContextVar[TelegramEnvelope | None] = ContextVar("smart_inbox_envelope", default=None)
_current_actions: ContextVar[list[dict[str, Any]] | None] = ContextVar("smart_inbox_actions", default=None)
_applied = False

_original_agent_reply = TelegramAgentRuntime.reply
_original_system_prompt = TelegramAgentRuntime._system_prompt
_original_run_tool = telegram_agent_module.run_tool
_original_ensure_schema = TelegramStore.ensure_schema

SMART_INBOX_SCHEMA = """
CREATE TABLE IF NOT EXISTS smart_inbox_actions (
    id TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    thread_id INTEGER,
    scope_key TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    agent_id TEXT NOT NULL,
    source_text TEXT NOT NULL DEFAULT '',
    actions_json TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    result_json TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_smart_inbox_pending
    ON smart_inbox_actions(user_id, chat_id, status, created_at DESC);
"""


_CONFIRM_ALWAYS = {
    "project_add",
    "todo_update",
    "todo_link",
    "todo_schedule",
    "todo_create_doc",
    "todo_create_sheet",
    "shop_clear_done",
    "event_add",
    "event_update",
    "event_delete",
    "forget",
    "inventory_delete",
    "core_memory_replace",
    "remind_cancel",
    "site_publish",
    "gdoc_create",
    "gdoc_append",
    "gsheet_create",
    "gsheet_append_row",
}


_TOOL_LABELS = {
    "project_add": "יצירת פרויקט",
    "todo_add": "יצירת משימה",
    "todo_update": "עדכון משימה",
    "todo_link": "קישור משימות",
    "todo_schedule": "שריון זמן למשימה",
    "todo_create_doc": "יצירת מסמך למשימה",
    "todo_create_sheet": "יצירת גיליון למשימה",
    "shop_clear_done": "ניקוי פריטים שנרכשו",
    "event_add": "יצירת אירוע",
    "event_update": "עדכון אירוע",
    "event_delete": "מחיקת אירוע",
    "forget": "מחיקת זיכרון",
    "inventory_delete": "הסרת פריט מהמלאי",
    "core_memory_replace": "שינוי זיכרון ליבה",
    "remind_add": "יצירת תזכורת",
    "remind_cancel": "ביטול תזכורת",
    "site_publish": "פרסום אתר",
    "gdoc_create": "יצירת Google Doc",
    "gdoc_append": "עדכון Google Doc",
    "gsheet_create": "יצירת Google Sheet",
    "gsheet_append_row": "הוספת שורה ל־Google Sheet",
}


def requires_confirmation(name: str, arguments: dict[str, Any]) -> bool:
    """Hold complex or destructive mutations; keep everyday capture fast."""
    if name in _CONFIRM_ALWAYS:
        return True
    if name == "todo_add":
        return any(
            (
                str(arguments.get("description") or "").strip(),
                arguments.get("project_id"),
                arguments.get("parent_task_id"),
                arguments.get("assigned_to"),
                arguments.get("due_at"),
                (arguments.get("priority") or "normal") != "normal",
                (arguments.get("status") or "todo") != "todo",
            )
        )
    if name == "remind_add":
        return bool(arguments.get("target_name") or arguments.get("recurrence"))
    return False


def parse_marker(value: str) -> dict[str, Any] | None:
    if not value.startswith(MARKER_PREFIX):
        return None
    try:
        payload = json.loads(value[len(MARKER_PREFIX) :])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not payload.get("id") or not payload.get("summary"):
        return None
    return payload


def _canonical_action(name: str, arguments: dict[str, Any]) -> str:
    return json.dumps({"name": name, "arguments": arguments}, ensure_ascii=False, sort_keys=True, default=str)


def _format_datetime(value: Any, timezone_name: str) -> str:
    if not value:
        return ""
    text = str(value)
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
        local = parsed.astimezone(ZoneInfo(timezone_name))
        return local.strftime("%d.%m.%Y %H:%M")
    except (ValueError, TypeError, KeyError):
        return text


async def _member_names(store) -> dict[int, str]:
    try:
        rows = await store.list_members()
    except Exception:
        return {}
    result: dict[int, str] = {}
    for row in rows:
        raw_id = row.get("telegram_user_id") or row.get("id")
        if raw_id is None:
            continue
        try:
            user_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        result[user_id] = str(row.get("display_name") or row.get("username") or user_id)
    return result


async def _describe_action(store, settings, action: dict[str, Any]) -> str:
    name = str(action.get("name") or "")
    arguments = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}
    label = _TOOL_LABELS.get(name, name.replace("_", " "))
    timezone_name = getattr(settings, "household_timezone", "Asia/Jerusalem")

    if name == "todo_add":
        details = [f"✅ משימה: {arguments.get('title') or '(ללא כותרת)'}"]
        if arguments.get("due_at"):
            details.append(f"מועד: {_format_datetime(arguments['due_at'], timezone_name)}")
        if arguments.get("assigned_to"):
            members = await _member_names(store)
            details.append(f"אחראי: {members.get(int(arguments['assigned_to']), arguments['assigned_to'])}")
        if arguments.get("priority") and arguments.get("priority") != "normal":
            details.append(f"עדיפות: {arguments['priority']}")
        return " · ".join(details)

    if name == "event_add":
        start = _format_datetime(arguments.get("start_at"), timezone_name)
        end = _format_datetime(arguments.get("end_at"), timezone_name)
        details = [f"📅 אירוע: {arguments.get('title') or '(ללא כותרת)'}"]
        if start:
            details.append(f"{start}{f'–{end}' if end else ''}")
        if arguments.get("location"):
            details.append(f"מיקום: {arguments['location']}")
        if arguments.get("attendees"):
            details.append(f"מוזמנים: {', '.join(map(str, arguments['attendees']))}")
        return " · ".join(details)

    if name == "remind_add":
        due = _format_datetime(arguments.get("due_at"), timezone_name)
        target = arguments.get("target_name") or ("כל בני הבית" if arguments.get("target") == "all" else "לי")
        details = [f"⏰ תזכורת: {arguments.get('text') or ''}"]
        if due:
            details.append(due)
        if target:
            details.append(f"עבור: {target}")
        if arguments.get("recurrence"):
            details.append(f"חזרה: {arguments['recurrence']}")
        return " · ".join(details)

    if name == "project_add":
        details = [f"🗂 פרויקט: {arguments.get('name') or '(ללא שם)'}"]
        if arguments.get("due_at"):
            details.append(f"יעד: {_format_datetime(arguments['due_at'], timezone_name)}")
        return " · ".join(details)

    if name in {"event_update", "todo_update"}:
        title = arguments.get("title") or arguments.get("id") or ""
        return f"✏️ {label}: {title}"

    if name in {"event_delete", "forget", "inventory_delete", "remind_cancel", "shop_clear_done"}:
        subject = arguments.get("title") or arguments.get("key") or arguments.get("item") or arguments.get("id") or ""
        return f"🗑 {label}{f': {subject}' if subject != '' else ''}"

    if name == "todo_schedule":
        start = _format_datetime(arguments.get("start_at"), timezone_name)
        end = _format_datetime(arguments.get("end_at"), timezone_name)
        return f"🗓 {label} #{arguments.get('id')} · {start}{f'–{end}' if end else ''}"

    subject = (
        arguments.get("title")
        or arguments.get("name")
        or arguments.get("text")
        or arguments.get("item")
        or arguments.get("id")
        or ""
    )
    return f"• {label}{f': {subject}' if subject != '' else ''}"


async def _build_summary(store, settings, actions: list[dict[str, Any]]) -> str:
    lines = [await _describe_action(store, settings, action) for action in actions]
    return (
        "📥 לפני ביצוע\n\n"
        "הבנתי שצריך:\n"
        + "\n".join(f"• {line.lstrip('• ')}" for line in lines)
        + "\n\nהפעולה עדיין לא בוצעה."
    )[:3500]


async def _create_pending_action(
    telegram_store: TelegramStore,
    *,
    envelope: TelegramEnvelope,
    agent_id: str,
    source_text: str,
    actions: list[dict[str, Any]],
    summary: str,
) -> str:
    action_id = uuid.uuid4().hex[:12]
    now = time.time()
    await telegram_store.store.db.execute(
        """INSERT INTO smart_inbox_actions(
               id, chat_id, thread_id, scope_key, user_id, agent_id, source_text,
               actions_json, summary, status, created_at, updated_at, expires_at
           ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
        (
            action_id,
            envelope.chat_id,
            envelope.topic_id,
            envelope.scope_key,
            envelope.user_id,
            agent_id,
            source_text[:2000],
            json.dumps(actions, ensure_ascii=False, default=str),
            summary,
            now,
            now,
            now + ACTION_TTL_SECONDS,
        ),
    )
    await telegram_store.store.db.commit()
    return action_id


async def _get_action(telegram_store: TelegramStore, action_id: str) -> dict[str, Any] | None:
    row = await (
        await telegram_store.store.db.execute(
            "SELECT * FROM smart_inbox_actions WHERE id=?",
            (action_id,),
        )
    ).fetchone()
    return dict(row) if row else None


async def _claim_action(
    telegram_store: TelegramStore,
    action_id: str,
    *,
    user_id: int,
    chat_id: int,
) -> dict[str, Any] | None:
    now = time.time()
    cursor = await telegram_store.store.db.execute(
        """UPDATE smart_inbox_actions
           SET status='executing', updated_at=?
           WHERE id=? AND user_id=? AND chat_id=?
             AND status IN ('pending', 'failed') AND expires_at>?""",
        (now, action_id, user_id, chat_id, now),
    )
    await telegram_store.store.db.commit()
    if cursor.rowcount <= 0:
        return None
    return await _get_action(telegram_store, action_id)


async def _set_action_status(
    telegram_store: TelegramStore,
    action_id: str,
    status: str,
    result: Any = "",
) -> None:
    result_json = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    await telegram_store.store.db.execute(
        "UPDATE smart_inbox_actions SET status=?, result_json=?, updated_at=? WHERE id=?",
        (status, result_json[:12000], time.time(), action_id),
    )
    await telegram_store.store.db.commit()


async def _patched_ensure_schema(self: TelegramStore) -> None:
    await _original_ensure_schema(self)
    await self.store.db.executescript(SMART_INBOX_SCHEMA)
    await self.store.db.execute(
        "UPDATE smart_inbox_actions SET status='expired', updated_at=? WHERE status IN ('pending', 'failed') AND expires_at<=?",
        (time.time(), time.time()),
    )
    await self.store.db.commit()


async def _patched_system_prompt(self: TelegramAgentRuntime, **kwargs) -> str:
    prompt = await _original_system_prompt(self, **kwargs)
    return (
        prompt
        + "\n\nSmart Inbox rules:\n"
        "- Complex or destructive mutations may return pending_confirmation=true.\n"
        "- That means nothing was executed yet. Never claim success; say it is awaiting the user's approval.\n"
        "- Simple shopping capture and simple title-only tasks remain immediate."
    )


async def _patched_run_tool(store, service, name, arguments, user_id, settings=None) -> str:
    envelope = _current_envelope.get()
    pending = _current_actions.get()
    if envelope is not None and pending is not None and requires_confirmation(name, arguments):
        action = {"name": name, "arguments": dict(arguments)}
        canonical = _canonical_action(name, arguments)
        if all(_canonical_action(str(item.get("name") or ""), item.get("arguments") or {}) != canonical for item in pending):
            pending.append(action)
        return json.dumps(
            {
                "ok": False,
                "pending_confirmation": True,
                "message": "The action is waiting for explicit approval and has not been executed.",
            },
            ensure_ascii=False,
        )
    return await _original_run_tool(store, service, name, arguments, user_id, settings=settings)


async def _patched_agent_reply(
    self: TelegramAgentRuntime,
    *,
    envelope: TelegramEnvelope,
    profile,
    user_text: str,
) -> str:
    envelope_token = _current_envelope.set(envelope)
    actions_token = _current_actions.set([])
    try:
        answer = await _original_agent_reply(
            self,
            envelope=envelope,
            profile=profile,
            user_text=user_text,
        )
        actions = _current_actions.get() or []
        if not actions:
            return answer
        summary = await _build_summary(self.store, self.settings, actions)
        action_id = await _create_pending_action(
            self.telegram_store,
            envelope=envelope,
            agent_id=profile.id,
            source_text=user_text,
            actions=actions,
            summary=summary,
        )
        return MARKER_PREFIX + json.dumps({"id": action_id, "summary": summary}, ensure_ascii=False)
    finally:
        _current_actions.reset(actions_token)
        _current_envelope.reset(envelope_token)


def _pending_keyboard(action_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ אישור", callback_data=f"inbox_confirm:{action_id}"),
                InlineKeyboardButton("✏️ עריכה", callback_data=f"inbox_edit:{action_id}"),
            ],
            [InlineKeyboardButton("ביטול", callback_data=f"inbox_cancel:{action_id}")],
        ]
    )


async def on_text(update: Update, context) -> None:
    platform = bot_module._platform(context)
    envelope = platform.envelope(update)
    message = update.effective_message
    if not envelope or not message or not envelope.text or not platform.is_authorized(envelope):
        return

    handler = bot_module.BUTTON_ACTIONS.get(envelope.text.strip())
    if handler is not None:
        await handler(update, context)
        return

    await platform.register_message(update, envelope)
    if not await platform.should_respond(envelope):
        return

    await message.reply_chat_action(ChatAction.TYPING)
    await bot_module._set_reaction_safely(message, ReactionEmoji.EYES)
    progress: Message | None = None
    if envelope.is_private:
        progress = await message.reply_text("מטפל בזה…")
    elif envelope.is_group:
        await platform.raw_api.send_ephemeral_text(
            chat_id=envelope.chat_id,
            receiver_user_id=envelope.user_id,
            message_thread_id=envelope.thread_id,
            text="מטפל בזה…",
        )

    try:
        result = await platform.answer(update)
    except Exception:
        incident = uuid.uuid4().hex[:8]
        bot_module.log.exception("Telegram agent failed incident=%s", incident)
        await bot_module._set_reaction_safely(message, ReactionEmoji.THUMBS_DOWN, is_big=True)
        error_text = f"לא הצלחתי להשלים את הפעולה. אפשר לנסות שוב.\nקוד תקלה: <code>{incident}</code>"
        if progress:
            await progress.edit_text(error_text, parse_mode=ParseMode.HTML)
        else:
            await message.reply_text(error_text, parse_mode=ParseMode.HTML)
        return

    if not result:
        if progress:
            await progress.delete()
        return

    answer, _agent_id = result
    pending = parse_marker(answer)
    if pending:
        markup = _pending_keyboard(str(pending["id"]))
        if progress:
            await progress.edit_text(str(pending["summary"]), reply_markup=markup)
        else:
            await message.reply_text(str(pending["summary"]), reply_markup=markup)
        return

    if len(answer) <= 4000:
        if progress:
            await progress.edit_text(answer)
        else:
            await message.reply_text(answer)
        await bot_module._set_reaction_safely(message, ReactionEmoji.THUMBS_UP, is_big=True)
        return

    if progress:
        await progress.delete()
    for offset in range(0, len(answer), 4000):
        await message.reply_text(answer[offset : offset + 4000])
    await bot_module._set_reaction_safely(message, ReactionEmoji.THUMBS_UP, is_big=True)


async def handle_callback(update: Update, context) -> bool:
    query = update.callback_query
    data = (query.data if query else "") or ""
    if not data.startswith("inbox_"):
        return False

    platform = bot_module._platform(context)
    envelope = platform.envelope(update)
    user = update.effective_user
    if not query or not envelope or not user or not platform.is_authorized(envelope):
        return True

    action, _, action_id = data.partition(":")
    if not action_id:
        await query.answer("ההצעה אינה תקינה", show_alert=True)
        return True

    telegram_store = platform.telegram_store
    row = await _get_action(telegram_store, action_id)
    if not row or int(row["user_id"]) != user.id or int(row["chat_id"]) != envelope.chat_id:
        await query.answer("ההצעה לא נמצאה או שאינה שלך", show_alert=True)
        return True

    if float(row["expires_at"]) <= time.time():
        await _set_action_status(telegram_store, action_id, "expired")
        await query.answer("ההצעה פגה. שלח את הבקשה מחדש.", show_alert=True)
        await query.edit_message_text("⌛ ההצעה פגה ולא בוצעה.")
        return True

    if action == "inbox_cancel":
        if row["status"] not in {"pending", "failed"}:
            await query.answer("ההצעה כבר טופלה", show_alert=True)
            return True
        await _set_action_status(telegram_store, action_id, "cancelled")
        await query.answer("בוטל")
        await query.edit_message_text(f"❌ בוטל — לא בוצע דבר.\n\n{row['summary']}")
        return True

    if action == "inbox_edit":
        if row["status"] not in {"pending", "failed"}:
            await query.answer("ההצעה כבר טופלה", show_alert=True)
            return True
        await _set_action_status(telegram_store, action_id, "editing")
        await query.answer("אפשר לשלוח תיקון")
        await query.edit_message_text(
            "✏️ ההצעה לא בוצעה.\n\nשלח עכשיו את הנוסח המתוקן כהודעה חדשה, ואני אכין הצעה חדשה.\n\n"
            + str(row["summary"])
        )
        return True

    if action != "inbox_confirm":
        await query.answer("פעולה לא מוכרת", show_alert=True)
        return True

    claimed = await _claim_action(
        telegram_store,
        action_id,
        user_id=user.id,
        chat_id=envelope.chat_id,
    )
    if not claimed:
        await query.answer("ההצעה כבר טופלה או פגה", show_alert=True)
        return True

    await query.answer("מבצע…")
    try:
        actions = json.loads(str(claimed["actions_json"]))
        if not isinstance(actions, list):
            raise ValueError("invalid action batch")
        results: list[dict[str, Any]] = []
        all_ok = True
        settings = context.application.bot_data["settings"]
        service = context.application.bot_data["service"]
        store = context.application.bot_data["store"]
        for item in actions:
            name = str(item.get("name") or "")
            arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
            raw_result = await execute_tool(store, service, name, arguments, user.id, settings=settings)
            try:
                parsed = json.loads(raw_result)
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = {"ok": False, "error": "invalid tool response"}
            results.append({"name": name, "result": parsed})
            if not bool(parsed.get("ok")):
                all_ok = False
                break

        if all_ok:
            await _set_action_status(telegram_store, action_id, "completed", results)
            await query.edit_message_text(f"✅ בוצע\n\n{claimed['summary']}")
        else:
            await _set_action_status(telegram_store, action_id, "failed", results)
            await query.edit_message_text(
                "⚠️ לא הצלחתי להשלים את הפעולה. אפשר לנסות שוב בלי ליצור כפילות.",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("נסה שוב", callback_data=f"inbox_confirm:{action_id}")],
                        [InlineKeyboardButton("ביטול", callback_data=f"inbox_cancel:{action_id}")],
                    ]
                ),
            )
    except Exception as exc:
        log.exception("smart inbox execution failed action=%s", action_id)
        await _set_action_status(telegram_store, action_id, "failed", {"error": str(exc)[:500]})
        await query.edit_message_text(
            "⚠️ לא הצלחתי להשלים את הפעולה. אפשר לנסות שוב.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("נסה שוב", callback_data=f"inbox_confirm:{action_id}")],
                    [InlineKeyboardButton("ביטול", callback_data=f"inbox_cancel:{action_id}")],
                ]
            ),
        )
    return True


def apply_runtime_patches() -> None:
    global _applied
    if _applied:
        return
    TelegramStore.ensure_schema = _patched_ensure_schema
    TelegramAgentRuntime._system_prompt = _patched_system_prompt
    TelegramAgentRuntime.reply = _patched_agent_reply
    telegram_agent_module.run_tool = _patched_run_tool
    _applied = True
