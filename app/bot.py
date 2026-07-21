from __future__ import annotations

import html
import logging
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update, WebAppInfo
from telegram.constants import ChatAction, ParseMode, ReactionEmoji
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from app.agent import HomeAgent
from app.config import Settings
from app.store_v2 import Store
from app.services import HomeService

log = logging.getLogger("homebot.bot")


def _is_allowed(settings: Settings, user_id: int) -> bool:
    return bool(settings.allowed_user_ids) and user_id in settings.allowed_user_ids


def _app_keyboard(settings: Settings) -> InlineKeyboardMarkup | None:
    if not settings.resolved_mini_app_url:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 פתיחת הבית", web_app=WebAppInfo(url=settings.resolved_mini_app_url))]])


async def _set_reaction_safely(message: Message, reaction: ReactionEmoji, *, is_big: bool = False) -> None:
    """Set a best-effort status reaction without interrupting message handling."""
    try:
        await message.set_reaction(reaction=reaction, is_big=is_big)
    except TelegramError as exc:
        log.warning(
            "could not set reaction chat_id=%s message_id=%s reaction=%s: %s",
            message.chat_id,
            message.message_id,
            reaction,
            exc,
        )


async def _authorized(update: Update, settings: Settings) -> bool:
    user = update.effective_user
    if user and _is_allowed(settings, user.id):
        return True
    message = update.effective_message
    if message:
        await message.reply_text("הבוט הזה פרטי ואינו מוגדר עבור החשבון הזה.")
    return False


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    store: Store = context.application.bot_data["store"]
    user = update.effective_user
    if not user or not await _authorized(update, settings):
        return
    await store.upsert_member_profile(user.id, user.full_name, user.username or "")
    text = (
        f"<b>ברוך הבא ל־{settings.home_name}</b> 🏠\n\n"
        "כאן מנהלים יחד קניות, משימות, אירועים, מידע חשוב וכל מה שצריך כדי שהבית יעבוד בשקט.\n\n"
        "אפשר פשוט לכתוב לי:\n"
        "• הוסף חלב לרשימת הקניות\n"
        "• תזכיר לנו להזמין אינסטלטור ביום חמישי\n"
        "• מה צריך לעשות השבוע?\n\n"
        "לסקירה ועריכה נוחה, פתח את אפליקציית הבית."
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=_app_keyboard(settings))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not await _authorized(update, settings):
        return
    await update.effective_message.reply_text(
        "אפשר לדבר איתי בשפה חופשית או להשתמש בקיצורים:\n"
        "/todos — משימות פתוחות\n"
        "/shop — רשימת קניות\n"
        "/events — אירועים\n"
        "/memory — מידע שמור\n"
        "/app — פתיחת אפליקציית הבית",
        reply_markup=_app_keyboard(settings),
    )


async def cmd_app(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not await _authorized(update, settings):
        return
    keyboard = _app_keyboard(settings)
    if keyboard:
        await update.effective_message.reply_text("כל הבית, במקום אחד:", reply_markup=keyboard)
    else:
        await update.effective_message.reply_text("כתובת ה־Mini App עדיין לא הוגדרה.")


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user:
        await update.effective_message.reply_text(f"Telegram ID: <code>{user.id}</code>", parse_mode=ParseMode.HTML)


async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    store: Store = context.application.bot_data["store"]
    if not await _authorized(update, settings):
        return
    rows = await store.list_memories(limit=20)
    if not rows:
        await update.effective_message.reply_text("עדיין אין מידע שמור בבית.")
        return
    lines = [f"• <b>{html.escape(str(row['key']))}</b>: {html.escape(str(row['value']))}" for row in rows]
    await update.effective_message.reply_text("🧠 <b>מידע משותף</b>\n" + "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=_app_keyboard(settings))


async def cmd_todos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    store: Store = context.application.bot_data["store"]
    if not await _authorized(update, settings):
        return
    rows = await store.list_todos(False)
    if not rows:
        await update.effective_message.reply_text("אין משימות פתוחות ✓", reply_markup=_app_keyboard(settings))
        return
    keyboard = [[InlineKeyboardButton(f"✓ {row['title'][:32]}", callback_data=f"todo_done:{row['id']}")] for row in rows[:8]]
    if settings.resolved_mini_app_url:
        keyboard.append([InlineKeyboardButton("כל המשימות", web_app=WebAppInfo(url=f"{settings.resolved_mini_app_url}?tab=tasks"))])
    await update.effective_message.reply_text("✅ <b>משימות פתוחות</b>\nלחץ לסימון כהושלם:", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))


async def cmd_shop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    store: Store = context.application.bot_data["store"]
    if not await _authorized(update, settings):
        return
    rows = await store.shop_list(False)
    if not rows:
        await update.effective_message.reply_text("רשימת הקניות ריקה 🛒", reply_markup=_app_keyboard(settings))
        return
    keyboard = [[InlineKeyboardButton(f"✓ {row['item'][:32]} × {row['qty']}", callback_data=f"shop_done:{row['id']}")] for row in rows[:8]]
    if settings.resolved_mini_app_url:
        keyboard.append([InlineKeyboardButton("למצב קניות", web_app=WebAppInfo(url=f"{settings.resolved_mini_app_url}?tab=shopping"))])
    await update.effective_message.reply_text("🛒 <b>רשימת קניות</b>\nלחץ לאחר שהפריט נרכש:", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))


async def cmd_events(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    store: Store = context.application.bot_data["store"]
    if not await _authorized(update, settings):
        return
    rows = await store.list_events()
    if not rows:
        await update.effective_message.reply_text("אין אירועים שמורים.", reply_markup=_app_keyboard(settings))
        return
    lines = [f"• <b>{html.escape(str(row['title']))}</b> — {html.escape(str(row.get('start_at') or row.get('when_text') or ''))}" for row in rows[:12]]
    await update.effective_message.reply_text("📅 <b>אירועים</b>\n" + "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=_app_keyboard(settings))


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    service: HomeService = context.application.bot_data["service"]
    query = update.callback_query
    user = update.effective_user
    if not query or not user or not _is_allowed(settings, user.id):
        return
    await query.answer()
    action, _, raw_id = (query.data or "").partition(":")
    try:
        entity_id = int(raw_id)
    except ValueError:
        return
    if action == "todo_done":
        item = await service.update_todo(user.id, entity_id, done=True)
        if item:
            await query.edit_message_text(f"✓ הושלמה: {item['title']}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ביטול", callback_data=f"todo_undo:{entity_id}")]]))
    elif action == "todo_undo":
        item = await service.update_todo(user.id, entity_id, done=False)
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


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    agent: HomeAgent = context.application.bot_data["agent"]
    user = update.effective_user
    message = update.effective_message
    if not user or not message or not message.text or not await _authorized(update, settings):
        return

    await context.bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.TYPING)
    await _set_reaction_safely(message, ReactionEmoji.EYES)
    progress = await message.reply_text("מטפל בזה…")
    try:
        answer = await agent.reply(user_text=message.text, user_id=user.id, username=user.username, display_name=user.full_name)
    except Exception:
        incident = uuid.uuid4().hex[:8]
        log.exception("agent failed incident=%s", incident)
        await _set_reaction_safely(message, ReactionEmoji.THUMBS_DOWN, is_big=True)
        await progress.edit_text(f"לא הצלחתי להשלים את הפעולה. אפשר לנסות שוב.\nקוד תקלה: <code>{incident}</code>", parse_mode=ParseMode.HTML)
        return

    if len(answer) <= 4000:
        await progress.edit_text(answer)
        await _set_reaction_safely(message, ReactionEmoji.THUMBS_UP, is_big=True)
        return
    await progress.delete()
    for offset in range(0, len(answer), 4000):
        await message.reply_text(answer[offset : offset + 4000])
    await _set_reaction_safely(message, ReactionEmoji.THUMBS_UP, is_big=True)


def build_application(settings: Settings, store: Store, service: HomeService, agent: HomeAgent) -> Application:
    application = Application.builder().token(settings.telegram_bot_token).concurrent_updates(True).build()
    application.bot_data.update(settings=settings, store=store, service=service, agent=agent)
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("app", cmd_app))
    application.add_handler(CommandHandler("whoami", cmd_whoami))
    application.add_handler(CommandHandler("memory", cmd_memory))
    application.add_handler(CommandHandler("todos", cmd_todos))
    application.add_handler(CommandHandler(["shop", "shopping"], cmd_shop))
    application.add_handler(CommandHandler("events", cmd_events))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return application
