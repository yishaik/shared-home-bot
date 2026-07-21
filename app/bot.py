"""Telegram handlers — every allowed user shares the same agent memory."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.agent import HomeAgent
from app.config import Settings
from app.db import Store

log = logging.getLogger("homebot.bot")


def _is_allowed(settings: Settings, user_id: int) -> bool:
    if not settings.allowed_user_ids:
        # empty allowlist = open (not recommended); require at least one in prod
        return True
    return user_id in settings.allowed_user_ids


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    user = update.effective_user
    if not user or not _is_allowed(settings, user.id):
        await update.effective_message.reply_text("Sorry — this bot is private.")
        return
    await update.effective_message.reply_text(
        f"Hey {user.first_name or 'there'} 👋\n"
        f"I'm *{settings.bot_display_name}* — shared home assistant for *{settings.home_name}*.\n\n"
        "Both of you talk to me here. Memory, todos, notes, and events are shared.\n\n"
        "Try:\n"
        "• «תזכור שהאינטרנט זה X»\n"
        "• «הוסף לקניות חלב»\n"
        "• /memory /todos /help\n",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Commands:\n"
        "/start — intro\n"
        "/help — this\n"
        "/whoami — your Telegram user id (for ALLOWED_USER_IDS)\n"
        "/memory — shared facts\n"
        "/todos — open todos\n"
        "/shop — shopping list\n"
        "/notes — notes\n"
        "/events — events\n"
        "/inventory — what's at home\n"
        "/people — household people\n\n"
        "Otherwise just chat — I use tools so both of you share the same brain."
    )


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    await update.effective_message.reply_text(
        f"id: `{user.id}`\nusername: @{user.username or '—'}\nname: {user.full_name}",
        parse_mode="Markdown",
    )


async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    store: Store = context.application.bot_data["store"]
    user = update.effective_user
    if not user or not _is_allowed(settings, user.id):
        return
    rows = await store.list_memories()
    if not rows:
        await update.effective_message.reply_text("Shared memory is empty.")
        return
    lines = [f"• [{r['category']}] *{r['key']}*: {r['value']}" for r in rows]
    text = "🧠 Shared memory\n" + "\n".join(lines)
    await update.effective_message.reply_text(text[:4000], parse_mode="Markdown")


async def cmd_todos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    store: Store = context.application.bot_data["store"]
    user = update.effective_user
    if not user or not _is_allowed(settings, user.id):
        return
    rows = await store.list_todos(include_done=False)
    if not rows:
        await update.effective_message.reply_text("No open todos.")
        return
    lines = [f"#{r['id']} — {r['title']}" for r in rows]
    await update.effective_message.reply_text("✅ Open todos\n" + "\n".join(lines))


async def cmd_notes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    store: Store = context.application.bot_data["store"]
    user = update.effective_user
    if not user or not _is_allowed(settings, user.id):
        return
    rows = await store.list_notes()
    if not rows:
        await update.effective_message.reply_text("No notes yet.")
        return
    lines = [f"• {r['title']}" + (f" [{r['tags']}]" if r["tags"] else "") for r in rows]
    await update.effective_message.reply_text("📝 Notes\n" + "\n".join(lines))


async def cmd_events(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    store: Store = context.application.bot_data["store"]
    user = update.effective_user
    if not user or not _is_allowed(settings, user.id):
        return
    rows = await store.list_events()
    if not rows:
        await update.effective_message.reply_text("No events yet.")
        return
    lines = [f"• {r['title']} @ {r['when_text']}" for r in rows]
    await update.effective_message.reply_text("📅 Events\n" + "\n".join(lines))


async def cmd_shop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    store: Store = context.application.bot_data["store"]
    user = update.effective_user
    if not user or not _is_allowed(settings, user.id):
        return
    rows = await store.shop_list(include_done=False)
    if not rows:
        await update.effective_message.reply_text("🛒 Shopping list is empty.")
        return
    lines = [f"#{r['id']} {r['item']} × {r['qty']}" for r in rows]
    await update.effective_message.reply_text("🛒 Shopping\n" + "\n".join(lines))


async def cmd_inventory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    store: Store = context.application.bot_data["store"]
    user = update.effective_user
    if not user or not _is_allowed(settings, user.id):
        return
    rows = await store.inventory_list()
    if not rows:
        await update.effective_message.reply_text("Inventory is empty.")
        return
    lines = [f"• {r['item']}: {r['qty']} @ {r['location']}" for r in rows]
    await update.effective_message.reply_text("📦 Inventory\n" + "\n".join(lines[:80]))


async def cmd_people(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    store: Store = context.application.bot_data["store"]
    user = update.effective_user
    if not user or not _is_allowed(settings, user.id):
        return
    rows = await store.people_list()
    if not rows:
        await update.effective_message.reply_text("No people saved yet.")
        return
    lines = []
    for r in rows:
        bit = f"• {r['name']}"
        if r["relation"]:
            bit += f" ({r['relation']})"
        if r["prefs"]:
            bit += f" — {r['prefs']}"
        lines.append(bit)
    await update.effective_message.reply_text("👥 People\n" + "\n".join(lines))


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    agent: HomeAgent = context.application.bot_data["agent"]
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg or not msg.text:
        return
    if not _is_allowed(settings, user.id):
        await msg.reply_text("Sorry — this bot is private.")
        return

    await context.bot.send_chat_action(chat_id=msg.chat_id, action="typing")
    try:
        answer = await agent.reply(
            user_text=msg.text,
            user_id=user.id,
            username=user.username,
            display_name=user.full_name,
        )
    except Exception as e:
        log.exception("agent failed")
        await msg.reply_text(f"Something went wrong: {e}")
        return

    # Telegram 4096 limit
    for i in range(0, len(answer), 4000):
        await msg.reply_text(answer[i : i + 4000])


def build_application(settings: Settings, store: Store, agent: HomeAgent) -> Application:
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .concurrent_updates(True)
        .build()
    )
    app.bot_data["settings"] = settings
    app.bot_data["store"] = store
    app.bot_data["agent"] = agent

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("whoami", cmd_whoami))
    app.add_handler(CommandHandler("memory", cmd_memory))
    app.add_handler(CommandHandler("todos", cmd_todos))
    app.add_handler(CommandHandler("notes", cmd_notes))
    app.add_handler(CommandHandler("events", cmd_events))
    app.add_handler(CommandHandler("shop", cmd_shop))
    app.add_handler(CommandHandler("shopping", cmd_shop))
    app.add_handler(CommandHandler("inventory", cmd_inventory))
    app.add_handler(CommandHandler("people", cmd_people))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app
