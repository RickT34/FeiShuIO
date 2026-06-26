from __future__ import annotations

from pydantic import BaseModel, Field


class SendMarkdownRequest(BaseModel):
    id: str = Field(..., min_length=1, description="Bound Feishu group id.")
    text: str = Field(..., min_length=1, description="Markdown text to send.")


class SendMarkdownResponse(BaseModel):
    ok: bool
    id: str
    feishu_code: int | None = None
    message: str | None = None


class RecvUnreadRequest(BaseModel):
    id: str = Field(..., min_length=1, description="Bound Feishu group id.")
    limit: int = Field(100, ge=1, le=500)


class UnreadMessage(BaseModel):
    message_id: int
    external_message_id: str | None = None
    id: str
    sender_id: str | None = None
    sender_name: str | None = None
    message_type: str
    text: str
    raw: dict
    created_at: str


class RecvUnreadResponse(BaseModel):
    ok: bool
    id: str
    messages: list[UnreadMessage]
