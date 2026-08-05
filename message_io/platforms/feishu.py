from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

import httpx

from message_io.domain import Destination, IncomingMessage, MessageContent, Sender
from message_io.platforms.base import PlatformError


logger = logging.getLogger(__name__)


class FeishuError(PlatformError):
    pass


def _raise_for_http_error(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise FeishuError(
            f"Feishu HTTP {exc.response.status_code}: {exc.response.text}"
        ) from exc


def build_message_payload(
    *,
    conversation_id: str,
    content: MessageContent,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    if content.type == "markdown":
        body = {
            "config": {"wide_screen_mode": True},
            "elements": [{"tag": "markdown", "content": content.text}],
        }
        message_type = "interactive"
    elif content.type == "text":
        body = {"text": content.text}
        message_type = "text"
    else:
        raise FeishuError(f"Feishu adapter cannot send content type: {content.type}")

    payload: dict[str, Any] = {
        "receive_id": conversation_id,
        "msg_type": message_type,
        "content": json.dumps(body, ensure_ascii=False),
    }
    if idempotency_key:
        payload["uuid"] = idempotency_key
    return payload


def build_reaction_payload(*, emoji_type: str) -> dict[str, Any]:
    return {"reaction_type": {"emoji_type": emoji_type}}


def extract_message(
    event_payload: dict[str, Any],
    *,
    account_id: str,
) -> IncomingMessage | None:
    event = event_payload.get("event")
    if not isinstance(event, dict):
        return None
    message = event.get("message")
    if not isinstance(message, dict):
        return None

    conversation_id = message.get("chat_id")
    if not isinstance(conversation_id, str) or not conversation_id:
        return None

    source_type = str(message.get("message_type") or "unknown")
    canonical_type = source_type if source_type in {
        "text", "image", "file", "audio", "video"
    } else "unknown"
    text = _extract_text(message.get("content"))
    if not text:
        text = f"[{canonical_type}]"

    sender_payload = event.get("sender")
    sender_payload = sender_payload if isinstance(sender_payload, dict) else {}
    sender_ids = sender_payload.get("sender_id")
    sender_ids = sender_ids if isinstance(sender_ids, dict) else {}
    sender_id = (
        sender_ids.get("open_id")
        or sender_ids.get("user_id")
        or sender_ids.get("union_id")
    )
    external_id = message.get("message_id")

    return IncomingMessage(
        destination=Destination(
            platform="feishu",
            account_id=account_id,
            conversation_id=conversation_id,
        ),
        external_message_id=str(external_id) if external_id is not None else None,
        sender=Sender(id=str(sender_id) if sender_id else None),
        content=MessageContent(type=canonical_type, text=text),
        raw=event_payload,
    )


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            return content
    if isinstance(content, dict):
        value = content.get("text")
        return str(value) if value is not None else ""
    return ""


class FeishuAdapter:
    platform = "feishu"

    def __init__(
        self,
        *,
        account_id: str,
        app_id: str,
        app_secret: str,
        reaction_emoji: str | None,
        timeout: float = 10.0,
        retry_attempts: int = 3,
        retry_base_delay: float = 0.5,
    ) -> None:
        self.account_id = account_id
        self.app_id = app_id
        self.app_secret = app_secret
        self.reaction_emoji = reaction_emoji
        self.timeout = timeout
        self.retry_attempts = retry_attempts
        self.retry_base_delay = retry_base_delay
        self._tenant_access_token: str | None = None
        self._token_expires_at = 0.0

    async def _post_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        retry_safe: bool = False,
    ) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(1, self.retry_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        url, params=params, headers=headers, json=json_body
                    )
                if response.status_code != 429 and response.status_code < 500:
                    _raise_for_http_error(response)
                    return response
                try:
                    _raise_for_http_error(response)
                except FeishuError as exc:
                    last_error = exc
            except httpx.RequestError as exc:
                last_error = FeishuError(f"Feishu request failed: {exc}")

            if not retry_safe or attempt >= self.retry_attempts:
                break
            delay = self.retry_base_delay * (2 ** (attempt - 1))
            logger.warning(
                "Feishu request failed on attempt %s/%s; retrying in %.1fs: %s",
                attempt,
                self.retry_attempts,
                delay,
                last_error,
            )
            if delay > 0:
                await asyncio.sleep(delay)
        raise last_error or FeishuError("Feishu request failed")

    async def get_tenant_access_token(self) -> str:
        now = time.time()
        if self._tenant_access_token and now < self._token_expires_at - 120:
            return self._tenant_access_token
        response = await self._post_json(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json_body={"app_id": self.app_id, "app_secret": self.app_secret},
            retry_safe=True,
        )
        data = response.json()
        if data.get("code") != 0:
            raise FeishuError(data.get("msg") or str(data))
        token = data.get("tenant_access_token")
        if not token:
            raise FeishuError("Feishu did not return tenant_access_token")
        self._tenant_access_token = token
        self._token_expires_at = now + int(data.get("expire") or 7200)
        return token

    async def send(self, *, destination: Destination, content: MessageContent) -> None:
        self._validate_destination(destination)
        token = await self.get_tenant_access_token()
        response = await self._post_json(
            "https://open.feishu.cn/open-apis/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers={"Authorization": f"Bearer {token}"},
            json_body=build_message_payload(
                conversation_id=destination.conversation_id,
                content=content,
                idempotency_key=uuid.uuid4().hex,
            ),
            retry_safe=True,
        )
        data = response.json()
        if data.get("code") != 0:
            raise FeishuError(data.get("msg") or str(data))

    async def mark_delivered(self, *, external_message_id: str) -> None:
        if not self.reaction_emoji:
            return
        token = await self.get_tenant_access_token()
        response = await self._post_json(
            f"https://open.feishu.cn/open-apis/im/v1/messages/{external_message_id}/reactions",
            headers={"Authorization": f"Bearer {token}"},
            json_body=build_reaction_payload(emoji_type=self.reaction_emoji),
        )
        data = response.json()
        if data.get("code") != 0:
            raise FeishuError(data.get("msg") or str(data))

    def _validate_destination(self, destination: Destination) -> None:
        if (destination.platform, destination.account_id) != (
            self.platform,
            self.account_id,
        ):
            raise FeishuError("destination does not belong to this Feishu adapter")
