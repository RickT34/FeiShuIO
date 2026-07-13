import json

import httpx
import pytest

from feishu_io.client import FeishuIO, FeishuIOError


def make_transport(handler):
    return httpx.MockTransport(handler)


def test_python_client_send_markdown_constructs_request():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/send_markdown"
        assert request.headers["X-API-Key"] == "secret"
        assert json.loads(request.content) == {"id": "test", "text": "**hi**"}
        return httpx.Response(200, json={"ok": True, "id": "test"})

    client = FeishuIO("http://feishuio.local", "secret", transport=make_transport(handler))

    assert client.send_markdown("**hi**", "test") == {"ok": True, "id": "test"}
    assert len(requests) == 1


def test_python_client_recv_unread_returns_messages():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/recv_unread"
        assert json.loads(request.content) == {"id": "test", "limit": 2, "ack": False}
        return httpx.Response(
            200,
            json={
                "ok": True,
                "id": "test",
                "messages": [{"message_id": 1, "text": "hello"}],
                "ack_required": True,
            },
        )

    client = FeishuIO("http://feishuio.local", "secret", transport=make_transport(handler))

    assert client.recv_unread("test", limit=2, ack=False) == [
        {"message_id": 1, "text": "hello"}
    ]


def test_python_client_raises_for_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "unknown id"})

    client = FeishuIO("http://feishuio.local", "secret", transport=make_transport(handler))

    with pytest.raises(FeishuIOError, match="404"):
        client.recv_unread("missing")

