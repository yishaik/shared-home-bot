from __future__ import annotations

import asyncio
import time

from app.calendar_service import CalendarService
from app import google_client


async def _safe_incremental_sync(self: CalendarService):
    state = await self._sync_state()
    token = state.get("sync_token") or ""
    if not token:
        return await self.full_sync()

    reset_required = False
    async with self._sync_lock:
        try:
            result = await asyncio.to_thread(
                google_client.calendar_sync,
                self.settings,
                calendar_id=self.calendar_id,
                sync_token=token,
            )
            for event in result["events"]:
                await self._save_event(event)
            await self._set_sync_state(
                sync_token=result.get("next_sync_token") or token,
                last_incremental_sync_at=time.time(),
                last_error="",
            )
            return {"ok": True, "mode": "incremental", "count": len(result["events"])}
        except Exception as exc:
            if getattr(exc, "resp", None) is not None and getattr(exc.resp, "status", None) == 410:
                await self._set_sync_state(sync_token="")
                reset_required = True
            else:
                await self._set_sync_state(last_error=f"{type(exc).__name__}: {exc}")
                raise

    # Run outside the incremental lock; full_sync acquires the same lock.
    if reset_required:
        return await self.full_sync()
    raise RuntimeError("incremental sync ended without a result")


CalendarService.incremental_sync = _safe_incremental_sync
