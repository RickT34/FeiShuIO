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
    ack: bool = Field(
        True,
        description=(
            "When true, messages are marked delivered before the response returns. "
            "When false, messages are leased and must be acknowledged later."
        ),
    )


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
    lease_token: str | None = None


class RecvUnreadResponse(BaseModel):
    ok: bool
    id: str
    messages: list[UnreadMessage]
    ack_required: bool = False


class AckMessagesRequest(BaseModel):
    id: str = Field(..., min_length=1, description="Bound Feishu group id.")
    message_ids: list[int] = Field(..., min_length=1, max_length=500)
    lease_token: str = Field(..., min_length=32, max_length=64)


class AckMessagesResponse(BaseModel):
    ok: bool
    id: str
    acked: int
