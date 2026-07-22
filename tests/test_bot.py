from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.constants import ReactionEmoji
from telegram.error import BadRequest

from app.bot import BUTTON_ACTIONS, MENU_ACTIONS, _reply_keyboard, _set_reaction_safely


def test_reply_keyboard_labels_are_all_routable() -> None:
    settings = SimpleNamespace(resolved_mini_app_url="https://example/app")
    keyboard = _reply_keyboard(settings)
    labels = [button.text for row in keyboard.keyboard for button in row]
    # Every tappable text label (all but the direct web_app button) has a handler.
    text_labels = [label for label in labels if label != "🏠 אפליקציה"]
    assert set(text_labels) == set(BUTTON_ACTIONS)
    assert all(callable(handler) for handler in BUTTON_ACTIONS.values())
    assert all(callable(handler) for handler in MENU_ACTIONS.values())


def test_reply_keyboard_hides_app_button_without_url() -> None:
    keyboard = _reply_keyboard(SimpleNamespace(resolved_mini_app_url=""))
    labels = [button.text for row in keyboard.keyboard for button in row]
    assert "🏠 אפליקציה" not in labels


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
