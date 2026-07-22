import asyncio
import json
import time
from pathlib import Path

import pytest

from app.config import Settings
from app.services import HomeService
from app.smart_inbox_service import (
    InboxConflictError,
    InboxNeedsReviewError,
    InboxPermissionError,
    SmartInboxService,
    classify_action,
)
from app.store_v2 import Store


async def build_inbox(tmp_path: Path) -> tuple[Store, SmartInboxService]:
    settings = Settings(
        TELEGRAM_BOT_TOKEN="test-token",
        OPENAI_API_KEY="test-key",
        ALLOWED_USER_IDS="1,2",
        TELEGRAM_ADMIN_USER_IDS="1",
        APP_SESSION_SECRET="test-secret",
        DATABASE_PATH=str(tmp_path / "home.db"),
    )
    store = Store(tmp_path / "home.db", settings.household_id)
    await store.connect()
    await store.bootstrap_household("Test Home", "Asia/Jerusalem", [1, 2])
    inbox = SmartInboxService(settings, store, HomeService(store))
    await inbox.ensure_schema()
    return store, inbox


def test_policy_is_fail_safe() -> None:
    assert not classify_action("todo_list", {}).mutating
    simple = classify_action("todo_add", {"title": "להוציא זבל"})
    assert simple.mutating and not simple.requires_approval and simple.risk_level == "low"

    structured = classify_action(
        "todo_add",
        {"title": "להתקשר לרופא", "due_at": "2026-07-24T09:00:00+03:00"},
    )
    assert structured.requires_approval and structured.risk_level == "medium"

    unknown = classify_action("future_new_mutation", {"value": 1})
    assert unknown.mutating and unknown.requires_approval


@pytest.mark.asyncio
async def test_source_idempotency_and_auto_execution(tmp_path: Path) -> None:
    store, inbox = await build_inbox(tmp_path)
    calls: list[str] = []

    async def executor(name: str, arguments: dict, actor_id: int) -> str:
        calls.append(name)
        return json.dumps({"ok": True, "item": arguments.get("item")})

    kwargs = dict(
        source_kind="telegram",
        source_key="100:50:900",
        created_by=2,
        source_text="תוסיף חלב",
        agent_id="shopping",
        actions=[{"name": "shop_add", "arguments": {"item": "חלב", "qty": "1"}}],
    )
    first = await inbox.create_proposal(**kwargs)
    duplicate = await inbox.create_proposal(**kwargs)
    assert duplicate["id"] == first["id"]

    completed = await inbox.approve(
        first["id"], 2, expected_version=first["version"], auto=True, executor=executor
    )
    assert completed["status"] == "completed"
    assert calls == ["shop_add"]
    await store.close()


@pytest.mark.asyncio
async def test_multistep_plan_is_atomic_and_single_claim(tmp_path: Path) -> None:
    store, inbox = await build_inbox(tmp_path)
    calls: list[str] = []

    async def executor(name: str, arguments: dict, actor_id: int) -> str:
        await asyncio.sleep(0.01)
        calls.append(str(arguments["item"]))
        return json.dumps({"ok": True})

    proposal = await inbox.create_proposal(
        source_kind="telegram",
        source_key="multi",
        created_by=2,
        source_text="תוסיף חלב ולחם",
        agent_id="shopping",
        actions=[
            {"name": "shop_add", "arguments": {"item": "חלב"}},
            {"name": "shop_add", "arguments": {"item": "לחם"}},
        ],
    )
    assert proposal["status"] == "pending"
    assert all(step["requires_approval"] for step in proposal["steps"])

    results = await asyncio.gather(
        inbox.approve(
            proposal["id"], 2, expected_version=proposal["version"], executor=executor
        ),
        inbox.approve(
            proposal["id"], 2, expected_version=proposal["version"], executor=executor
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, InboxConflictError) for result in results) == 1
    completed = next(result for result in results if isinstance(result, dict))
    assert completed["status"] == "completed"
    assert sorted(calls) == ["חלב", "לחם"]
    await store.close()


@pytest.mark.asyncio
async def test_retry_skips_completed_steps(tmp_path: Path) -> None:
    store, inbox = await build_inbox(tmp_path)
    attempts: dict[str, int] = {"חלב": 0, "לחם": 0}

    async def executor(name: str, arguments: dict, actor_id: int) -> str:
        item = str(arguments["item"])
        attempts[item] += 1
        if item == "לחם" and attempts[item] == 1:
            return json.dumps({"ok": False, "error": "temporary"})
        return json.dumps({"ok": True})

    proposal = await inbox.create_proposal(
        source_kind="api",
        source_key="retry",
        created_by=2,
        source_text="חלב ולחם",
        agent_id="shopping",
        actions=[
            {"name": "shop_add", "arguments": {"item": "חלב"}},
            {"name": "shop_add", "arguments": {"item": "לחם"}},
        ],
    )
    failed = await inbox.approve(
        proposal["id"], 2, expected_version=proposal["version"], executor=executor
    )
    assert failed["status"] == "failed"
    assert [step["status"] for step in failed["steps"]] == ["completed", "failed"]

    completed = await inbox.retry(
        proposal["id"], 2, expected_version=failed["version"], executor=executor
    )
    assert completed["status"] == "completed"
    assert attempts == {"חלב": 1, "לחם": 2}
    await store.close()


@pytest.mark.asyncio
async def test_external_exception_requires_manual_review(tmp_path: Path) -> None:
    store, inbox = await build_inbox(tmp_path)

    async def executor(name: str, arguments: dict, actor_id: int) -> str:
        raise TimeoutError("calendar timed out after request was sent")

    proposal = await inbox.create_proposal(
        source_kind="telegram",
        source_key="external",
        created_by=2,
        source_text="תיצור אירוע",
        agent_id="calendar",
        actions=[
            {
                "name": "event_add",
                "arguments": {
                    "title": "רופא",
                    "start_at": "2026-07-24T09:00:00+03:00",
                    "end_at": "2026-07-24T10:00:00+03:00",
                },
            }
        ],
    )
    with pytest.raises(InboxNeedsReviewError):
        await inbox.approve(
            proposal["id"], 2, expected_version=proposal["version"], executor=executor
        )
    current = await inbox.get(proposal["id"], 2)
    assert current["status"] == "needs_review"
    assert current["steps"][0]["status"] == "uncertain"
    with pytest.raises(InboxNeedsReviewError):
        await inbox.retry(
            proposal["id"], 2, expected_version=current["version"], executor=executor
        )
    await store.close()


@pytest.mark.asyncio
async def test_non_owner_cannot_access_unless_admin(tmp_path: Path) -> None:
    store, inbox = await build_inbox(tmp_path)
    proposal = await inbox.create_proposal(
        source_kind="telegram",
        source_key="private",
        created_by=2,
        source_text="משימה",
        agent_id="tasks",
        actions=[{"name": "todo_add", "arguments": {"title": "משימה"}}],
    )
    # User 1 is configured as admin.
    assert (await inbox.get(proposal["id"], 1))["id"] == proposal["id"]

    inbox.settings.telegram_admin_user_ids = []
    inbox.settings.allowed_user_ids = [2]
    with pytest.raises(InboxPermissionError):
        await inbox.get(proposal["id"], 999)
    await store.close()


@pytest.mark.asyncio
async def test_stale_external_execution_is_not_retried(tmp_path: Path) -> None:
    store, inbox = await build_inbox(tmp_path)
    proposal = await inbox.create_proposal(
        source_kind="telegram",
        source_key="stale",
        created_by=2,
        source_text="אירוע",
        agent_id="calendar",
        actions=[
            {
                "name": "event_add",
                "arguments": {
                    "title": "בדיקה",
                    "start_at": "2026-07-24T09:00:00+03:00",
                    "end_at": "2026-07-24T10:00:00+03:00",
                },
            }
        ],
    )
    old = time.time() - 1000
    await store.db.execute(
        "UPDATE action_inbox_proposals SET status='executing', executing_at=?, updated_at=? WHERE id=?",
        (old, old, proposal["id"]),
    )
    await store.db.execute(
        "UPDATE action_inbox_steps SET status='executing', started_at=? WHERE proposal_id=?",
        (old, proposal["id"]),
    )
    await store.db.commit()

    assert await inbox.recover_stale(stale_after_seconds=600) == 1
    current = await inbox.get(proposal["id"], 2)
    assert current["status"] == "needs_review"
    await store.close()


@pytest.mark.asyncio
async def test_legacy_pending_rows_are_migrated(tmp_path: Path) -> None:
    settings = Settings(
        TELEGRAM_BOT_TOKEN="test-token",
        OPENAI_API_KEY="test-key",
        ALLOWED_USER_IDS="1",
        APP_SESSION_SECRET="test-secret",
    )
    store = Store(tmp_path / "home.db", settings.household_id)
    await store.connect()
    await store.bootstrap_household("Test Home", "Asia/Jerusalem", [1])
    now = time.time()
    await store.db.executescript(
        """
        CREATE TABLE smart_inbox_actions (
            id TEXT PRIMARY KEY, chat_id INTEGER, thread_id INTEGER,
            scope_key TEXT, user_id INTEGER, agent_id TEXT, source_text TEXT,
            actions_json TEXT, summary TEXT, status TEXT, result_json TEXT,
            created_at REAL, updated_at REAL, expires_at REAL
        );
        """
    )
    await store.db.execute(
        """INSERT INTO smart_inbox_actions VALUES(
            'legacy-1', 10, NULL, 'telegram:10:0', 1, 'tasks', 'צור משימה',
            ?, 'הצעה ישנה', 'pending', '', ?, ?, ?
        )""",
        (
            json.dumps([{"name": "todo_add", "arguments": {"title": "ישן"}}]),
            now,
            now,
            now + 3600,
        ),
    )
    await store.db.commit()

    inbox = SmartInboxService(settings, store, HomeService(store))
    await inbox.ensure_schema()
    migrated = await inbox.get("legacy-1", 1)
    assert migrated["source_kind"] == "legacy"
    assert migrated["steps"][0]["tool_name"] == "todo_add"
    await store.close()
