from __future__ import annotations

from app.work_service import WorkService


class HouseholdWorkService(WorkService):
    """Compatibility name for Telegram handlers.

    WorkService is patched once at package startup so API, agent and Telegram
    entrypoints all enqueue the same assignment notifications.
    """

    pass
