from __future__ import annotations

import logging
import re

from telegram import Bot, Chat, Message, Update

from app.agent_profiles import AgentRouter, DEFAULT_AGENT_PROFILES
from app.config import Settings
from app.services import HomeService
from app.store_v2 import Store
from app.telegram_agent import TelegramAgentRuntime
from app.telegram_api import TelegramRawApi
from app.telegram_models import TelegramEnvelope
from app.telegram_store import TelegramStore


log = logging.getLogger("homebot.telegram.platform")


class TelegramPlatform:
    def __init__(self, settings: Settings, store: Store, service: HomeService):
        self.settings = settings
        self.store = store
        self.service = service
        self.telegram_store = TelegramStore(store)
        self.router = AgentRouter(self.telegram_store)
        self.agent = TelegramAgentRuntime(settings, store, service, self.telegram_store)
        self.raw_api = TelegramRawApi(settings)
        self.bot_username = ""

    async def initialize(self, bot: Bot) -> None:
        await self.telegram_store.ensure_schema()
        await self.telegram_store.prune_updates()
        me = await bot.get_me()
        self.bot_username = me.username or ""

    async def shutdown(self) -> None:
        await self.agent.shutdown()
        await self.raw_api.close()

    def envelope(self, update: Update) -> TelegramEnvelope | None:
        return TelegramEnvelope.from_update(update, bot_username=self.bot_username)

    def is_admin_user(self, user_id: int) -> bool:
        admins = self.settings.telegram_admin_user_ids or self.settings.allowed_user_ids
        return user_id in admins

    def is_authorized(self, envelope: TelegramEnvelope) -> bool:
        if envelope.user_id not in self.settings.allowed_user_ids:
            return False
        if envelope.is_private:
            return True
        if envelope.is_group:
            if envelope.chat_id in self.settings.allowed_chat_ids:
                return True
            return self.settings.telegram_allow_unlisted_groups
        return envelope.chat_id in self.settings.allowed_chat_ids

    async def register_message(self, update: Update, envelope: TelegramEnvelope) -> None:
        chat = update.effective_chat
        if not chat:
            return
        await self.telegram_store.upsert_chat(
            chat_id=chat.id,
            chat_type=str(chat.type),
            title=getattr(chat, "title", None) or getattr(chat, "full_name", None) or "",
            username=chat.username or "",
            is_forum=bool(chat.is_forum),
            is_direct_messages=bool(getattr(chat, "is_direct_messages", False)),
            parent_chat_id=getattr(getattr(chat, "parent_chat", None), "id", None),
        )
        if envelope.thread_id is not None:
            await self.telegram_store.upsert_topic(
                chat_id=envelope.chat_id,
                thread_id=envelope.thread_id,
                created_by=envelope.user_id,
            )

    async def should_respond(self, envelope: TelegramEnvelope) -> bool:
        if envelope.is_private:
            return True
        if not envelope.is_group:
            return False
        mode = self.settings.telegram_group_response_mode
        if mode == "all":
            return True
        bound = await self.telegram_store.topic_agent(envelope.chat_id, envelope.thread_id)
        if mode == "topics":
            return bound is not None
        if mode == "mentions":
            return envelope.bot_mentioned or envelope.reply_to_bot
        return bool(bound or envelope.bot_mentioned or envelope.reply_to_bot)

    def clean_user_text(self, text: str) -> str:
        cleaned = text
        if self.bot_username:
            cleaned = re.sub(
                rf"@{re.escape(self.bot_username)}\b",
                "",
                cleaned,
                flags=re.IGNORECASE,
            )
        cleaned = re.sub(r"(?:^|\s)(?:@agent:|agent:)[a-z0-9_-]+", " ", cleaned, flags=re.IGNORECASE)
        return " ".join(cleaned.split()).strip()

    async def answer(self, update: Update) -> tuple[str, str] | None:
        envelope = self.envelope(update)
        if not envelope or not envelope.text:
            return None
        if not self.is_authorized(envelope):
            return None
        await self.register_message(update, envelope)
        if not await self.should_respond(envelope):
            return None
        profile = await self.router.select(envelope, envelope.text)
        text = self.clean_user_text(envelope.text)
        if not text:
            return None
        answer = await self.agent.reply(envelope=envelope, profile=profile, user_text=text)
        return answer, profile.id

    async def create_topic(
        self,
        *,
        bot: Bot,
        chat: Chat,
        user_id: int,
        name: str,
        agent_id: str | None = None,
        icon_custom_emoji_id: str | None = None,
    ):
        if not self.settings.telegram_allow_topic_creation:
            raise PermissionError("Topic creation is disabled")
        if not self.is_admin_user(user_id):
            raise PermissionError("Only configured Telegram admins may manage topics")
        if chat.type == "private" and not self.settings.telegram_enable_private_topics:
            raise PermissionError("Private-chat topics are disabled")
        topic = await bot.create_forum_topic(
            chat_id=chat.id,
            name=name[:128],
            icon_custom_emoji_id=icon_custom_emoji_id,
        )
        await self.telegram_store.upsert_topic(
            chat_id=chat.id,
            thread_id=topic.message_thread_id,
            name=topic.name,
            icon_custom_emoji_id=topic.icon_custom_emoji_id,
            created_by=user_id,
        )
        if agent_id:
            if agent_id not in DEFAULT_AGENT_PROFILES:
                raise ValueError(f"Unknown agent: {agent_id}")
            await self.telegram_store.bind_topic(chat.id, topic.message_thread_id, agent_id)
        return topic

    async def sync_topic_service_message(self, message: Message) -> None:
        if message.message_thread_id is None:
            return
        if message.forum_topic_created:
            await self.telegram_store.upsert_topic(
                chat_id=message.chat_id,
                thread_id=message.message_thread_id,
                name=message.forum_topic_created.name,
                icon_custom_emoji_id=message.forum_topic_created.icon_custom_emoji_id,
                created_by=message.from_user.id if message.from_user else None,
                status="open",
            )
        elif message.forum_topic_edited and message.forum_topic_edited.name:
            await self.telegram_store.rename_topic(
                message.chat_id,
                message.message_thread_id,
                message.forum_topic_edited.name,
            )
        elif message.forum_topic_closed:
            await self.telegram_store.set_topic_status(message.chat_id, message.message_thread_id, "closed")
        elif message.forum_topic_reopened:
            await self.telegram_store.set_topic_status(message.chat_id, message.message_thread_id, "open")

    async def set_chat_active(self, chat: Chat, active: bool) -> None:
        await self.telegram_store.upsert_chat(
            chat_id=chat.id,
            chat_type=str(chat.type),
            title=getattr(chat, "title", None) or getattr(chat, "full_name", None) or "",
            username=chat.username or "",
            is_forum=bool(chat.is_forum),
            active=active,
        )
