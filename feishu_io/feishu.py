from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

import httpx


logger = logging.getLogger(__name__)


class FeishuAppError(RuntimeError):
    pass


def _raise_for_feishu_http_error(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        body = exc.response.text
        raise FeishuAppError(
            f"Feishu HTTP {exc.response.status_code}: {body}"
        ) from exc


def build_markdown_message_payload(
    *,
    chat_id: str,
    text: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    card = {
        "config": {"wide_screen_mode": True},
        "elements": [
            {
                "tag": "markdown",
                "content": text,
            }
        ],
    }
    payload = {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    }
    if idempotency_key:
        payload["uuid"] = idempotency_key
    return payload


def build_reaction_payload(*, emoji_type: str) -> dict[str, Any]:
    return {"reaction_type": {"emoji_type": emoji_type}}


class FeishuAppClient:
    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        timeout: float = 10.0,
        retry_attempts: int = 3,
        retry_base_delay: float = 0.5,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
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
                        url,
                        params=params,
                        headers=headers,
                        json=json_body,
                    )
                if response.status_code not in {429} and response.status_code < 500:
                    _raise_for_feishu_http_error(response)
                    return response

                try:
                    _raise_for_feishu_http_error(response)
                except FeishuAppError as exc:
                    last_error = exc
            except httpx.RequestError as exc:
                last_error = FeishuAppError(f"Feishu request failed: {exc}")

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

        raise last_error or FeishuAppError("Feishu request failed")

    async def get_tenant_access_token(self) -> str:
        now = time.time()
        if self._tenant_access_token and now < self._token_expires_at - 120:
            return self._tenant_access_token

        response = await self._post_json(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json_body={
                "app_id": self.app_id,
                "app_secret": self.app_secret,
            },
            retry_safe=True,
        )

        data = response.json()
        if data.get("code") != 0:
            raise FeishuAppError(data.get("msg") or str(data))

        token = data.get("tenant_access_token")
        if not token:
            raise FeishuAppError("Feishu did not return tenant_access_token")

        self._tenant_access_token = token
        self._token_expires_at = now + int(data.get("expire") or 7200)
        return token

    async def send_markdown(self, *, chat_id: str, text: str) -> dict[str, Any]:
        token = await self.get_tenant_access_token()
        payload = build_markdown_message_payload(
            chat_id=chat_id,
            text=text,
            idempotency_key=uuid.uuid4().hex,
        )

        response = await self._post_json(
            "https://open.feishu.cn/open-apis/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers={"Authorization": f"Bearer {token}"},
            json_body=payload,
            retry_safe=True,
        )

        data = response.json()
        if data.get("code") != 0:
            raise FeishuAppError(data.get("msg") or str(data))
        return data

    async def add_reaction(
        self,
        *,
        message_id: str,
        emoji_type: str,
    ) -> dict[str, Any]:
        token = await self.get_tenant_access_token()
        payload = build_reaction_payload(emoji_type=emoji_type)

        response = await self._post_json(
            f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reactions",
            headers={"Authorization": f"Bearer {token}"},
            json_body=payload,
        )

        data = response.json()
        if data.get("code") != 0:
            raise FeishuAppError(data.get("msg") or str(data))
        return data
