from __future__ import annotations

from pydantic import BaseModel, Field

from message_io.domain import ContentType


class MessageContent(BaseModel):
    type: ContentType
    text: str = Field(..., min_length=1)


class MessageSender(BaseModel):
    id: str | None = None
    name: str | None = None


class SendMessageRequest(BaseModel):
    target: str = Field(..., min_length=1, max_length=64)
    content: MessageContent


class SendMessageResponse(BaseModel):
    ok: bool
    target: str
    sent: int


class ReceiveMessagesRequest(BaseModel):
    target: str = Field(..., min_length=1, max_length=64)
    limit: int = Field(100, ge=1, le=500)
    ack: bool = Field(
        True,
        description=(
            "When true, messages are marked delivered before the response returns. "
            "When false, messages are leased and must be acknowledged later."
        ),
    )


class ReceivedMessage(BaseModel):
    message_id: int
    sender: MessageSender
    content: MessageContent
    received_at: str


class ReceiveMessagesResponse(BaseModel):
    ok: bool
    target: str
    messages: list[ReceivedMessage]
    ack_required: bool = False
    lease_token: str | None = None


class AckMessagesRequest(BaseModel):
    target: str = Field(..., min_length=1, max_length=64)
    message_ids: list[int] = Field(..., min_length=1, max_length=500)
    lease_token: str = Field(..., min_length=32, max_length=64)


class AckMessagesResponse(BaseModel):
    ok: bool
    target: str
    acked: int
