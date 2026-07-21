from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.constants import ReactionEmoji
from telegram.error import BadRequest

from app.bot import _set_reaction_safely


@pytest.mark.asyncio
async def test_set_reaction_safely() -> None:
    message = SimpleNamespace(chat_id=10, message_id=20, set_reaction=AsyncMock())

    await _set_reaction_safely(message, ReactionEmoji.EYES)

    message.set_reaction.assert_awaited_once_with(reaction=ReactionEmoji.EYES, is_big=False)


@pytest.mark.asyncio
async def test_set_reaction_safely_ignores_telegram_errors() -> None:
    message = SimpleNamespace(
        chat_id=10,
        message_id=20,
        set_reaction=AsyncMock(side_effect=BadRequest("reaction unavailable")),
    )

    await _set_reaction_safely(message, ReactionEmoji.THUMBS_UP, is_big=True)

    message.set_reaction.assert_awaited_once_with(reaction=ReactionEmoji.THUMBS_UP, is_big=True)
