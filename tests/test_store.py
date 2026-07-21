from pathlib import Path

import pytest

from app.store_v2 import Store
from app.services import HomeService


@pytest.mark.asyncio
async def test_household_crud(tmp_path: Path) -> None:
    store = Store(tmp_path / "home.db")
    await store.connect()
    await store.bootstrap_household("Our Home", "Asia/Jerusalem", [1, 2])
    service = HomeService(store)

    task = await service.add_todo(1, "Book plumber", priority="high")
    shopping = await service.add_shopping(2, "Milk", "2")

    assert task["priority"] == "high"
    assert shopping["item"] == "Milk"
    assert await store.is_member(1)
    assert len(await store.list_activity()) == 2
    await store.close()
