from __future__ import annotations

import json
import os
from typing import Any

import httpx

from message_io.client_config import ClientConfig, load_client_config, normalize_server_url


DEFAULT_BASE_URL = "http://127.0.0.1:8000"


class MessageIOError(RuntimeError):
    pass


class MessageIO:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
        config_path: str | os.PathLike[str] | None = None,
    ) -> None:
        environment_url = os.getenv("MESSAGE_IO_URL")
        environment_key = os.getenv("MESSAGE_IO_API_KEY")
        selected_url = base_url if base_url is not None else (environment_url or None)
        stored = ClientConfig()
        if selected_url is None or not (api_key or environment_key):
            stored = load_client_config(config_path)
        self.base_url = normalize_server_url(
            selected_url if selected_url is not None else (stored.url or DEFAULT_BASE_URL)
        )
        self.api_key = api_key or environment_key or stored.api_key
        if not self.api_key:
            raise ValueError("api_key is required, or set MESSAGE_IO_API_KEY")
        self.timeout = timeout
        self.transport = transport

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key or ""}

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            with httpx.Client(
                base_url=self.base_url,
                timeout=self.timeout,
                transport=self.transport,
            ) as client:
                response = client.request(method, path, headers=self._headers(), **kwargs)
        except httpx.HTTPError as exc:
            raise MessageIOError(
                f"request to {self.base_url}{path} failed: {exc}"
            ) from exc
        if response.status_code >= 400:
            detail: Any = response.text.strip()
            try:
                payload = response.json()
                if isinstance(payload, dict) and "detail" in payload:
                    detail = payload["detail"]
            except ValueError:
                pass
            if not isinstance(detail, str):
                detail = json.dumps(detail, ensure_ascii=False, separators=(",", ":"))
            raise MessageIOError(f"{response.status_code}: {detail}")
        try:
            return response.json()
        except ValueError as exc:
            raise MessageIOError(
                f"{response.status_code} response from {path} is not valid JSON"
            ) from exc

    def send(
        self,
        target: str,
        text: str,
        *,
        content_type: str = "markdown",
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/messages/send",
            json={
                "target": target,
                "content": {"type": content_type, "text": text},
            },
        )

    def receive(
        self,
        target: str,
        *,
        limit: int = 100,
        ack: bool = True,
    ) -> list[dict[str, Any]]:
        return list(
            self.receive_response(target, limit=limit, ack=ack).get("messages") or []
        )

    def receive_response(
        self,
        target: str,
        *,
        limit: int = 100,
        ack: bool = True,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/messages/receive",
            json={"target": target, "limit": limit, "ack": ack},
        )

    def acknowledge(
        self,
        target: str,
        message_ids: list[int],
        *,
        lease_token: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/messages/acknowledge",
            json={
                "target": target,
                "message_ids": message_ids,
                "lease_token": lease_token,
            },
        )

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def ready(self) -> dict[str, Any]:
        return self._request("GET", "/ready")

    def cleanup(self) -> dict[str, Any]:
        return self._request("POST", "/maintenance/cleanup")
