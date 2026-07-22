from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.drive_service import DriveService, _item_payload, _safe_name


def test_safe_name_removes_client_paths() -> None:
    assert _safe_name("../../contracts/home.pdf") == "home.pdf"
    assert _safe_name(r"C:\fakepath\photo.jpg") == "photo.jpg"


def test_safe_name_rejects_empty_values() -> None:
    with pytest.raises(ValueError):
        _safe_name("")
    with pytest.raises(ValueError):
        _safe_name("..")


def test_item_payload_normalizes_drive_folder() -> None:
    item = _item_payload({
        "id": "folder-1",
        "name": "מסמכים",
        "mimeType": "application/vnd.google-apps.folder",
        "parents": ["root-1"],
    })

    assert item["is_folder"] is True
    assert item["size"] is None
    assert item["web_view_link"].endswith("/folder-1")
    assert item["parents"] == ["root-1"]


def test_drive_shared_email_configuration_is_normalized() -> None:
    settings = Settings(
        _env_file=None,
        GOOGLE_DRIVE_SHARED_EMAILS=" One@example.com, two@example.com,one@example.com ",
    )

    assert settings.google_drive_shared_emails == ["one@example.com", "two@example.com"]


@pytest.mark.asyncio
async def test_drive_status_is_fail_closed_without_google_credentials() -> None:
    settings = Settings(_env_file=None, GOOGLE_DRIVE_SHARED_EMAILS="member@example.com")
    store = AsyncMock()
    store.get_setting.return_value = None
    service = DriveService(settings, store)

    status = await service.status()

    assert status == {
        "connected": False,
        "detail": "Google Drive is not connected",
        "root": None,
        "shared_emails": ["member@example.com"],
    }
