from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypeAlias


ContentType: TypeAlias = Literal[
    "text", "markdown", "image", "file", "audio", "video", "unknown"
]


@dataclass(frozen=True)
class Destination:
    platform: str
    account_id: str
    conversation_id: str


@dataclass(frozen=True)
class MessageContent:
    type: ContentType
    text: str

    def __post_init__(self) -> None:
        if not self.text:
            raise ValueError("message content text must not be empty")


@dataclass(frozen=True)
class Sender:
    id: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class IncomingMessage:
    destination: Destination
    external_message_id: str | None
    sender: Sender
    content: MessageContent
    raw: dict[str, Any]


@dataclass(frozen=True)
class DeliveryReference:
    platform: str
    account_id: str
    external_message_id: str | None
