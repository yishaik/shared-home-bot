from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.member_service import MemberService
from app.store_v2 import Store
from app.work_service import WorkService


def settings():
    return SimpleNamespace(
        google_calendar_id="primary",
        google_enabled=False,
        google_docs_folder_id="",
        household_id="primary",
        household_timezone="Asia/Jerusalem",
    )


@pytest.mark.asyncio
async def test_active_members_only_after_interaction(tmp_path: Path) -> None:
    store = Store(tmp_path / "home.db")
    await store.connect()
    await store.bootstrap_household("Home", "Asia/Jerusalem", [11, 22])
    members = MemberService(store)

    assert await members.list_active() == []
    await members.touch(11, "ישי", "yishai", private_chat_id=101, started=True)
    active = await members.list_active()

    assert len(active) == 1
    assert active[0]["display_name"] == "ישי"
    assert active[0]["username"] == "yishai"
    assert active[0]["can_receive_dm"] == 1
    assert "private_chat_id" not in active[0]
    await store.close()


@pytest.mark.asyncio
async def test_projects_tasks_dependencies_and_resources(tmp_path: Path) -> None:
    store = Store(tmp_path / "home.db")
    await store.connect()
    await store.bootstrap_household("Home", "Asia/Jerusalem", [1])
    work = WorkService(settings(), store)

    project = await work.create_project(1, name="מעבר דירה", status="active")
    first = await work.create_task(1, title="לקבל הצעות מחיר", project_id=project["id"], priority="high")
    second = await work.create_task(1, title="לבחור מוביל", project_id=project["id"])

    await work.add_relationship(
        1,
        source_task_id=first["id"],
        target_task_id=second["id"],
        relationship_type="blocks",
    )
    blocked = await work.get_task(second["id"])
    assert blocked and blocked["blocked"] is True
    assert blocked["blockers"][0]["title"] == "לקבל הצעות מחיר"

    with pytest.raises(ValueError, match="cycle"):
        await work.add_relationship(
            1,
            source_task_id=second["id"],
            target_task_id=first["id"],
            relationship_type="blocks",
        )

    resource = await work.add_resource(
        1,
        first["id"],
        file_name="הצעת מחיר",
        web_url="https://drive.google.com/file/d/example/view",
    )
    assert resource["task_id"] == first["id"]

    completed = await work.update_task(1, first["id"], status="completed")
    assert completed and completed["status"] == "completed"
    unblocked = await work.get_task(second["id"])
    assert unblocked and unblocked["blocked"] is False

    projects = await work.list_projects()
    assert projects[0]["task_count"] == 2
    assert projects[0]["completed_count"] == 1
    assert projects[0]["progress"] == 50
    await store.close()
