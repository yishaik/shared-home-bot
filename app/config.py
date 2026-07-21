from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    allowed_user_ids: list[int] = Field(default_factory=list, alias="ALLOWED_USER_IDS")

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o", alias="OPENAI_MODEL")
    openai_base_url: str | None = Field(default=None, alias="OPENAI_BASE_URL")

    def require_runtime(self) -> None:
        missing = []
        if not self.telegram_bot_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not self.openai_api_key:
            missing.append("OPENAI_API_KEY")
        if missing:
            raise RuntimeError(
                "Missing required env: " + ", ".join(missing) + " (see .env.example)"
            )

    home_name: str = Field(default="Our Home", alias="HOME_NAME")
    bot_display_name: str = Field(default="Home", alias="BOT_DISPLAY_NAME")

    public_url: str = Field(default="", alias="PUBLIC_URL")
    port: int = Field(default=8080, alias="PORT")
    database_path: str = Field(default="./data/home.db", alias="DATABASE_PATH")
    max_context_messages: int = Field(default=40, alias="MAX_CONTEXT_MESSAGES")

    @field_validator("allowed_user_ids", mode="before")
    @classmethod
    def parse_ids(cls, v):
        if v is None or v == "":
            return []
        if isinstance(v, list):
            return [int(x) for x in v]
        return [int(x.strip()) for x in str(v).split(",") if x.strip()]

    @property
    def webhook_url(self) -> str | None:
        base = (self.public_url or "").rstrip("/")
        if not base:
            return None
        return f"{base}/telegram/webhook"

    @property
    def db_path(self) -> Path:
        p = Path(self.database_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
