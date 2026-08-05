from __future__ import annotations

from typing import Any, Protocol

from message_io.domain import Destination, MessageContent


class PlatformError(RuntimeError):
    pass


class PlatformAdapter(Protocol):
    platform: str
    account_id: str

    async def send(self, *, destination: Destination, content: MessageContent) -> None:
        ...

    async def mark_delivered(self, *, external_message_id: str) -> None:
        ...


class PlatformListener(Protocol):
    def start(self) -> Any:
        ...

    def stop(self, timeout: float = 5.0) -> None:
        ...

    def status(self) -> dict[str, Any]:
        ...
