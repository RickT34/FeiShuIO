from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    api_key: str = Field(..., alias="FEISHU_IO_API_KEY")
    feishu_app_id: str = Field(..., alias="FEISHU_APP_ID")
    feishu_app_secret: str = Field(..., alias="FEISHU_APP_SECRET")
    db_path: str = Field("feishu_io.sqlite3", alias="FEISHU_IO_DB")
    host: str = Field("0.0.0.0", min_length=1, alias="FEISHU_IO_HOST")
    port: int = Field(8000, ge=1, le=65535, alias="FEISHU_IO_PORT")
    log_level: str = Field("info", min_length=1, alias="FEISHU_IO_LOG_LEVEL")
    feishu_event_verify_token: str | None = Field(
        None, alias="FEISHU_EVENT_VERIFY_TOKEN"
    )
    feishu_event_encrypt_key: str | None = Field(None, alias="FEISHU_EVENT_ENCRYPT_KEY")
    enable_ws_listener: bool = Field(True, alias="FEISHU_IO_ENABLE_WS")
    mark_read_reaction: bool = Field(True, alias="FEISHU_MARK_READ_REACTION")
    read_reaction_emoji: str = Field("Get", alias="FEISHU_READ_REACTION_EMOJI")
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
    message_lease_seconds: int = Field(
        300, ge=1, le=86400, alias="FEISHU_MESSAGE_LEASE_SECONDS"
    )
    delivered_retention_days: int = Field(
        30, ge=1, le=3650, alias="FEISHU_DELIVERED_RETENTION_DAYS"
    )
    processed_retention_days: int = Field(
        30, ge=1, le=3650, alias="FEISHU_PROCESSED_RETENTION_DAYS"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
