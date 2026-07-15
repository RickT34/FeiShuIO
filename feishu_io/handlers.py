from __future__ import annotations

import asyncio
import logging
import threading
from typing import Protocol

from feishu_io.events import IncomingMessage, parse_bind_command
from feishu_io.feishu import FeishuAppError
from feishu_io.store import MessageStore

logger = logging.getLogger(__name__)


class MarkdownSender(Protocol):
    async def send_markdown(self, *, chat_id: str, text: str) -> dict:
        ...


async def handle_incoming_message(
    *,
    message: IncomingMessage,
    store: MessageStore,
    client: MarkdownSender | None = None,
) -> dict:
    chat_id = message.group_id
    alias = parse_bind_command(message.text)
    if alias:
        processed, changed = store.bind_group_once(
            external_message_id=message.external_message_id,
            alias=alias,
            chat_id=chat_id,
        )
        if not processed:
            return {"ok": True, "duplicate": True}
        if changed and client:
            try:
                await client.send_markdown(
                    chat_id=chat_id,
                    text=f"已绑定当前群为 `{alias}`。",
                )
            except FeishuAppError:
                logger.exception("failed to send bind confirmation")
        return {"ok": True, "bound": alias, "changed": changed}

    if not store.add_message_once(message):
        return {"ok": True, "duplicate": True}
    return {"ok": True}


def handle_incoming_message_sync(
    *,
    message: IncomingMessage,
    store: MessageStore,
    client: MarkdownSender | None = None,
) -> dict:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            handle_incoming_message(message=message, store=store, client=client)
        )

    result: dict | None = None
    error: BaseException | None = None

    def run_in_thread() -> None:
        nonlocal result, error
        try:
            result = asyncio.run(
                handle_incoming_message(message=message, store=store, client=client)
            )
        except BaseException as exc:
            error = exc

    thread = threading.Thread(target=run_in_thread, name="feishu-message-handler")
    thread.start()
    thread.join()
    if error:
        raise error
    return result or {"ok": True}
