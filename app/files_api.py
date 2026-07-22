from __future__ import annotations

from typing import Annotated, Any, Awaitable, Callable

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.drive_service import DriveBoundaryError, DriveService, DriveUnavailableError


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    parent_id: str | None = None


def build_files_router(
    drive: DriveService,
    actor_dependency: Callable[..., Awaitable[Any]],
) -> APIRouter:
    router = APIRouter(prefix="/api/files", tags=["files"])

    def translate_error(exc: Exception) -> HTTPException:
        if isinstance(exc, FileNotFoundError):
            return HTTPException(status_code=404, detail="File or folder not found")
        if isinstance(exc, (ValueError, DriveBoundaryError)):
            return HTTPException(status_code=400, detail=str(exc))
        if isinstance(exc, DriveUnavailableError):
            return HTTPException(status_code=503, detail=str(exc))
        return HTTPException(status_code=502, detail="Google Drive request failed")

    @router.get("/status")
    async def files_status(_: Annotated[Any, actor_dependency]) -> dict[str, Any]:
        return await drive.status()

    @router.get("")
    async def list_files(
        _: Annotated[Any, actor_dependency],
        folder_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            return await drive.list_items(folder_id)
        except Exception as exc:
            raise translate_error(exc) from exc

    @router.post("/folders", status_code=status.HTTP_201_CREATED)
    async def create_folder(
        body: FolderCreate,
        _: Annotated[Any, actor_dependency],
    ) -> dict[str, Any]:
        try:
            return await drive.create_folder(body.name, body.parent_id)
        except Exception as exc:
            raise translate_error(exc) from exc

    @router.post("/upload", status_code=status.HTTP_201_CREATED)
    async def upload_file(
        _: Annotated[Any, actor_dependency],
        upload: UploadFile = File(...),
        folder_id: str | None = Form(default=None),
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
        _: Annotated[Any, actor_dependency],
    ) -> None:
        try:
            await drive.delete(file_id)
        except Exception as exc:
            raise translate_error(exc) from exc

    return router
