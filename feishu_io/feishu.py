from __future__ import annotations

import json
import time
from typing import Any

import httpx


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


def build_markdown_message_payload(*, chat_id: str, text: str) -> dict[str, Any]:
    card = {
        "config": {"wide_screen_mode": True},
        "elements": [
            {
                "tag": "markdown",
                "content": text,
            }
        ],
    }
    return {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    }


def build_reaction_payload(*, emoji_type: str) -> dict[str, Any]:
    return {"reaction_type": {"emoji_type": emoji_type}}


class FeishuAppClient:
    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        timeout: float = 10.0,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.timeout = timeout
        self._tenant_access_token: str | None = None
        self._token_expires_at = 0.0

    async def get_tenant_access_token(self) -> str:
        now = time.time()
        if self._tenant_access_token and now < self._token_expires_at - 120:
            return self._tenant_access_token

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={
                    "app_id": self.app_id,
                    "app_secret": self.app_secret,
                },
            )
            _raise_for_feishu_http_error(response)

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
        payload = build_markdown_message_payload(chat_id=chat_id, text=text)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                "https://open.feishu.cn/open-apis/im/v1/messages",
                params={"receive_id_type": "chat_id"},
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
            _raise_for_feishu_http_error(response)

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

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reactions",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
            _raise_for_feishu_http_error(response)

        data = response.json()
        if data.get("code") != 0:
            raise FeishuAppError(data.get("msg") or str(data))
        return data
