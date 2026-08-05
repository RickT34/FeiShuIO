from __future__ import annotations

import asyncio
import logging
import threading

from message_io.domain import IncomingMessage, MessageContent
from message_io.events import UserCommand, parse_user_command
from message_io.platforms.base import PlatformAdapter, PlatformError
from message_io.store import MessageStore

logger = logging.getLogger(__name__)

HELP_TEXT = """**MessageIO 指令**
- `/bind <alias>`：绑定当前会话
- `/bind`：查看当前会话的绑定
- `/binds`：查看所有 alias 及其会话数
- `/unbind`：删除当前会话的绑定
- `/help`：查看帮助"""


async def handle_incoming_message(
    *,
    message: IncomingMessage,
    store: MessageStore,
    adapter: PlatformAdapter | None = None,
) -> dict:
    command = parse_user_command(message.content.text)
    if command is not None:
        return await _handle_user_command(
            command=command,
            message=message,
            store=store,
            adapter=adapter,
        )

    if not store.add_message_once(message):
        return {"ok": True, "duplicate": True}
    return {"ok": True}


async def _handle_user_command(
    *,
    command: UserCommand,
    message: IncomingMessage,
    store: MessageStore,
    adapter: PlatformAdapter | None,
) -> dict:
    if command.name == "bind":
        assert command.alias is not None
        processed, changed, count = store.bind_destination_once(
            external_message_id=message.external_message_id,
            destination=message.destination,
            alias=command.alias,
        )
        if not processed:
            return {"ok": True, "duplicate": True}
        await _send_command_response(
            adapter,
            message,
            f"已绑定当前会话为 `{command.alias}`。该 alias 共绑定 {count} 个会话。",
        )
        return {
            "ok": True,
            "bound": command.alias,
            "changed": changed,
            "destination_count": count,
        }

    if command.name == "unbind":
        processed, alias, remaining = store.unbind_destination_once(
            external_message_id=message.external_message_id,
            destination=message.destination,
        )
        if not processed:
            return {"ok": True, "duplicate": True}
        if alias is None:
            text = "当前会话尚未绑定 alias。"
        else:
            text = f"已删除当前会话的绑定 `{alias}`。该 alias 还绑定 {remaining} 个会话。"
        await _send_command_response(adapter, message, text)
        return {"ok": True, "unbound": alias, "remaining": remaining}

    if not store.mark_processed_once(
        external_message_id=message.external_message_id,
        destination=message.destination,
    ):
        return {"ok": True, "duplicate": True}

    if command.name == "help" or command.name == "invalid":
        text = HELP_TEXT if command.name == "help" else f"指令格式不正确。\n\n{HELP_TEXT}"
        await _send_command_response(adapter, message, text)
        return {"ok": True, "command": command.name}

    if command.name == "current":
        alias = store.destination_alias(message.destination)
        text = (
            f"当前会话绑定为 `{alias}`。"
            if alias is not None
            else "当前会话尚未绑定 alias。"
        )
        await _send_command_response(adapter, message, text)
        return {"ok": True, "current": alias}

    bindings = store.list_bindings()
    if bindings:
        lines = ["**所有绑定**"]
        lines.extend(f"- `{alias}`：{count} 个会话" for alias, count in bindings)
        text = "\n".join(lines)
    else:
        text = "当前没有任何绑定。"
    await _send_command_response(adapter, message, text)
    return {"ok": True, "bindings": bindings}


async def _send_command_response(
    adapter: PlatformAdapter | None,
    message: IncomingMessage,
    text: str,
) -> None:
    if adapter is None:
        return
    try:
        await adapter.send(
            destination=message.destination,
            content=MessageContent(type="markdown", text=text),
        )
    except PlatformError:
        logger.exception("failed to send command response")


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
