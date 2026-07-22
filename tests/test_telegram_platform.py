import pytest

from app.agent_profiles import AgentRouter
from app.telegram_models import TelegramEnvelope


class FakeTelegramStore:
    def __init__(self, binding=None):
        self.binding = binding

    async def topic_agent(self, chat_id, thread_id):
        return self.binding


@pytest.mark.asyncio
async def test_router_prefers_topic_binding() -> None:
    router = AgentRouter(FakeTelegramStore("calendar"))
    envelope = TelegramEnvelope(
        update_id=1,
        chat_id=-100,
        chat_type="supergroup",
        user_id=10,
        username="u",
        display_name="User",
        message_id=20,
        text="לקנות חלב",
        thread_id=7,
    )

    profile = await router.select(envelope, envelope.text)

    assert profile.id == "calendar"


@pytest.mark.asyncio
async def test_router_uses_domain_heuristics_without_binding() -> None:
    router = AgentRouter(FakeTelegramStore())
    envelope = TelegramEnvelope(
        update_id=1,
        chat_id=10,
        chat_type="private",
        user_id=10,
        username=None,
        display_name="User",
        message_id=20,
        text="תוסיף חלב לרשימת הקניות",
    )

    profile = await router.select(envelope, envelope.text)

    assert profile.id == "shopping"


def test_envelope_scope_is_topic_isolated() -> None:
    envelope = TelegramEnvelope(
        update_id=1,
        chat_id=-100,
        chat_type="supergroup",
        user_id=10,
        username=None,
        display_name="User",
        message_id=20,
        text="hello",
        thread_id=99,
    )

    assert envelope.scope_key == "telegram:-100:99"


def test_direct_message_topic_is_used_when_thread_id_is_absent() -> None:
    envelope = TelegramEnvelope(
        update_id=1,
        chat_id=42,
        chat_type="private",
        user_id=10,
        username=None,
        display_name="User",
        message_id=20,
        text="hello",
        direct_messages_topic_id=77,
    )

    assert envelope.topic_id == 77
    assert envelope.scope_key == "telegram:42:77"
