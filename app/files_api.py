from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.config import Settings
from app.drive_service import DriveBoundaryError, DriveService, DriveUnavailableError
from app.security import AuthenticationError, SessionSigner
from app.store_v2 import Store


@dataclass(frozen=True)
class FileActor:
    user_id: int
    household_id: str
    display_name: str


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    parent_id: str | None = None


def build_files_router(settings: Settings, store: Store, drive: DriveService) -> APIRouter:
    router = APIRouter(prefix="/api/files", tags=["files"])
    signer = SessionSigner(settings.effective_session_secret, settings.session_ttl_seconds)

    async def current_actor(authorization: Annotated[str | None, Header()] = None) -> FileActor:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing session")
        try:
            payload = signer.verify(authorization.removeprefix("Bearer ").strip())
            actor = FileActor(
                user_id=int(payload["sub"]),
                household_id=str(payload["household_id"]),
                display_name=str(payload.get("name") or ""),
            )
        except (AuthenticationError, KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session") from exc
        if actor.household_id != settings.household_id or not await store.is_member(actor.user_id, actor.household_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a household member")
        return actor

    def translate_error(exc: Exception) -> HTTPException:
        if isinstance(exc, FileNotFoundError):
            return HTTPException(status_code=404, detail="File or folder not found")
        if isinstance(exc, (ValueError, DriveBoundaryError)):
            return HTTPException(status_code=400, detail=str(exc))
        if isinstance(exc, DriveUnavailableError):
            return HTTPException(status_code=503, detail=str(exc))
        return HTTPException(status_code=502, detail="Google Drive request failed")

    @router.get("/status")
    async def files_status(_: FileActor = Depends(current_actor)) -> dict[str, Any]:
        return await drive.status()

    @router.get("")
    async def list_files(
        folder_id: str | None = None,
        _: FileActor = Depends(current_actor),
    ) -> dict[str, Any]:
        try:
            return await drive.list_items(folder_id)
        except Exception as exc:
            raise translate_error(exc) from exc

    @router.post("/folders", status_code=status.HTTP_201_CREATED)
    async def create_folder(
        body: FolderCreate,
        _: FileActor = Depends(current_actor),
    ) -> dict[str, Any]:
        try:
            return await drive.create_folder(body.name, body.parent_id)
        except Exception as exc:
            raise translate_error(exc) from exc

    @router.post("/upload", status_code=status.HTTP_201_CREATED)
    async def upload_file(
        upload: UploadFile = File(...),
        folder_id: str | None = Form(default=None),
        _: FileActor = Depends(current_actor),
    ) -> dict[str, Any]:
        max_bytes = drive.settings.google_drive_max_upload_mb * 1024 * 1024
        content = await upload.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds the {drive.settings.google_drive_max_upload_mb} MB upload limit",
            )
        try:
            return await drive.upload(
                name=upload.filename or "upload",
                mime_type=upload.content_type or "application/octet-stream",
                content=content,
                parent_id=folder_id,
            )
        except Exception as exc:
            raise translate_error(exc) from exc
        finally:
            await upload.close()

    @router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_file(
        file_id: str,
        _: FileActor = Depends(current_actor),
    ) -> None:
        try:
            await drive.delete(file_id)
        except Exception as exc:
            raise translate_error(exc) from exc

    return router
