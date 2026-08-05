from __future__ import annotations

import asyncio
import logging
import threading
from message_io.domain import IncomingMessage, MessageContent
from message_io.events import parse_bind_command
from message_io.platforms.base import PlatformAdapter, PlatformError
from message_io.store import MessageStore

logger = logging.getLogger(__name__)


async def handle_incoming_message(
    *,
    message: IncomingMessage,
    store: MessageStore,
    adapter: PlatformAdapter | None = None,
) -> dict:
    alias = parse_bind_command(message.content.text)
    if alias:
        processed, changed = store.bind_destination_once(
            external_message_id=message.external_message_id,
            destination=message.destination,
            alias=alias,
        )
        if not processed:
            return {"ok": True, "duplicate": True}
        if changed and adapter:
            try:
                await adapter.send(
                    destination=message.destination,
                    content=MessageContent(
                        type="markdown", text=f"已绑定当前会话为 `{alias}`。"
                    ),
                )
            except PlatformError:
                logger.exception("failed to send bind confirmation")
        return {"ok": True, "bound": alias, "changed": changed}

    if not store.add_message_once(message):
        return {"ok": True, "duplicate": True}
    return {"ok": True}


def handle_incoming_message_sync(
    *,
    message: IncomingMessage,
    store: MessageStore,
    adapter: PlatformAdapter | None = None,
) -> dict:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            handle_incoming_message(message=message, store=store, adapter=adapter)
        )

    result: dict | None = None
    error: BaseException | None = None

    def run_in_thread() -> None:
        nonlocal result, error
        try:
            result = asyncio.run(
                handle_incoming_message(message=message, store=store, adapter=adapter)
            )
        except BaseException as exc:
            error = exc

    thread = threading.Thread(target=run_in_thread, name="platform-message-handler")
    thread.start()
    thread.join()
    if error:
        raise error
    return result or {"ok": True}
