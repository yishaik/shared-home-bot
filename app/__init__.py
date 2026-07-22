"""Shared Home Telegram bot — one memory for the household."""

from app import google_client as google_client  # noqa: E402,F401
from app import google_calendar_gateway as _calendar_gateway  # noqa: E402

for _name in (
    "calendar_create",
    "calendar_get",
    "calendar_update",
    "calendar_delete",
    "calendar_sync",
):
    setattr(google_client, _name, getattr(_calendar_gateway, _name))

from app import calendar_work_block_patch as _calendar_work_block_patch  # noqa: E402,F401
from app import context_patch as _context_patch  # noqa: E402,F401
from app import web_prompt_patch as _web_prompt_patch  # noqa: E402,F401
