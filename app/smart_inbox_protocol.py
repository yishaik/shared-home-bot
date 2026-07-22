from __future__ import annotations

import json
from typing import Any

MARKER_PREFIX = "__SMART_INBOX_V2__:"


def proposal_marker(proposal: dict[str, Any]) -> str:
    payload = {
        "id": proposal["id"],
        "status": proposal["status"],
        "version": int(proposal["version"]),
        "summary": proposal["summary"],
        "last_error": proposal.get("last_error") or "",
    }
    return MARKER_PREFIX + json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def parse_marker(text: str) -> dict[str, Any] | None:
    if not text.startswith(MARKER_PREFIX):
        return None
    try:
        payload = json.loads(text[len(MARKER_PREFIX):])
    except (TypeError, ValueError):
        return None
    if (
        not isinstance(payload, dict)
        or not payload.get("id")
        or not payload.get("status")
    ):
        return None
    return payload
