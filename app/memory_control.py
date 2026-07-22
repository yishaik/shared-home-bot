from __future__ import annotations

import json
import time
from typing import Any

MEMORY_CONTROL_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id TEXT NOT NULL,
    action TEXT NOT NULL,
    memory_key TEXT NOT NULL DEFAULT '',
    old_value TEXT NOT NULL DEFAULT '',
    new_value TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',
    actor_id INTEGER,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_audit_created
    ON memory_audit(household_id, created_at DESC);
"""

AUTO_MEMORY_SETTING = "auto_memory_enabled"
REFLECTION_LAST_STATUS = "reflection_last_status"
REFLECTION_LAST_AT = "reflection_last_at"
REFLECTION_LAST_ERROR = "reflection_last_error"


async def ensure_memory_control_schema(store) -> None:
    await store.db.executescript(MEMORY_CONTROL_SCHEMA)
    await store.db.commit()


async def auto_memory_enabled(store) -> bool:
    return (await store.get_setting(AUTO_MEMORY_SETTING, "true")).strip().lower() not in {"0", "false", "off", "no"}


async def set_auto_memory_enabled(store, enabled: bool, actor_id: int | None = None) -> None:
    await store.set_setting(AUTO_MEMORY_SETTING, "true" if enabled else "false")
    await record_memory_audit(
        store,
        action="auto_memory_enabled" if enabled else "auto_memory_disabled",
        source="settings",
        actor_id=actor_id,
    )


async def record_memory_audit(
    store,
    *,
    action: str,
    memory_key: str = "",
    old_value: str = "",
    new_value: str = "",
    source: str = "manual",
    actor_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    cur = await store.db.execute(
        """INSERT INTO memory_audit
           (household_id, action, memory_key, old_value, new_value, source, actor_id, metadata, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            store.household_id,
            action,
            memory_key,
            old_value,
            new_value,
            source,
            actor_id,
            json.dumps(metadata or {}, ensure_ascii=False),
            time.time(),
        ),
    )
    await store.db.commit()
    return int(cur.lastrowid or 0)


async def list_memory_audit(store, limit: int = 40) -> list[dict[str, Any]]:
    rows = await (
        await store.db.execute(
            """SELECT id, action, memory_key, old_value, new_value, source, actor_id, metadata, created_at
               FROM memory_audit WHERE household_id=? ORDER BY id DESC LIMIT ?""",
            (store.household_id, limit),
        )
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.get("metadata") or "{}")
        except json.JSONDecodeError:
            item["metadata"] = {}
        result.append(item)
    return result


async def reflection_status(store) -> dict[str, Any]:
    return {
        "auto_memory_enabled": await auto_memory_enabled(store),
        "last_status": await store.get_setting(REFLECTION_LAST_STATUS, "never"),
        "last_at": await store.get_setting(REFLECTION_LAST_AT, ""),
        "last_error": await store.get_setting(REFLECTION_LAST_ERROR, ""),
    }


async def mark_reflection_started(store) -> None:
    await store.set_setting(REFLECTION_LAST_STATUS, "running")
    await store.set_setting(REFLECTION_LAST_ERROR, "")


async def mark_reflection_finished(store) -> None:
    await store.set_setting(REFLECTION_LAST_STATUS, "success")
    await store.set_setting(REFLECTION_LAST_AT, str(time.time()))
    await store.set_setting(REFLECTION_LAST_ERROR, "")


async def mark_reflection_failed(store, error: str) -> None:
    await store.set_setting(REFLECTION_LAST_STATUS, "failed")
    await store.set_setting(REFLECTION_LAST_AT, str(time.time()))
    await store.set_setting(REFLECTION_LAST_ERROR, error[:500])
