from __future__ import annotations

import html

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.constants import ParseMode

from app import bot as bot_module
from app.calendar_service import CalendarService
from app.member_service import MemberService
from app.work_service import WorkService

_original_start = bot_module.cmd_start
_original_help = bot_module.cmd_help
_original_app = bot_module.cmd_app
_original_memory = bot_module.cmd_memory
_original_shop = bot_module.cmd_shop
_original_text = bot_module.on_text


async def _touch(update, context, *, started: bool = False) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user:
        return
    chat_id = message.chat_id if message and getattr(message.chat, "type", "") == "private" else None
    await MemberService(context.application.bot_data["store"]).touch(
        user.id, user.full_name, user.username or "", private_chat_id=chat_id, started=started,
    )


async def cmd_start(update, context):
    await _touch(update, context, started=True)
    await _original_start(update, context)


async def cmd_help(update, context):
    await _touch(update, context)
    await _original_help(update, context)


async def cmd_app(update, context):
    await _touch(update, context)
    await _original_app(update, context)


async def cmd_memory(update, context):
    await _touch(update, context)
    await _original_memory(update, context)


async def cmd_shop(update, context):
    await _touch(update, context)
    await _original_shop(update, context)


async def cmd_whoami(update, context):
    await _touch(update, context)
    user = update.effective_user
    if user and update.effective_message:
        username = f" · @{html.escape(user.username)}" if user.username else ""
        await update.effective_message.reply_text(
            f"אתה מזוהה כ־<b>{html.escape(user.full_name)}</b>{username}", parse_mode=ParseMode.HTML,
        )


async def cmd_todos(update, context):
    if not await bot_module._authorized(update, context):
        return
    await _touch(update, context)
    settings = context.application.bot_data["settings"]
    store = context.application.bot_data["store"]
    rows = await WorkService(settings, store).list_tasks()
    if not rows:
        await update.effective_message.reply_text("אין משימות פתוחות ✓", reply_markup=bot_module._app_keyboard(settings))
        return
    keyboard = []
    for row in rows[:8]:
        prefix = "🔒" if row.get("blocked") else "✓"
        keyboard.append([InlineKeyboardButton(f"{prefix} {row['title'][:30]}", callback_data=f"todo_done:{row['id']}")])
    if settings.resolved_mini_app_url:
        keyboard.append([InlineKeyboardButton("כל העבודה", web_app=WebAppInfo(url=f"{settings.resolved_mini_app_url}?tab=tasks"))])
    await update.effective_message.reply_text(
        "✅ <b>משימות פתוחות</b>\nלחץ לסימון כהושלם:",
        parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_events(update, context):
    if not await bot_module._authorized(update, context):
        return
    await _touch(update, context)
    settings = context.application.bot_data["settings"]
    store = context.application.bot_data["store"]
    calendar = CalendarService(settings, store)
    if settings.google_enabled:
        try:
            await calendar.incremental_sync()
        except Exception:
            bot_module.log.exception("calendar sync failed in /events")
    rows = await calendar.list_events()
    if not rows:
        await update.effective_message.reply_text("אין אירועים קרובים ביומן המשותף.", reply_markup=bot_module._app_keyboard(settings))
        return
    lines = []
    for row in rows[:12]:
        start, end = html.escape(str(row.get("start_at") or "")), html.escape(str(row.get("end_at") or ""))
        lines.append(f"• <b>{html.escape(str(row['title']))}</b> — {start}{f' → {end}' if end else ''}")
    await update.effective_message.reply_text(
        "📅 <b>אירועים מ־Google Calendar</b>\n" + "\n".join(lines),
        parse_mode=ParseMode.HTML, reply_markup=bot_module._app_keyboard(settings),
    )


async def on_callback(update, context):
    platform = bot_module._platform(context)
    envelope = platform.envelope(update)
    query, user = update.callback_query, update.effective_user
    if not query or not user or not envelope or not platform.is_authorized(envelope):
        return
    await _touch(update, context)
    await query.answer()
    action, _, raw_id = (query.data or "").partition(":")
    try:
        entity_id = int(raw_id)
    except ValueError:
        return
    settings, store = context.application.bot_data["settings"], context.application.bot_data["store"]
    work = WorkService(settings, store)
    service = context.application.bot_data["service"]
    if action == "todo_done":
        item = await work.update_task(user.id, entity_id, status="completed")
        if item:
            await query.edit_message_text(f"✓ הושלמה: {item['title']}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ביטול", callback_data=f"todo_undo:{entity_id}")]]))
    elif action == "todo_start":
        item = await work.update_task(user.id, entity_id, status="in_progress")
        if item:
            await query.edit_message_text(f"▶ התחלת לעבוד על: {item['title']}")
    elif action == "todo_undo":
        item = await work.update_task(user.id, entity_id, status="todo")
        if item:
            await query.edit_message_text(f"↩ המשימה הוחזרה: {item['title']}")
    elif action == "shop_done":
        item = await service.update_shopping(user.id, entity_id, done=True)
        if item:
            await query.edit_message_text(f"✓ נרכש: {item['item']}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ביטול", callback_data=f"shop_undo:{entity_id}")]]))
    elif action == "shop_undo":
        item = await service.update_shopping(user.id, entity_id, done=False)
        if item:
            await query.edit_message_text(f"↩ הוחזר לרשימה: {item['item']}")


async def on_text(update, context):
    await _touch(update, context, started=True)
    await _original_text(update, context)


def apply() -> None:
    bot_module.cmd_start = cmd_start
    bot_module.cmd_help = cmd_help
    bot_module.cmd_app = cmd_app
    bot_module.cmd_whoami = cmd_whoami
    bot_module.cmd_memory = cmd_memory
    bot_module.cmd_todos = cmd_todos
    bot_module.cmd_shop = cmd_shop
    bot_module.cmd_events = cmd_events
    bot_module.on_callback = on_callback
    bot_module.on_text = on_text
