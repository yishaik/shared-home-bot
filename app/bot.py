from __future__ import annotations

import html
import logging
import uuid

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    Update,
    WebAppInfo,
)
from telegram.constants import ChatAction, ParseMode, ReactionEmoji
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.agent_profiles import DEFAULT_AGENT_PROFILES
from app.config import Settings
from app.services import HomeService
from app.store_v2 import Store
from app.telegram_platform import TelegramPlatform


log = logging.getLogger("homebot.bot")


def _app_keyboard(settings: Settings) -> InlineKeyboardMarkup | None:
    if not settings.resolved_mini_app_url:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🏠 פתיחת הבית", web_app=WebAppInfo(url=settings.resolved_mini_app_url))]]
    )


# Persistent bottom keyboard so the household never has to type / commands.
# It holds the daily-frequent actions; everything else lives behind 📋 תפריט so
# no action is duplicated across the two surfaces. Tapping a label sends its text,
# which on_text routes to the matching action.
def _reply_keyboard(settings: Settings) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton("🛒 קניות"), KeyboardButton("✅ משימות")],
        [KeyboardButton("📅 אירועים"), KeyboardButton("📋 תפריט")],
    ]
    if settings.resolved_mini_app_url:
        rows.append([KeyboardButton("🏠 אפליקציה", web_app=WebAppInfo(url=settings.resolved_mini_app_url))])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    # Only the actions that are NOT on the persistent keyboard live here, so the
    # two surfaces never duplicate. Daily actions (קניות/משימות/אירועים/אפליקציה)
    # stay on the bottom keyboard.
    rows = [
        [
            InlineKeyboardButton("🧠 זיכרון", callback_data="menu:memory"),
            InlineKeyboardButton("🤖 סוכנים", callback_data="menu:agents"),
        ],
        [
            InlineKeyboardButton("🗂 Topics", callback_data="menu:topics"),
            InlineKeyboardButton("❓ עזרה", callback_data="menu:help"),
        ],
    ]
    await update.effective_message.reply_text(
        "עוד פעולות — הפעולות היומיומיות נמצאות בכפתורים שלמטה:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def _set_reaction_safely(message: Message, reaction: ReactionEmoji, *, is_big: bool = False) -> None:
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


def _platform(context: ContextTypes.DEFAULT_TYPE) -> TelegramPlatform:
    return context.application.bot_data["telegram_platform"]


async def _authorized(update: Update, context: ContextTypes.DEFAULT_TYPE, *, admin: bool = False) -> bool:
    platform = _platform(context)
    envelope = platform.envelope(update)
    if envelope and platform.is_authorized(envelope) and (not admin or platform.is_admin_user(envelope.user_id)):
        return True
    message = update.effective_message
    if message:
        await message.reply_text("הבוט הזה פרטי ואינו מורשה לפעול עבור המשתמש או הצ׳אט הזה.")
    return False


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    settings: Settings = context.application.bot_data["settings"]
    store: Store = context.application.bot_data["store"]
    user = update.effective_user
    if not user or not update.effective_message:
        return
    await store.upsert_member_profile(user.id, user.full_name, user.username or "")
    text = (
        f"<b>ברוך הבא ל־{html.escape(settings.home_name)}</b> 🏠\n\n"
        "אפשר לדבר איתי באופן טבעי, או להשתמש בכפתורים שלמטה — בלי לזכור פקודות.\n\n"
        "דוגמאות:\n"
        "• הוסף חלב לרשימת הקניות\n"
        "• תזכיר לנו להזמין אינסטלטור ביום חמישי\n"
        "• 📋 תפריט — כל הפעולות במקום אחד"
    )
    envelope = _platform(context).envelope(update)
    reply_markup = _reply_keyboard(settings) if (envelope and envelope.is_private) else _app_keyboard(settings)
    await update.effective_message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    settings: Settings = context.application.bot_data["settings"]
    await update.effective_message.reply_text(
        "אפשר לדבר איתי בשפה חופשית, להשתמש בכפתורים שלמטה, או ב־📋 /menu.\n\n"
        "פקודות זמינות גם ידנית:\n"
        "/todos — משימות פתוחות\n"
        "/shop — רשימת קניות\n"
        "/events — אירועים\n"
        "/memory — מידע שמור\n"
        "/agents — סוכנים זמינים\n"
        "/agent tasks — חיבור ה־Topic הנוכחי לסוכן\n"
        "/topic שם | agent — יצירת Topic חדש\n"
        "/topics — נושאים שנצפו או נוצרו\n"
        "/chatid — מזהה הצ׳אט להגדרת הרשאות\n"
        "/app — פתיחת אפליקציית הבית",
        reply_markup=_app_keyboard(settings),
    )


async def cmd_app(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    settings: Settings = context.application.bot_data["settings"]
    keyboard = _app_keyboard(settings)
    if keyboard:
        await update.effective_message.reply_text("כל הבית, במקום אחד:", reply_markup=keyboard)
    else:
        await update.effective_message.reply_text("כתובת ה־Mini App עדיין לא הוגדרה.")


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user and update.effective_message:
        await update.effective_message.reply_text(
            f"Telegram ID: <code>{user.id}</code>", parse_mode=ParseMode.HTML
        )


async def cmd_chatid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.effective_message
    if chat and message:
        await message.reply_text(
            f"Chat ID: <code>{chat.id}</code>\n"
            f"Topic ID: <code>{message.message_thread_id or 0}</code>",
            parse_mode=ParseMode.HTML,
        )


async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    envelope = _platform(context).envelope(update)
    if not envelope or not _platform(context).private_context_allowed(envelope):
        await update.effective_message.reply_text(
            "זיכרון פרטי אינו זמין בקבוצה הזו. אפשר להשתמש בפקודה בשיחה פרטית עם הבוט."
        )
        return
    settings: Settings = context.application.bot_data["settings"]
    store: Store = context.application.bot_data["store"]
    rows = await store.list_memories(limit=20)
    if not rows:
        await update.effective_message.reply_text("עדיין אין מידע שמור בבית.")
        return
    lines = [f"• <b>{html.escape(str(row['key']))}</b>: {html.escape(str(row['value']))}" for row in rows]
    await update.effective_message.reply_text(
        "🧠 <b>מידע משותף</b>\n" + "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=_app_keyboard(settings),
    )


async def cmd_todos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    settings: Settings = context.application.bot_data["settings"]
    store: Store = context.application.bot_data["store"]
    rows = await store.list_todos(False)
    if not rows:
        await update.effective_message.reply_text("אין משימות פתוחות ✓", reply_markup=_app_keyboard(settings))
        return
    keyboard = [
        [InlineKeyboardButton(f"✓ {row['title'][:32]}", callback_data=f"todo_done:{row['id']}")]
        for row in rows[:8]
    ]
    if settings.resolved_mini_app_url:
        keyboard.append(
            [
                InlineKeyboardButton(
                    "כל המשימות", web_app=WebAppInfo(url=f"{settings.resolved_mini_app_url}?tab=tasks")
                )
            ]
        )
    await update.effective_message.reply_text(
        "✅ <b>משימות פתוחות</b>\nלחץ לסימון כהושלם:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_shop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    settings: Settings = context.application.bot_data["settings"]
    store: Store = context.application.bot_data["store"]
    rows = await store.shop_list(False)
    if not rows:
        await update.effective_message.reply_text("רשימת הקניות ריקה 🛒", reply_markup=_app_keyboard(settings))
        return
    keyboard = [
        [InlineKeyboardButton(f"✓ {row['item'][:32]} × {row['qty']}", callback_data=f"shop_done:{row['id']}")]
        for row in rows[:8]
    ]
    if settings.resolved_mini_app_url:
        keyboard.append(
            [
                InlineKeyboardButton(
                    "למצב קניות",
                    web_app=WebAppInfo(url=f"{settings.resolved_mini_app_url}?tab=shopping"),
                )
            ]
        )
    await update.effective_message.reply_text(
        "🛒 <b>רשימת קניות</b>\nלחץ לאחר שהפריט נרכש:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_events(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    settings: Settings = context.application.bot_data["settings"]
    store: Store = context.application.bot_data["store"]
    rows = await store.list_events()
    if not rows:
        await update.effective_message.reply_text("אין אירועים שמורים.", reply_markup=_app_keyboard(settings))
        return
    lines = [
        f"• <b>{html.escape(str(row['title']))}</b> — "
        f"{html.escape(str(row.get('start_at') or row.get('when_text') or ''))}"
        for row in rows[:12]
    ]
    await update.effective_message.reply_text(
        "📅 <b>אירועים</b>\n" + "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=_app_keyboard(settings),
    )


async def cmd_agents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    lines = [
        f"• <code>{profile.id}</code> — <b>{html.escape(profile.name)}</b>: {html.escape(profile.description)}"
        for profile in DEFAULT_AGENT_PROFILES.values()
    ]
    await update.effective_message.reply_text(
        "🤖 <b>סוכנים זמינים</b>\n" + "\n".join(lines), parse_mode=ParseMode.HTML
    )


async def cmd_agent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context, admin=True):
        return
    message = update.effective_message
    chat = update.effective_chat
    if not message:
        return
    if not chat or message.message_thread_id is None:
        await message.reply_text("יש להפעיל את הפקודה מתוך Topic קיים.")
        return
    platform = _platform(context)
    requested = (context.args[0].lower() if context.args else "").strip()
    if not requested:
        current = await platform.telegram_store.topic_agent(chat.id, message.message_thread_id)
        await message.reply_text(f"הסוכן המחובר: {current or 'auto'}")
        return
    if requested == "auto":
        await platform.telegram_store.bind_topic(chat.id, message.message_thread_id, None)
        await message.reply_text("ה־Topic חזר לניתוב אוטומטי.")
        return
    if requested not in DEFAULT_AGENT_PROFILES:
        await message.reply_text("סוכן לא מוכר. השתמש ב־/agents להצגת הרשימה.")
        return
    await platform.telegram_store.bind_topic(chat.id, message.message_thread_id, requested)
    await message.reply_text(f"ה־Topic חובר לסוכן: {DEFAULT_AGENT_PROFILES[requested].name}")


async def cmd_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context, admin=True):
        return
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat or not user:
        return
    raw = " ".join(context.args).strip()
    if not raw:
        await message.reply_text("שימוש: /topic שם הנושא | agent\nלדוגמה: /topic תכנון החודש | calendar")
        return
    name, separator, agent_id = raw.partition("|")
    name = name.strip()
    agent_id = agent_id.strip().lower() if separator else ""
    if not name:
        await message.reply_text("יש לציין שם ל־Topic.")
        return
    if agent_id and agent_id not in DEFAULT_AGENT_PROFILES:
        await message.reply_text("סוכן לא מוכר. השתמש ב־/agents להצגת הרשימה.")
        return
    platform = _platform(context)
    try:
        topic = await platform.create_topic(
            bot=context.bot,
            chat=chat,
            user_id=user.id,
            name=name,
            agent_id=agent_id or None,
        )
    except TelegramError as exc:
        log.warning("topic creation failed chat=%s: %s", chat.id, exc)
        await message.reply_text(
            "לא הצלחתי ליצור Topic. בקבוצה יש להפעיל Topics ולתת לבוט הרשאת Manage Topics."
        )
        return
    except (PermissionError, ValueError) as exc:
        await message.reply_text(str(exc))
        return
    suffix = f" · {DEFAULT_AGENT_PROFILES[agent_id].name}" if agent_id else " · ניתוב אוטומטי"
    await context.bot.send_message(
        chat_id=chat.id,
        message_thread_id=topic.message_thread_id,
        text=f"Topic נוצר: {topic.name}{suffix}",
    )


async def cmd_topics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context):
        return
    chat = update.effective_chat
    if not chat:
        return
    rows = await _platform(context).telegram_store.list_topics(chat.id)
    if not rows:
        await update.effective_message.reply_text("עדיין לא נרשמו Topics בצ׳אט הזה.")
        return
    lines = [
        f"• <code>{row['thread_id']}</code> — {html.escape(row['name'] or '(ללא שם)')} "
        f"[{row['status']}] · {html.escape(row['agent_id'] or 'auto')}"
        for row in rows[:30]
    ]
    await update.effective_message.reply_text(
        "<b>Topics</b>\n" + "\n".join(lines), parse_mode=ParseMode.HTML
    )


async def _require_current_topic(update: Update) -> tuple[Message, int] | None:
    message = update.effective_message
    if not message or message.message_thread_id is None:
        if message:
            await message.reply_text("יש להפעיל את הפקודה מתוך Topic.")
        return None
    return message, message.message_thread_id


async def cmd_topic_rename(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context, admin=True):
        return
    current = await _require_current_topic(update)
    if not current:
        return
    message, thread_id = current
    name = " ".join(context.args).strip()
    if not name:
        await message.reply_text("שימוש: /topic_rename שם חדש")
        return
    await context.bot.edit_forum_topic(chat_id=message.chat_id, message_thread_id=thread_id, name=name[:128])
    await _platform(context).telegram_store.rename_topic(message.chat_id, thread_id, name[:128])
    await message.reply_text("שם ה־Topic עודכן.")


async def cmd_topic_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context, admin=True):
        return
    current = await _require_current_topic(update)
    if not current:
        return
    message, thread_id = current
    await context.bot.close_forum_topic(chat_id=message.chat_id, message_thread_id=thread_id)
    await _platform(context).telegram_store.set_topic_status(message.chat_id, thread_id, "closed")


async def cmd_topic_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context, admin=True):
        return
    current = await _require_current_topic(update)
    if not current:
        return
    message, thread_id = current
    await context.bot.reopen_forum_topic(chat_id=message.chat_id, message_thread_id=thread_id)
    await _platform(context).telegram_store.set_topic_status(message.chat_id, thread_id, "open")
    await message.reply_text("ה־Topic נפתח מחדש.")


async def cmd_topic_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update, context, admin=True):
        return
    current = await _require_current_topic(update)
    if not current:
        return
    message, thread_id = current
    await context.bot.delete_forum_topic(chat_id=message.chat_id, message_thread_id=thread_id)
    await _platform(context).telegram_store.set_topic_status(message.chat_id, thread_id, "deleted")
    await context.bot.send_message(chat_id=message.chat_id, text=f"Topic {thread_id} נמחק.")


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    platform = _platform(context)
    envelope = platform.envelope(update)
    query = update.callback_query
    user = update.effective_user
    service: HomeService = context.application.bot_data["service"]
    if not query or not user or not envelope or not platform.is_authorized(envelope):
        return
    await query.answer()
    action, _, raw_id = (query.data or "").partition(":")
    if action == "menu":
        handler = MENU_ACTIONS.get(raw_id)
        if handler is not None:
            await handler(update, context)
        return
    try:
        entity_id = int(raw_id)
    except ValueError:
        return
    if action == "todo_done":
        item = await service.update_todo(user.id, entity_id, done=True)
        if item:
            await query.edit_message_text(
                f"✓ הושלמה: {item['title']}",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("ביטול", callback_data=f"todo_undo:{entity_id}")]]
                ),
            )
    elif action == "todo_undo":
        item = await service.update_todo(user.id, entity_id, done=False)
        if item:
            await query.edit_message_text(f"↩ המשימה הוחזרה: {item['title']}")
    elif action == "shop_done":
        item = await service.update_shopping(user.id, entity_id, done=True)
        if item:
            await query.edit_message_text(
                f"✓ נרכש: {item['item']}",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("ביטול", callback_data=f"shop_undo:{entity_id}")]]
                ),
            )
    elif action == "shop_undo":
        item = await service.update_shopping(user.id, entity_id, done=False)
        if item:
            await query.edit_message_text(f"↩ הוחזר לרשימה: {item['item']}")


async def on_service_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message:
        await _platform(context).sync_topic_service_message(message)


async def on_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    change = update.my_chat_member or update.chat_member
    if not change:
        return
    platform = _platform(context)
    status = str(change.new_chat_member.status)
    user_id = change.new_chat_member.user.id
    await platform.telegram_store.record_membership(
        change.chat.id,
        user_id,
        status,
        role="bot" if change.new_chat_member.user.is_bot else "member",
    )
    if update.my_chat_member:
        await platform.set_chat_active(change.chat, status not in {"left", "kicked"})


# Reply-keyboard labels → the handler they trigger. Kept next to the keyboard so
# the two never drift apart. "🏠 אפליקציה" opens the Mini App directly (web_app),
# so it never arrives here as text and needs no mapping.
BUTTON_ACTIONS = {
    "🛒 קניות": cmd_shop,
    "✅ משימות": cmd_todos,
    "📅 אירועים": cmd_events,
    "📋 תפריט": cmd_menu,
}

# The "more" drawer behind 📋 תפריט — disjoint from the keyboard above (no dupes).
MENU_ACTIONS = {
    "memory": cmd_memory,
    "agents": cmd_agents,
    "topics": cmd_topics,
    "help": cmd_help,
}


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    platform = _platform(context)
    envelope = platform.envelope(update)
    message = update.effective_message
    if not envelope or not message or not envelope.text or not platform.is_authorized(envelope):
        return
    # Reply-keyboard taps are handled directly and kept out of the agent transcript.
    handler = BUTTON_ACTIONS.get(envelope.text.strip())
    if handler is not None:
        await handler(update, context)
        return
    await platform.register_message(update, envelope)
    if not await platform.should_respond(envelope):
        return

    await message.reply_chat_action(ChatAction.TYPING)
    await _set_reaction_safely(message, ReactionEmoji.EYES)
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
        log.exception("Telegram agent failed incident=%s", incident)
        await _set_reaction_safely(message, ReactionEmoji.THUMBS_DOWN, is_big=True)
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
    if len(answer) <= 4000:
        if progress:
            await progress.edit_text(answer)
        else:
            await message.reply_text(answer)
        await _set_reaction_safely(message, ReactionEmoji.THUMBS_UP, is_big=True)
        return

    if progress:
        await progress.delete()
    for offset in range(0, len(answer), 4000):
        await message.reply_text(answer[offset : offset + 4000])
    await _set_reaction_safely(message, ReactionEmoji.THUMBS_UP, is_big=True)


def build_application(settings: Settings, store: Store, service: HomeService) -> Application:
    platform = TelegramPlatform(settings, store, service)
    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .concurrent_updates(settings.telegram_concurrent_updates)
        .build()
    )
    application.bot_data.update(
        settings=settings,
        store=store,
        service=service,
        telegram_platform=platform,
    )

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("menu", cmd_menu))
    application.add_handler(CommandHandler("app", cmd_app))
    application.add_handler(CommandHandler("whoami", cmd_whoami))
    application.add_handler(CommandHandler("chatid", cmd_chatid))
    application.add_handler(CommandHandler("memory", cmd_memory))
    application.add_handler(CommandHandler("todos", cmd_todos))
    application.add_handler(CommandHandler(["shop", "shopping"], cmd_shop))
    application.add_handler(CommandHandler("events", cmd_events))
    application.add_handler(CommandHandler("agents", cmd_agents))
    application.add_handler(CommandHandler("agent", cmd_agent))
    application.add_handler(CommandHandler("topic", cmd_topic))
    application.add_handler(CommandHandler("topics", cmd_topics))
    application.add_handler(CommandHandler("topic_rename", cmd_topic_rename))
    application.add_handler(CommandHandler("topic_close", cmd_topic_close))
    application.add_handler(CommandHandler("topic_open", cmd_topic_open))
    application.add_handler(CommandHandler("topic_delete", cmd_topic_delete))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(ChatMemberHandler(on_chat_member, ChatMemberHandler.ANY_CHAT_MEMBER))
    application.add_handler(MessageHandler(filters.StatusUpdate.ALL, on_service_update))
    application.add_handler(MessageHandler((filters.TEXT | filters.CAPTION) & ~filters.COMMAND, on_text))
    return application
