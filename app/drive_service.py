from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path
from typing import Any

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

from app.config import Settings
from app.google_client import SCOPES, TOKEN_URI
from app.store_v2 import Store

log = logging.getLogger("homebot.drive")

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
ROOT_SETTING_KEY = "google_drive_folder_id"


class DriveUnavailableError(RuntimeError):
    """Google Drive credentials or the managed root folder are unavailable."""


class DriveBoundaryError(RuntimeError):
    """A requested Drive item is outside the household-managed root folder."""


def _safe_name(value: str) -> str:
    normalized = (value or "").replace("\\", "/").strip()
    name = Path(normalized).name.strip()
    if not name or name in {".", ".."}:
        raise ValueError("A valid file or folder name is required")
    return name[:240]


def _item_payload(item: dict[str, Any]) -> dict[str, Any]:
    item_id = str(item.get("id") or "")
    mime_type = str(item.get("mimeType") or "application/octet-stream")
    is_folder = mime_type == FOLDER_MIME_TYPE
    fallback_url = (
        f"https://drive.google.com/drive/folders/{item_id}"
        if is_folder
        else f"https://drive.google.com/open?id={item_id}"
    )
    size = item.get("size")
    return {
        "id": item_id,
        "name": str(item.get("name") or ""),
        "mime_type": mime_type,
        "is_folder": is_folder,
        "size": int(size) if size not in (None, "") else None,
        "created_time": item.get("createdTime"),
        "modified_time": item.get("modifiedTime"),
        "web_view_link": item.get("webViewLink") or fallback_url,
        "web_content_link": item.get("webContentLink"),
        "thumbnail_link": item.get("thumbnailLink"),
        "parents": list(item.get("parents") or []),
    }


class DriveService:
    """Household-scoped Google Drive gateway.

    The Google account is the automation principal. Telegram household members
    use the Mini App, while Google users receive inherited access by sharing the
    managed root folder. Every API operation is boundary-checked against that
    root before it reaches Drive.
    """

    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        self._service: Any | None = None
        self._credentials_key: tuple[str, str, str] | None = None
        self._root_id: str | None = None
        self._known_descendants: set[str] = set()
        self._lock = asyncio.Lock()

    def reset_credentials(self) -> None:
        """Drop cached Google clients after OAuth credentials are replaced."""
        self._service = None
        self._credentials_key = None

    async def _effective_settings(self) -> Settings:
        if self.settings.google_refresh_token:
            return self.settings
        stored_refresh = await self.store.get_setting("google_refresh_token")
        if not stored_refresh:
            return self.settings
        return self.settings.model_copy(update={"google_refresh_token": stored_refresh})

    async def _drive(self):
        effective = await self._effective_settings()
        if not effective.google_enabled:
            raise DriveUnavailableError("Google Drive is not connected")
        key = (effective.google_client_id, effective.google_client_secret, effective.google_refresh_token)
        if self._service is not None and self._credentials_key == key:
            return self._service

        def build_service():
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            credentials = Credentials(
                token=None,
                refresh_token=effective.google_refresh_token,
                client_id=effective.google_client_id,
                client_secret=effective.google_client_secret,
                token_uri=TOKEN_URI,
                scopes=SCOPES,
            )
            return build("drive", "v3", credentials=credentials, cache_discovery=False)

        self._service = await asyncio.to_thread(build_service)
        self._credentials_key = key
        return self._service

    async def ensure_root(self) -> dict[str, Any]:
        async with self._lock:
            drive = await self._drive()
            root_id = (
                self._root_id
                or self.settings.google_drive_folder_id
                or self.settings.google_docs_folder_id
                or await self.store.get_setting(ROOT_SETTING_KEY)
            )

            if root_id:
                try:
                    item = await asyncio.to_thread(
                        lambda: drive.files().get(
                            fileId=root_id,
                            fields="id,name,mimeType,createdTime,modifiedTime,webViewLink,parents,trashed",
                            supportsAllDrives=True,
                        ).execute()
                    )
                    if item.get("trashed") or item.get("mimeType") != FOLDER_MIME_TYPE:
                        raise DriveUnavailableError("Configured Google Drive root is not an active folder")
                except HttpError as exc:
                    raise DriveUnavailableError("Configured Google Drive root cannot be accessed") from exc
            else:
                body = {
                    "name": self.settings.google_drive_folder_name,
                    "mimeType": FOLDER_MIME_TYPE,
                    "appProperties": {
                        "shared_home_bot": "true",
                        "household_id": self.settings.household_id,
                    },
                }
                item = await asyncio.to_thread(
                    lambda: drive.files().create(
                        body=body,
                        fields="id,name,mimeType,createdTime,modifiedTime,webViewLink,parents",
                    ).execute()
                )
                root_id = str(item["id"])
                await self.store.set_setting(ROOT_SETTING_KEY, root_id)
                log.info("created managed Google Drive root folder id=%s", root_id)

            self._root_id = str(root_id)
            self._known_descendants.add(self._root_id)
            # Keep legacy Google Docs/Sheets tools pointed at the same folder for
            # the lifetime of this process. The persisted DB setting survives restarts.
            self.settings.google_drive_folder_id = self._root_id
            if not self.settings.google_docs_folder_id:
                self.settings.google_docs_folder_id = self._root_id

            await self._sync_root_permissions(drive, self._root_id)
            return _item_payload(item)

    async def _sync_root_permissions(self, drive, root_id: str) -> None:
        emails = {email.lower() for email in self.settings.google_drive_shared_emails if email.strip()}
        if not emails:
            return
        try:
            response = await asyncio.to_thread(
                lambda: drive.permissions().list(
                    fileId=root_id,
                    fields="permissions(id,type,role,emailAddress)",
                    supportsAllDrives=True,
                ).execute()
            )
            existing = {
                str(permission.get("emailAddress") or "").lower()
                for permission in response.get("permissions", [])
            }
            for email in sorted(emails - existing):
                await asyncio.to_thread(
                    lambda email=email: drive.permissions().create(
                        fileId=root_id,
                        body={"type": "user", "role": "writer", "emailAddress": email},
                        sendNotificationEmail=True,
                        supportsAllDrives=True,
                        fields="id,emailAddress,role",
                    ).execute()
                )
                log.info("shared managed Drive root with %s", email)
        except HttpError:
            # File access remains usable even if an account/domain policy blocks sharing.
            log.exception("failed to synchronize Google Drive root permissions")

    async def status(self) -> dict[str, Any]:
        effective = await self._effective_settings()
        if not effective.google_enabled:
            return {
                "connected": False,
                "detail": "Google Drive is not connected",
                "root": None,
                "shared_emails": self.settings.google_drive_shared_emails,
            }
        try:
            root = await self.ensure_root()
            return {
                "connected": True,
                "detail": "Google Drive connected",
                "root": root,
                "shared_emails": self.settings.google_drive_shared_emails,
            }
        except DriveUnavailableError as exc:
            return {
                "connected": False,
                "detail": str(exc),
                "root": None,
                "shared_emails": self.settings.google_drive_shared_emails,
            }

    async def _metadata(self, file_id: str) -> dict[str, Any]:
        drive = await self._drive()
        try:
            return await asyncio.to_thread(
                lambda: drive.files().get(
                    fileId=file_id,
                    fields="id,name,mimeType,parents,trashed,createdTime,modifiedTime,webViewLink,webContentLink,thumbnailLink,size",
                    supportsAllDrives=True,
                ).execute()
            )
        except HttpError as exc:
            if getattr(exc.resp, "status", None) == 404:
                raise FileNotFoundError(file_id) from exc
            raise DriveUnavailableError("Google Drive request failed") from exc

    async def _assert_in_root(self, file_id: str, *, folder_required: bool = False) -> dict[str, Any]:
        root = await self.ensure_root()
        root_id = root["id"]
        if file_id == root_id:
            item = await self._metadata(file_id)
            if folder_required and item.get("mimeType") != FOLDER_MIME_TYPE:
                raise DriveBoundaryError("Target is not a folder")
            return item
        if file_id in self._known_descendants:
            item = await self._metadata(file_id)
            if folder_required and item.get("mimeType") != FOLDER_MIME_TYPE:
                raise DriveBoundaryError("Target is not a folder")
            return item

        current_id = file_id
        visited: set[str] = set()
        requested: dict[str, Any] | None = None
        for _ in range(50):
            if current_id in visited:
                break
            visited.add(current_id)
            item = await self._metadata(current_id)
            requested = requested or item
            if item.get("trashed"):
                raise FileNotFoundError(file_id)
            parents = list(item.get("parents") or [])
            if root_id in parents:
                self._known_descendants.update(visited)
                if folder_required and requested.get("mimeType") != FOLDER_MIME_TYPE:
                    raise DriveBoundaryError("Target is not a folder")
                return requested
            if not parents:
                break
            current_id = str(parents[0])

        raise DriveBoundaryError("Drive item is outside the household folder")

    async def list_items(self, folder_id: str | None = None) -> dict[str, Any]:
        root = await self.ensure_root()
        target_id = folder_id or root["id"]
        folder = await self._assert_in_root(target_id, folder_required=True)
        drive = await self._drive()
        query = f"'{target_id}' in parents and trashed=false"
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            response = await asyncio.to_thread(
                lambda page_token=page_token: drive.files().list(
                    q=query,
                    orderBy="folder,name_natural",
                    pageSize=200,
                    pageToken=page_token,
                    fields="nextPageToken,files(id,name,mimeType,size,createdTime,modifiedTime,webViewLink,webContentLink,thumbnailLink,parents)",
                    spaces="drive",
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True,
                ).execute()
            )
            page = response.get("files", [])
            items.extend(_item_payload(item) for item in page)
            self._known_descendants.update(str(item.get("id")) for item in page if item.get("id"))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return {"root": root, "folder": _item_payload(folder), "items": items}

    async def create_folder(self, name: str, parent_id: str | None = None) -> dict[str, Any]:
        root = await self.ensure_root()
        target_id = parent_id or root["id"]
        await self._assert_in_root(target_id, folder_required=True)
        drive = await self._drive()
        safe_name = _safe_name(name)
        item = await asyncio.to_thread(
            lambda: drive.files().create(
                body={"name": safe_name, "mimeType": FOLDER_MIME_TYPE, "parents": [target_id]},
                fields="id,name,mimeType,size,createdTime,modifiedTime,webViewLink,webContentLink,thumbnailLink,parents",
                supportsAllDrives=True,
            ).execute()
        )
        self._known_descendants.add(str(item["id"]))
        return _item_payload(item)

    async def upload(self, *, name: str, mime_type: str, content: bytes, parent_id: str | None = None) -> dict[str, Any]:
        root = await self.ensure_root()
        target_id = parent_id or root["id"]
        await self._assert_in_root(target_id, folder_required=True)
        drive = await self._drive()
        safe_name = _safe_name(name)
        media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mime_type or "application/octet-stream", resumable=False)
        item = await asyncio.to_thread(
            lambda: drive.files().create(
                body={"name": safe_name, "parents": [target_id]},
                media_body=media,
                fields="id,name,mimeType,size,createdTime,modifiedTime,webViewLink,webContentLink,thumbnailLink,parents",
                supportsAllDrives=True,
            ).execute()
        )
        self._known_descendants.add(str(item["id"]))
        return _item_payload(item)

    async def delete(self, file_id: str) -> dict[str, Any]:
        root = await self.ensure_root()
        if file_id == root["id"]:
            raise DriveBoundaryError("The household root folder cannot be deleted")
        item = await self._assert_in_root(file_id)
        drive = await self._drive()
        try:
            trashed = await asyncio.to_thread(
                lambda: drive.files().update(
                    fileId=file_id,
                    body={"trashed": True},
                    fields="id,name,mimeType,size,createdTime,modifiedTime,webViewLink,webContentLink,thumbnailLink,parents,trashed",
                    supportsAllDrives=True,
                ).execute()
            )
        except HttpError as exc:
            if getattr(exc.resp, "status", None) == 404:
                raise FileNotFoundError(file_id) from exc
            raise DriveUnavailableError("Google Drive trash operation failed") from exc
        self._known_descendants.discard(file_id)
        return _item_payload(trashed or item)
