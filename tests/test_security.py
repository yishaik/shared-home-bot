from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import urlencode

import pytest

from app.security import AuthenticationError, SessionSigner, parse_telegram_user, validate_telegram_init_data


def signed_init_data(token: str, now: int = 1_700_000_000) -> str:
    values = {
        "auth_date": str(now),
        "query_id": "AAEAAAE",
        "user": json.dumps({"id": 123, "first_name": "Yishai", "username": "yishaik"}, separators=(",", ":")),
    }
    data_check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def test_validate_telegram_init_data() -> None:
    payload = validate_telegram_init_data(signed_init_data("123:ABC"), "123:ABC", now=1_700_000_100)
    assert parse_telegram_user(payload).id == 123


def test_rejects_modified_init_data() -> None:
    data = signed_init_data("123:ABC").replace("Yishai", "Other")
    with pytest.raises(AuthenticationError):
        validate_telegram_init_data(data, "123:ABC", now=1_700_000_100)


def test_session_signer_roundtrip() -> None:
    signer = SessionSigner("secret", ttl_seconds=60)
    token = signer.issue(user_id=123, household_id="primary", display_name="Yishai")
    payload = signer.verify(token)
    assert payload["sub"] == 123
    assert payload["household_id"] == "primary"
