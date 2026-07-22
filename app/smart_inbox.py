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
from app import telegram_agent as agent_module
from app.telegram_agent import TelegramAgentRuntime
from app.telegram_models import TelegramEnvelope
from app.telegram_store import TelegramStore
from app.tools import run_tool as execute_tool

log = logging.getLogger("homebot.smart_inbox")
MARKER_PREFIX = "__SMART_INBOX__:"
TTL_SECONDS = 86400

_envelope_ctx: ContextVar[TelegramEnvelope | None] = ContextVar("smart_inbox_envelope", default=None)
_actions_ctx: ContextVar[list[dict[str, Any]] | None] = ContextVar("smart_inbox_actions", default=None)
_applied = False

_original_reply = TelegramAgentRuntime.reply
_original_prompt = TelegramAgentRuntime._system_prompt
_original_run_tool = agent_module.run_tool
_original_ensure_schema = TelegramStore.ensure_schema

SCHEMA = """
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

ALWAYS_CONFIRM = {
    "project_add", "todo_update", "todo_link", "todo_schedule", "todo_create_doc",
    "todo_create_sheet", "shop_clear_done", "event_add", "event_update", "event_delete",
    "forget", "inventory_delete", "core_memory_replace", "remind_cancel", "site_publish",
    "gdoc_create", "gdoc_append", "gsheet_create", "gsheet_append_row",
}

LABELS = {
    "project_add": "יצירת פרויקט", "todo_add": "יצירת משימה", "todo_update": "עדכון משימה",
    "todo_link": "קישור משימות", "todo_schedule": "שריון זמן למשימה",
    "todo_create_doc": "יצירת מסמך למשימה", "todo_create_sheet": "יצירת גיליון למשימה",
    "shop_clear_done": "ניקוי פריטים שנרכשו", "event_add": "יצירת אירוע",
    "event_update": "עדכון אירוע", "event_delete": "מחיקת אירוע", "forget": "מחיקת זיכרון",
    "inventory_delete": "הסרת פריט מהמלאי", "core_memory_replace": "שינוי זיכרון ליבה",
    "remind_add": "יצירת תזכורת", "remind_cancel": "ביטול תזכורת", "site_publish": "פרסום אתר",
    "gdoc_create": "יצירת Google Doc", "gdoc_append": "עדכון Google Doc",
    "gsheet_create": "יצירת Google Sheet", "gsheet_append_row": "הוספת שורה ל־Google Sheet",
}


def requires_confirmation(name: str, args: dict[str, Any]) -> bool:
    if name in ALWAYS_CONFIRM:
        return True
    if name == "todo_add":
        return any([
            str(args.get("description") or "").strip(), args.get("project_id"),
            args.get("parent_task_id"), args.get("assigned_to"), args.get("due_at"),
            (args.get("priority") or "normal") != "normal",
            (args.get("status") or "todo") != "todo",
        ])
    return name == "remind_add" and bool(args.get("target_name") or args.get("recurrence"))


def parse_marker(text: str) -> dict[str, Any] | None:
    if not text.startswith(MARKER_PREFIX):
        return None
    try:
        payload = json.loads(text[len(MARKER_PREFIX):])
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) and payload.get("id") and payload.get("summary") else None


def _canonical(name: str, args: dict[str, Any]) -> str:
    return json.dumps({"name": name, "arguments": args}, ensure_ascii=False, sort_keys=True, default=str)


def _local_time(value: Any, timezone_name: str) -> str:
    if not value:
        return ""
    text = str(value)
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        zone = ZoneInfo(timezone_name)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=zone)
        return parsed.astimezone(zone).strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError, KeyError):
        return text


async def _members(store) -> dict[int, str]:
    try:
        rows = await store.list_members()
    except Exception:
        return {}
    result: dict[int, str] = {}
    for row in rows:
        raw_id = row.get("telegram_user_id") or row.get("id")
        try:
            user_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        result[user_id] = str(row.get("display_name") or row.get("username") or user_id)
    return result


async def _describe(store, settings, action: dict[str, Any]) -> str:
    name = str(action.get("name") or "")
    args = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}
    zone = getattr(settings, "household_timezone", "Asia/Jerusalem")
    label = LABELS.get(name, name.replace("_", " "))

    if name == "todo_add":
        parts = [f"✅ משימה: {args.get('title') or '(ללא כותרת)'}"]
        if args.get("due_at"):
            parts.append(f"מועד: {_local_time(args['due_at'], zone)}")
        if args.get("assigned_to"):
            names = await _members(store)
            try:
                assignee = names.get(int(args["assigned_to"]), args["assigned_to"])
            except (TypeError, ValueError):
                assignee = args["assigned_to"]
            parts.append(f"אחראי: {assignee}")
        if args.get("priority") not in {None, "", "normal"}:
            parts.append(f"עדיפות: {args['priority']}")
        return " · ".join(parts)

    if name == "event_add":
        start, end = _local_time(args.get("start_at"), zone), _local_time(args.get("end_at"), zone)
        parts = [f"📅 אירוע: {args.get('title') or '(ללא כותרת)'}"]
        if start:
            parts.append(f"{start}{f'–{end}' if end else ''}")
        if args.get("location"):
            parts.append(f"מיקום: {args['location']}")
        if args.get("attendees"):
            parts.append(f"מוזמנים: {', '.join(map(str, args['attendees']))}")
        return " · ".join(parts)

    if name == "remind_add":
        target = args.get("target_name") or ("כל בני הבית" if args.get("target") == "all" else "לי")
        parts = [f"⏰ תזכורת: {args.get('text') or ''}"]
        if args.get("due_at"):
            parts.append(_local_time(args["due_at"], zone))
        if target:
            parts.append(f"עבור: {target}")
        if args.get("recurrence"):
            parts.append(f"חזרה: {args['recurrence']}")
        return " · ".join(parts)

    if name == "project_add":
        text = f"🗂 פרויקט: {args.get('name') or '(ללא שם)'}"
        return text + (f" · יעד: {_local_time(args['due_at'], zone)}" if args.get("due_at") else "")

    if name == "todo_schedule":
        start, end = _local_time(args.get("start_at"), zone), _local_time(args.get("end_at"), zone)
        return f"🗓 {label} #{args.get('id')} · {start}{f'–{end}' if end else ''}"

    subject = args.get("title") or args.get("name") or args.get("text") or args.get("key") or args.get("item") or args.get("id") or ""
    prefix = "🗑" if name in {"event_delete", "forget", "inventory_delete", "remind_cancel", "shop_clear_done"} else "•"
    return f"{prefix} {label}{f': {subject}' if subject != '' else ''}"


async def _summary(store, settings, actions: list[dict[str, Any]]) -> str:
    lines = [await _describe(store, settings, item) for item in actions]
    return ("📥 לפני ביצוע\n\nהבנתי שצריך:\n" + "\n".join(f"• {line.lstrip('• ')}" for line in lines) + "\n\nהפעולה עדיין לא בוצעה.")[:3500]


async def _insert(ts: TelegramStore, envelope: TelegramEnvelope, agent_id: str, source: str, actions: list[dict[str, Any]], summary: str) -> str:
    action_id, now = uuid.uuid4().hex[:12], time.time()
    await ts.store.db.execute(
        """INSERT INTO smart_inbox_actions(id, chat_id, thread_id, scope_key, user_id, agent_id,
           source_text, actions_json, summary, status, created_at, updated_at, expires_at)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
        (action_id, envelope.chat_id, envelope.topic_id, envelope.scope_key, envelope.user_id, agent_id,
         source[:2000], json.dumps(actions, ensure_ascii=False, default=str), summary, now, now, now + TTL_SECONDS),
    )
    await ts.store.db.commit()
    return action_id


async def _get(ts: TelegramStore, action_id: str) -> dict[str, Any] | None:
    row = await (await ts.store.db.execute("SELECT * FROM smart_inbox_actions WHERE id=?", (action_id,))).fetchone()
    return dict(row) if row else None


async def _status(ts: TelegramStore, action_id: str, status: str, result: Any = "") -> None:
    payload = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    await ts.store.db.execute(
        "UPDATE smart_inbox_actions SET status=?, result_json=?, updated_at=? WHERE id=?",
        (status, payload[:12000], time.time(), action_id),
    )
    await ts.store.db.commit()


async def _claim(ts: TelegramStore, action_id: str, user_id: int, chat_id: int) -> dict[str, Any] | None:
    now = time.time()
    cur = await ts.store.db.execute(
        """UPDATE smart_inbox_actions SET status='executing', updated_at=?
           WHERE id=? AND user_id=? AND chat_id=? AND status IN ('pending','failed') AND expires_at>?""",
        (now, action_id, user_id, chat_id, now),
    )
    await ts.store.db.commit()
    return await _get(ts, action_id) if cur.rowcount > 0 else None


async def _ensure_schema(self: TelegramStore) -> None:
    await _original_ensure_schema(self)
    now = time.time()
    await self.store.db.executescript(SCHEMA)
    await self.store.db.execute(
        "UPDATE smart_inbox_actions SET status='expired', updated_at=? WHERE status IN ('pending','failed') AND expires_at<=?",
        (now, now),
    )
    await self.store.db.execute(
        "UPDATE smart_inbox_actions SET status='failed', updated_at=? WHERE status='executing' AND updated_at<?",
        (now, now - 600),
    )
    await self.store.db.commit()


async def _system_prompt(self: TelegramAgentRuntime, **kwargs) -> str:
    return await _original_prompt(self, **kwargs) + (
        "\n\nSmart Inbox:\n"
        "- pending_confirmation=true means the action was not executed. Never claim success.\n"
        "- Complex or destructive changes wait for explicit approval.\n"
        "- Simple shopping items and title-only tasks remain immediate."
    )


async def _run_tool(store, service, name, arguments, user_id, settings=None) -> str:
    pending = _actions_ctx.get()
    if _envelope_ctx.get() is not None and pending is not None and requires_confirmation(name, arguments):
        action, canonical = {"name": name, "arguments": dict(arguments)}, _canonical(name, arguments)
        if all(_canonical(str(item.get("name") or ""), item.get("arguments") or {}) != canonical for item in pending):
            pending.append(action)
        return json.dumps({"ok": False, "pending_confirmation": True, "message": "Waiting for explicit approval; not executed."})
    return await _original_run_tool(store, service, name, arguments, user_id, settings=settings)


async def _reply(self: TelegramAgentRuntime, *, envelope: TelegramEnvelope, profile, user_text: str) -> str:
    env_token, actions_token = _envelope_ctx.set(envelope), _actions_ctx.set([])
    try:
        answer = await _original_reply(self, envelope=envelope, profile=profile, user_text=user_text)
        actions = _actions_ctx.get() or []
        if not actions:
            return answer
        summary = await _summary(self.store, self.settings, actions)
        action_id = await _insert(self.telegram_store, envelope, profile.id, user_text, actions, summary)
        return MARKER_PREFIX + json.dumps({"id": action_id, "summary": summary}, ensure_ascii=False)
    finally:
        _actions_ctx.reset(actions_token)
        _envelope_ctx.reset(env_token)


def _buttons(action_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ אישור", callback_data=f"inbox_confirm:{action_id}"),
         InlineKeyboardButton("✏️ עריכה", callback_data=f"inbox_edit:{action_id}")],
        [InlineKeyboardButton("ביטול", callback_data=f"inbox_cancel:{action_id}")],
    ])


async def on_text(update: Update, context) -> None:
    platform, message = bot_module._platform(context), update.effective_message
    envelope = platform.envelope(update)
    if not envelope or not message or not envelope.text or not platform.is_authorized(envelope):
        return
    handler = bot_module.BUTTON_ACTIONS.get(envelope.text.strip())
    if handler:
        await handler(update, context)
        return
    await platform.register_message(update, envelope)
    if not await platform.should_respond(envelope):
        return
    await message.reply_chat_action(ChatAction.TYPING)
    await bot_module._set_reaction_safely(message, ReactionEmoji.EYES)
    progress: Message | None = await message.reply_text("מטפל בזה…") if envelope.is_private else None
    if envelope.is_group:
        await platform.raw_api.send_ephemeral_text(chat_id=envelope.chat_id, receiver_user_id=envelope.user_id,
                                                   message_thread_id=envelope.thread_id, text="מטפל בזה…")
    try:
        result = await platform.answer(update)
    except Exception:
        incident = uuid.uuid4().hex[:8]
        bot_module.log.exception("Telegram agent failed incident=%s", incident)
        await bot_module._set_reaction_safely(message, ReactionEmoji.THUMBS_DOWN, is_big=True)
        error = f"לא הצלחתי להשלים את הפעולה. אפשר לנסות שוב.\nקוד תקלה: <code>{incident}</code>"
        await (progress.edit_text(error, parse_mode=ParseMode.HTML) if progress else message.reply_text(error, parse_mode=ParseMode.HTML))
        return
    if not result:
        if progress:
            await progress.delete()
        return
    answer, _agent_id = result
    pending = parse_marker(answer)
    if pending:
        await (progress.edit_text(str(pending["summary"]), reply_markup=_buttons(str(pending["id"])))
               if progress else message.reply_text(str(pending["summary"]), reply_markup=_buttons(str(pending["id"]))))
        return
    if len(answer) <= 4000:
        await (progress.edit_text(answer) if progress else message.reply_text(answer))
        await bot_module._set_reaction_safely(message, ReactionEmoji.THUMBS_UP, is_big=True)
        return
    if progress:
        await progress.delete()
    for offset in range(0, len(answer), 4000):
        await message.reply_text(answer[offset:offset + 4000])
    await bot_module._set_reaction_safely(message, ReactionEmoji.THUMBS_UP, is_big=True)


async def handle_callback(update: Update, context) -> bool:
    query = update.callback_query
    data = (query.data if query else "") or ""
    if not data.startswith("inbox_"):
        return False
    platform, user = bot_module._platform(context), update.effective_user
    envelope = platform.envelope(update)
    if not query or not user or not envelope or not platform.is_authorized(envelope):
        return True
    action, _, action_id = data.partition(":")
    row = await _get(platform.telegram_store, action_id) if action_id else None
    if not row or int(row["user_id"]) != user.id or int(row["chat_id"]) != envelope.chat_id:
        await query.answer("ההצעה לא נמצאה או שאינה שלך", show_alert=True)
        return True
    if float(row["expires_at"]) <= time.time():
        await _status(platform.telegram_store, action_id, "expired")
        await query.answer("ההצעה פגה. שלח את הבקשה מחדש.", show_alert=True)
        await query.edit_message_text("⌛ ההצעה פגה ולא בוצעה.")
        return True
    if action in {"inbox_cancel", "inbox_edit"}:
        if row["status"] not in {"pending", "failed"}:
            await query.answer("ההצעה כבר טופלה", show_alert=True)
            return True
        if action == "inbox_cancel":
            await _status(platform.telegram_store, action_id, "cancelled")
            await query.answer("בוטל")
            await query.edit_message_text(f"❌ בוטל — לא בוצע דבר.\n\n{row['summary']}")
        else:
            await _status(platform.telegram_store, action_id, "editing")
            await query.answer("אפשר לשלוח תיקון")
            await query.edit_message_text("✏️ ההצעה לא בוצעה.\n\nשלח עכשיו את הנוסח המתוקן כהודעה חדשה, ואני אכין הצעה חדשה.\n\n" + str(row["summary"]))
        return True
    if action != "inbox_confirm":
        await query.answer("פעולה לא מוכרת", show_alert=True)
        return True
    claimed = await _claim(platform.telegram_store, action_id, user.id, envelope.chat_id)
    if not claimed:
        await query.answer("ההצעה כבר טופלה או פגה", show_alert=True)
        return True
    await query.answer("מבצע…")
    try:
        actions = json.loads(str(claimed["actions_json"]))
        prior = json.loads(str(claimed.get("result_json") or "[]"))
        results = prior if isinstance(prior, list) else []
        completed = {int(item["index"]) for item in results if isinstance(item, dict)
                     and isinstance(item.get("result"), dict) and item["result"].get("ok")
                     and str(item.get("index", "")).isdigit()}
        settings, service, store = (context.application.bot_data[key] for key in ("settings", "service", "store"))
        all_ok = isinstance(actions, list)
        for index, item in enumerate(actions if isinstance(actions, list) else []):
            if index in completed:
                continue
            name = str(item.get("name") or "")
            args = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
            raw = await execute_tool(store, service, name, args, user.id, settings=settings)
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                parsed = {"ok": False, "error": "invalid tool response"}
            results = [entry for entry in results if not (isinstance(entry, dict) and entry.get("index") == index)]
            results.append({"index": index, "name": name, "result": parsed})
            await _status(platform.telegram_store, action_id, "executing", results)
            if not parsed.get("ok"):
                all_ok = False
                break
        if all_ok:
            await _status(platform.telegram_store, action_id, "completed", results)
            summary = str(claimed["summary"]).replace("📥 לפני ביצוע", "✅ בוצע").replace("הפעולה עדיין לא בוצעה.", "הפעולה בוצעה.")
            await query.edit_message_text(summary)
        else:
            await _status(platform.telegram_store, action_id, "failed", results)
            await query.edit_message_text("⚠️ לא הצלחתי להשלים את הפעולה. אפשר לנסות שוב בלי ליצור כפילות.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("נסה שוב", callback_data=f"inbox_confirm:{action_id}")],
                [InlineKeyboardButton("ביטול", callback_data=f"inbox_cancel:{action_id}")],
            ]))
    except Exception as exc:
        log.exception("smart inbox execution failed action=%s", action_id)
        await _status(platform.telegram_store, action_id, "failed", {"error": str(exc)[:500]})
        await query.edit_message_text("⚠️ לא הצלחתי להשלים את הפעולה. אפשר לנסות שוב.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("נסה שוב", callback_data=f"inbox_confirm:{action_id}")],
            [InlineKeyboardButton("ביטול", callback_data=f"inbox_cancel:{action_id}")],
        ]))
    return True


def apply_runtime_patches() -> None:
    global _applied
    if _applied:
        return
    TelegramStore.ensure_schema = _ensure_schema
    TelegramAgentRuntime._system_prompt = _system_prompt
    TelegramAgentRuntime.reply = _reply
    agent_module.run_tool = _run_tool
    _applied = True
