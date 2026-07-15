from __future__ import annotations

import os
from typing import Any

import httpx


DEFAULT_BASE_URL = "http://127.0.0.1:8000"


class FeishuIOError(RuntimeError):
    pass


class FeishuIO:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("FEISHU_IO_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key or os.getenv("FEISHU_IO_API_KEY")
        if not self.api_key:
            raise ValueError("api_key is required, or set FEISHU_IO_API_KEY")
        self.timeout = timeout
        self.transport = transport

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key or ""}

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        with httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            transport=self.transport,
        ) as client:
            response = client.request(method, path, headers=self._headers(), **kwargs)
        if response.status_code >= 400:
            raise FeishuIOError(
                f"{response.status_code} {response.reason_phrase}: {response.text}"
            )
        return response.json()

    def send_markdown(self, text: str, id: str) -> dict[str, Any]:
        return self._request("POST", "/send_markdown", json={"id": id, "text": text})

    def recv_unread(
        self,
        id: str,
        *,
        limit: int = 100,
        ack: bool = True,
    ) -> list[dict[str, Any]]:
        data = self._request(
            "POST",
            "/recv_unread",
            json={"id": id, "limit": limit, "ack": ack},
        )
        return list(data.get("messages") or [])

    def recv_unread_response(
        self,
        id: str,
        *,
        limit: int = 100,
        ack: bool = True,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/recv_unread",
            json={"id": id, "limit": limit, "ack": ack},
        )

    def ack_messages(
        self,
        id: str,
        message_ids: list[int],
        *,
        lease_token: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/ack_messages",
            json={
                "id": id,
                "message_ids": message_ids,
                "lease_token": lease_token,
            },
        )

    def health(self) -> dict[str, Any]:
        with httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            transport=self.transport,
        ) as client:
            response = client.get("/health")
        if response.status_code >= 400:
            raise FeishuIOError(
                f"{response.status_code} {response.reason_phrase}: {response.text}"
            )
        return response.json()

    def ready(self) -> dict[str, Any]:
        with httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            transport=self.transport,
        ) as client:
            response = client.get("/ready")
        if response.status_code >= 400:
            raise FeishuIOError(
                f"{response.status_code} {response.reason_phrase}: {response.text}"
            )
        return response.json()

    def cleanup(self) -> dict[str, Any]:
        return self._request("POST", "/maintenance/cleanup")
