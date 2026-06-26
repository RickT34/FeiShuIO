from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


ALIAS_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


@dataclass(frozen=True)
class IncomingMessage:
    group_id: str
    external_message_id: str | None
    sender_id: str | None
    sender_name: str | None
    message_type: str
    text: str
    raw: dict[str, Any]

    def for_group(self, group_id: str) -> "IncomingMessage":
        return IncomingMessage(
            group_id=group_id,
            external_message_id=self.external_message_id,
            sender_id=self.sender_id,
            sender_name=self.sender_name,
            message_type=self.message_type,
            text=self.text,
            raw=self.raw,
        )


def normalize_alias(alias: str) -> str:
    return alias.strip()


def parse_bind_command(text: str) -> str | None:
    parts = text.strip().split()
    if len(parts) != 2 or parts[0] != "/bind":
        return None
    alias = normalize_alias(parts[1])
    if not ALIAS_RE.fullmatch(alias):
        return None
    return alias


def extract_text_message(event_payload: dict[str, Any]) -> IncomingMessage | None:
    event = event_payload.get("event")
    if not isinstance(event, dict):
        return None

    message = event.get("message")
    if not isinstance(message, dict):
        return None

    chat_id = message.get("chat_id")
    message_type = message.get("message_type") or "unknown"
    if not isinstance(chat_id, str) or not chat_id:
        return None

    text = ""
    content = message.get("content")
    if isinstance(content, str):
        try:
            parsed_content = json.loads(content)
        except json.JSONDecodeError:
            text = content
        else:
            if isinstance(parsed_content, dict):
                text = str(parsed_content.get("text") or "")
    elif isinstance(content, dict):
        text = str(content.get("text") or "")

    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    sender_id = sender.get("sender_id") if isinstance(sender.get("sender_id"), dict) else {}

    external_message_id = message.get("message_id")
    if external_message_id is not None and not isinstance(external_message_id, str):
        external_message_id = str(external_message_id)

    return IncomingMessage(
        group_id=chat_id,
        external_message_id=external_message_id,
        sender_id=sender_id.get("open_id")
        or sender_id.get("user_id")
        or sender_id.get("union_id"),
        sender_name=sender.get("sender_type"),
        message_type=str(message_type),
        text=text,
        raw=event_payload,
    )
