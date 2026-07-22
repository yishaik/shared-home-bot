from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.config import Settings


ShareKind = Literal["todo", "shopping", "event", "help"]
ShareSurface = Literal["inline", "guest", "group", "private"]

SAFE_OPERATIONAL_KINDS: frozenset[str] = frozenset({"todo", "shopping", "event", "help"})
PRIVATE_KINDS: frozenset[str] = frozenset(
    {
        "memory",
        "core_memory",
        "note",
        "person",
        "setting",
        "document",
        "sheet",
        "file",
        "credential",
    }
)


@dataclass(frozen=True, slots=True)
class ShareDecision:
    allowed: bool
    reason: str = ""


class SharePolicy:
    """One authorization boundary for Telegram surfaces that can post externally.

    Inline and guest results may be inserted into chats that are not part of the
    household. They therefore expose only deliberately shareable operational
    summaries, never the broader household prompt snapshot.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def actor_may_use(self, actor_id: int) -> bool:
        return actor_id in self.settings.allowed_user_ids

    def decide(self, *, actor_id: int, kind: str, surface: ShareSurface) -> ShareDecision:
        if not self.actor_may_use(actor_id):
            return ShareDecision(False, "actor_not_allowed")
        normalized = (kind or "").strip().lower()
        if normalized in PRIVATE_KINDS:
            return ShareDecision(False, "private_kind")
        if surface in {"inline", "guest"} and normalized not in SAFE_OPERATIONAL_KINDS:
            return ShareDecision(False, "not_externally_shareable")
        if normalized not in SAFE_OPERATIONAL_KINDS:
            return ShareDecision(False, "unknown_kind")
        return ShareDecision(True)

    def may_share(self, *, actor_id: int, kind: str, surface: ShareSurface) -> bool:
        return self.decide(actor_id=actor_id, kind=kind, surface=surface).allowed
