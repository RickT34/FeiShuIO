from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_key: str = Field(..., alias="MESSAGE_IO_API_KEY")
    db_path: str = Field("message_io.sqlite3", alias="MESSAGE_IO_DB")
    host: str = Field("0.0.0.0", min_length=1, alias="MESSAGE_IO_HOST")
    port: int = Field(8000, ge=1, le=65535, alias="MESSAGE_IO_PORT")
    log_level: str = Field("info", min_length=1, alias="MESSAGE_IO_LOG_LEVEL")
    message_lease_seconds: int = Field(
        300, ge=1, le=86400, alias="MESSAGE_IO_LEASE_SECONDS"
    )
    delivered_retention_days: int = Field(
        30, ge=1, le=3650, alias="MESSAGE_IO_DELIVERED_RETENTION_DAYS"
    )
    processed_retention_days: int = Field(
        30, ge=1, le=3650, alias="MESSAGE_IO_PROCESSED_RETENTION_DAYS"
    )

    feishu_enabled: bool = Field(True, alias="FEISHU_ENABLED")
    feishu_account_id: str = Field("default", alias="FEISHU_ACCOUNT_ID")
    feishu_app_id: str | None = Field(None, alias="FEISHU_APP_ID")
    feishu_app_secret: str | None = Field(None, alias="FEISHU_APP_SECRET")
    feishu_event_verify_token: str | None = Field(
        None, alias="FEISHU_EVENT_VERIFY_TOKEN"
    )
    feishu_event_encrypt_key: str | None = Field(None, alias="FEISHU_EVENT_ENCRYPT_KEY")
    feishu_listener_enabled: bool = Field(True, alias="FEISHU_LISTENER_ENABLED")
    feishu_mark_delivered_reaction: bool = Field(
        True, alias="FEISHU_MARK_DELIVERED_REACTION"
    )
    feishu_read_reaction_emoji: str = Field(
        "Get", alias="FEISHU_DELIVERED_REACTION_EMOJI"
    )
    feishu_retry_attempts: int = Field(3, ge=1, le=10, alias="FEISHU_RETRY_ATTEMPTS")
    feishu_retry_base_delay: float = Field(
        0.5, ge=0.0, le=30.0, alias="FEISHU_RETRY_BASE_DELAY"
    )
    listener_retry_base_delay: float = Field(
        1.0, ge=0.0, le=300.0, alias="FEISHU_LISTENER_RETRY_BASE_DELAY"
    )
    listener_retry_max_delay: float = Field(
        60.0, ge=0.0, le=3600.0, alias="FEISHU_LISTENER_RETRY_MAX_DELAY"
    )

    @model_validator(mode="after")
    def validate_enabled_platforms(self) -> "Settings":
        if self.feishu_enabled and not (self.feishu_app_id and self.feishu_app_secret):
            raise ValueError(
                "FEISHU_APP_ID and FEISHU_APP_SECRET are required when FEISHU_ENABLED=true"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
