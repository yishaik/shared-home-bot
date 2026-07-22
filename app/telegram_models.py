from __future__ import annotations

from dataclasses import dataclass

from telegram import Update


@dataclass(frozen=True, slots=True)
class TelegramEnvelope:
    update_id: int
    chat_id: int
    chat_type: str
    user_id: int
    username: str | None
    display_name: str
    message_id: int
    text: str
    thread_id: int | None = None
    direct_messages_topic_id: int | None = None
    business_connection_id: str | None = None
    sender_chat_id: int | None = None
    is_topic: bool = False
    bot_mentioned: bool = False
    reply_to_bot: bool = False

    @property
    def is_private(self) -> bool:
        return self.chat_type == "private"

    @property
    def is_group(self) -> bool:
        return self.chat_type in {"group", "supergroup"}

    @property
    def topic_id(self) -> int | None:
        return self.thread_id or self.direct_messages_topic_id

    @property
    def scope_key(self) -> str:
        return f"telegram:{self.chat_id}:{self.topic_id or 0}"

    @classmethod
    def from_update(cls, update: Update, *, bot_username: str = "") -> "TelegramEnvelope | None":
        message = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        if not message or not chat or not user:
            return None

        text = (message.text or message.caption or "").strip()
        normalized_bot_username = bot_username.lstrip("@").lower()
        mention = bool(normalized_bot_username and f"@{normalized_bot_username}" in text.lower())
        reply_to_bot = bool(
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.is_bot
        )
        direct_topic = getattr(message, "direct_messages_topic", None)

        return cls(
            update_id=update.update_id,
            chat_id=chat.id,
            chat_type=str(chat.type),
            user_id=user.id,
            username=user.username,
            display_name=user.full_name,
            message_id=message.message_id,
            text=text,
            thread_id=message.message_thread_id,
            direct_messages_topic_id=getattr(direct_topic, "topic_id", None),
            business_connection_id=message.business_connection_id,
            sender_chat_id=message.sender_chat.id if message.sender_chat else None,
            is_topic=bool(message.is_topic_message),
            bot_mentioned=mention,
            reply_to_bot=reply_to_bot,
        )
