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
    feishu_event_verify_token: str | None = Field(
        None, alias="FEISHU_EVENT_VERIFY_TOKEN"
    )
    feishu_event_encrypt_key: str | None = Field(None, alias="FEISHU_EVENT_ENCRYPT_KEY")
    enable_ws_listener: bool = Field(True, alias="FEISHU_IO_ENABLE_WS")
    mark_read_reaction: bool = Field(True, alias="FEISHU_MARK_READ_REACTION")
    read_reaction_emoji: str = Field("OK", alias="FEISHU_READ_REACTION_EMOJI")


@lru_cache
def get_settings() -> Settings:
    return Settings()
