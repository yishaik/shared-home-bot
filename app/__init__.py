"""Shared Home Telegram bot — one memory for the household."""

# Keep the original Google Docs/Sheets client intact while exposing the rebuilt
# Calendar gateway through the same module namespace used by CalendarService.
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
