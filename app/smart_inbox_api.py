from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.config import Settings
from app.security import AuthenticationError, SessionSigner
from app.smart_inbox_service import (
    InboxConflictError,
    InboxNeedsReviewError,
    InboxNotFoundError,
    InboxPermissionError,
    SmartInboxService,
)
from app.store_v2 import Store


@dataclass(frozen=True, slots=True)
class InboxActor:
    user_id: int
    household_id: str
    display_name: str


class InboxMutationRequest(BaseModel):
    expected_version: int | None = Field(default=None, ge=1)


def build_smart_inbox_router(
    settings: Settings,
    store: Store,
    inbox: SmartInboxService,
) -> APIRouter:
    router = APIRouter(prefix="/api/inbox", tags=["smart-inbox"])
    signer = SessionSigner(
        settings.effective_session_secret, settings.session_ttl_seconds
    )

    async def current_actor(
        authorization: Annotated[str | None, Header()] = None,
    ) -> InboxActor:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing session"
            )
        try:
            payload = signer.verify(authorization.removeprefix("Bearer ").strip())
            actor = InboxActor(
                user_id=int(payload["sub"]),
                household_id=str(payload["household_id"]),
                display_name=str(payload.get("name") or ""),
            )
        except (AuthenticationError, KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session"
            ) from exc
        if (
            actor.household_id != settings.household_id
            or not await store.is_member(actor.user_id, actor.household_id)
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not a household member",
            )
        return actor

    def translate_error(exc: Exception) -> HTTPException:
        if isinstance(exc, InboxNotFoundError):
            return HTTPException(status_code=404, detail=str(exc))
        if isinstance(exc, InboxPermissionError):
            return HTTPException(status_code=403, detail=str(exc))
        if isinstance(exc, InboxNeedsReviewError):
            return HTTPException(status_code=409, detail=str(exc))
        if isinstance(exc, InboxConflictError):
            return HTTPException(status_code=409, detail=str(exc))
        if isinstance(exc, ValueError):
            return HTTPException(status_code=400, detail=str(exc))
        return HTTPException(status_code=500, detail="Inbox operation failed")

    @router.get("")
    async def list_inbox(
        actor: InboxActor = Depends(current_actor),
        status_filter: str | None = Query(
            default=None,
            alias="status",
            description="Comma-separated lifecycle states",
        ),
        limit: int = Query(default=30, ge=1, le=100),
        before: float | None = Query(default=None, gt=0),
    ) -> list[dict[str, Any]]:
        try:
            return await inbox.list_for_actor(
                actor.user_id,
                status=status_filter,
                limit=limit,
                before=before,
            )
        except Exception as exc:
            raise translate_error(exc) from exc

    @router.get("/counts")
    async def inbox_counts(
        actor: InboxActor = Depends(current_actor),
    ) -> dict[str, int]:
        return await inbox.counts(actor.user_id)

    @router.get("/health")
    async def inbox_health(
        actor: InboxActor = Depends(current_actor),
    ) -> dict[str, Any]:
        if actor.user_id not in inbox.admin_ids:
            raise HTTPException(status_code=403, detail="Admin access required")
        return await inbox.health()

    @router.get("/{proposal_id}")
    async def get_proposal(
        proposal_id: str,
        actor: InboxActor = Depends(current_actor),
    ) -> dict[str, Any]:
        try:
            return await inbox.get(proposal_id, actor.user_id)
        except Exception as exc:
            raise translate_error(exc) from exc

    @router.post("/{proposal_id}/approve")
    async def approve_proposal(
        proposal_id: str,
        body: InboxMutationRequest,
        actor: InboxActor = Depends(current_actor),
    ) -> dict[str, Any]:
        try:
            return await inbox.approve(
                proposal_id,
                actor.user_id,
                expected_version=body.expected_version,
            )
        except Exception as exc:
            raise translate_error(exc) from exc

    @router.post("/{proposal_id}/retry")
    async def retry_proposal(
        proposal_id: str,
        body: InboxMutationRequest,
        actor: InboxActor = Depends(current_actor),
    ) -> dict[str, Any]:
        try:
            return await inbox.retry(
                proposal_id,
                actor.user_id,
                expected_version=body.expected_version,
            )
        except Exception as exc:
            raise translate_error(exc) from exc

    @router.post("/{proposal_id}/cancel")
    async def cancel_proposal(
        proposal_id: str,
        body: InboxMutationRequest,
        actor: InboxActor = Depends(current_actor),
    ) -> dict[str, Any]:
        try:
            return await inbox.cancel(
                proposal_id,
                actor.user_id,
                expected_version=body.expected_version,
            )
        except Exception as exc:
            raise translate_error(exc) from exc

    @router.post("/{proposal_id}/edit")
    async def edit_proposal(
        proposal_id: str,
        body: InboxMutationRequest,
        actor: InboxActor = Depends(current_actor),
    ) -> dict[str, Any]:
        try:
            return await inbox.mark_editing(
                proposal_id,
                actor.user_id,
                expected_version=body.expected_version,
            )
        except Exception as exc:
            raise translate_error(exc) from exc

    return router
