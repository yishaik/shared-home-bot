from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl


class AuthenticationError(ValueError):
    pass


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


@dataclass(frozen=True)
class TelegramUser:
    id: int
    first_name: str = ""
    last_name: str = ""
    username: str = ""
    language_code: str = ""

    @property
    def display_name(self) -> str:
        full = " ".join(part for part in (self.first_name, self.last_name) if part).strip()
        return full or self.username or str(self.id)


def validate_telegram_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int = 3600,
    now: int | None = None,
) -> dict[str, Any]:
    if not init_data:
        raise AuthenticationError("Missing Telegram init data")
    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = values.pop("hash", "")
    if not received_hash:
        raise AuthenticationError("Missing Telegram hash")

    data_check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise AuthenticationError("Invalid Telegram signature")

    auth_date = int(values.get("auth_date", "0") or 0)
    current_time = int(now or time.time())
    if auth_date <= 0 or current_time - auth_date > max_age_seconds or auth_date > current_time + 30:
        raise AuthenticationError("Expired Telegram init data")

    user_raw = values.get("user")
    if not user_raw:
        raise AuthenticationError("Missing Telegram user")
    try:
        values["user"] = json.loads(user_raw)
    except json.JSONDecodeError as exc:
        raise AuthenticationError("Invalid Telegram user payload") from exc
    return values


def parse_telegram_user(payload: dict[str, Any]) -> TelegramUser:
    user = payload.get("user") or {}
    try:
        return TelegramUser(
            id=int(user["id"]),
            first_name=str(user.get("first_name") or ""),
            last_name=str(user.get("last_name") or ""),
            username=str(user.get("username") or ""),
            language_code=str(user.get("language_code") or ""),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthenticationError("Invalid Telegram user") from exc


class SessionSigner:
    def __init__(self, secret: str, ttl_seconds: int = 86400):
        if not secret:
            raise ValueError("Session secret is required")
        self.secret = secret.encode("utf-8")
        self.ttl_seconds = ttl_seconds

    def issue(self, *, user_id: int, household_id: str, display_name: str) -> str:
        now = int(time.time())
        payload = {
            "sub": user_id,
            "household_id": household_id,
            "name": display_name,
            "iat": now,
            "exp": now + self.ttl_seconds,
        }
        body = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signature = _b64url_encode(hmac.new(self.secret, body.encode("ascii"), hashlib.sha256).digest())
        return f"{body}.{signature}"

    def verify(self, token: str) -> dict[str, Any]:
        try:
            body, signature = token.split(".", 1)
        except ValueError as exc:
            raise AuthenticationError("Malformed session") from exc
        expected = _b64url_encode(hmac.new(self.secret, body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, signature):
            raise AuthenticationError("Invalid session signature")
        try:
            payload = json.loads(_b64url_decode(body))
        except (ValueError, json.JSONDecodeError) as exc:
            raise AuthenticationError("Invalid session payload") from exc
        if int(payload.get("exp", 0)) < int(time.time()):
            raise AuthenticationError("Expired session")
        return payload
