from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_webhook_secret: str = Field(default="", alias="TELEGRAM_WEBHOOK_SECRET")
    allowed_user_ids: list[int] = Field(default_factory=list, alias="ALLOWED_USER_IDS")

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o", alias="OPENAI_MODEL")
    openai_base_url: str | None = Field(default=None, alias="OPENAI_BASE_URL")
    embedding_model: str = Field(default="text-embedding-3-small", alias="EMBEDDING_MODEL")

    home_name: str = Field(default="הבית שלנו", alias="HOME_NAME")
    bot_display_name: str = Field(default="Home", alias="BOT_DISPLAY_NAME")
    household_id: str = Field(default="primary", alias="HOUSEHOLD_ID")
    household_timezone: str = Field(default="Asia/Jerusalem", alias="HOUSEHOLD_TIMEZONE")

    public_url: str = Field(default="", alias="PUBLIC_URL")
    mini_app_url: str = Field(default="", alias="MINI_APP_URL")
    port: int = Field(default=8080, alias="PORT")
    database_path: str = Field(default="./data/home.db", alias="DATABASE_PATH")
    max_context_messages: int = Field(default=40, alias="MAX_CONTEXT_MESSAGES")
    max_init_data_age_seconds: int = Field(default=3600, alias="MAX_INIT_DATA_AGE_SECONDS")
    session_ttl_seconds: int = Field(default=86400, alias="SESSION_TTL_SECONDS")
    app_session_secret: str = Field(default="", alias="APP_SESSION_SECRET")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Google (dedicated bot account, OAuth refresh-token flow — see scripts/google_oauth.py)
    google_client_id: str = Field(default="", alias="GOOGLE_CLIENT_ID")
    google_client_secret: str = Field(default="", alias="GOOGLE_CLIENT_SECRET")
    google_refresh_token: str = Field(default="", alias="GOOGLE_REFRESH_TOKEN")
    google_calendar_id: str = Field(default="primary", alias="GOOGLE_CALENDAR_ID")
    google_docs_folder_id: str = Field(default="", alias="GOOGLE_DOCS_FOLDER_ID")
    google_oauth_setup_secret: str = Field(default="", alias="GOOGLE_OAUTH_SETUP_SECRET")

    @field_validator("allowed_user_ids", mode="before")
    @classmethod
    def parse_ids(cls, value):
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return [int(x) for x in value]
        return [int(x.strip()) for x in str(value).split(",") if x.strip()]

    @property
    def resolved_public_url(self) -> str:
        explicit = (self.public_url or "").rstrip("/")
        if explicit:
            return explicit
        railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
        return f"https://{railway_domain}" if railway_domain else ""

    @property
    def resolved_mini_app_url(self) -> str:
        explicit = (self.mini_app_url or "").rstrip("/")
        if explicit:
            return explicit
        base = self.resolved_public_url
        return f"{base}/app" if base else ""

    @property
    def webhook_url(self) -> str | None:
        base = self.resolved_public_url
        return f"{base}/telegram/webhook" if base else None

    @property
    def db_path(self) -> Path:
        path = Path(self.database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def effective_session_secret(self) -> str:
        return self.app_session_secret or self.telegram_bot_token

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret and self.google_refresh_token)

    def require_runtime(self) -> None:
        missing: list[str] = []
        if not self.telegram_bot_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not self.openai_api_key:
            missing.append("OPENAI_API_KEY")
        if not self.allowed_user_ids:
            missing.append("ALLOWED_USER_IDS")
        if self.webhook_url and not self.telegram_webhook_secret:
            missing.append("TELEGRAM_WEBHOOK_SECRET")
        if not self.effective_session_secret:
            missing.append("APP_SESSION_SECRET")
        if missing:
            raise RuntimeError("Missing required env: " + ", ".join(missing) + " (see .env.example)")

    @staticmethod
    def generate_secret() -> str:
        return secrets.token_urlsafe(32)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
