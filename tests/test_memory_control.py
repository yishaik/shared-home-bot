from pathlib import Path

import pytest

from app.memory_control import (
    auto_memory_enabled,
    ensure_memory_control_schema,
    list_memory_audit,
    record_memory_audit,
    set_auto_memory_enabled,
)
from app.store_v2 import Store


@pytest.mark.asyncio
async def test_memory_control_toggle_and_audit(tmp_path: Path) -> None:
    store = Store(tmp_path / "home.db")
    await store.connect()
    await ensure_memory_control_schema(store)

    assert await auto_memory_enabled(store) is True
    await set_auto_memory_enabled(store, False, actor_id=123)
    assert await auto_memory_enabled(store) is False

    rows = await list_memory_audit(store)
    assert rows[0]["action"] == "auto_memory_disabled"
    assert rows[0]["actor_id"] == 123
    await store.close()


@pytest.mark.asyncio
async def test_memory_audit_preserves_deleted_value(tmp_path: Path) -> None:
    store = Store(tmp_path / "home.db")
    await store.connect()
    await ensure_memory_control_schema(store)
    await record_memory_audit(
        store,
        action="deleted",
        memory_key="wifi",
        old_value="secret value",
        source="manual",
        metadata={"reason": "user request"},
    )

    row = (await list_memory_audit(store))[0]
    assert row["memory_key"] == "wifi"
    assert row["old_value"] == "secret value"
    assert row["metadata"]["reason"] == "user request"
    await store.close()
