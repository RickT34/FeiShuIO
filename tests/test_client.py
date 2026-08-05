import json

import httpx
import pytest

from message_io.client import MessageIO, MessageIOError


def transport(handler):
    return httpx.MockTransport(handler)


def test_send_uses_only_platform_neutral_contract():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/messages/send"
        assert json.loads(request.content) == {
            "target": "ops",
            "content": {"type": "markdown", "text": "**done**"},
        }
        return httpx.Response(200, json={"ok": True, "target": "ops", "sent": 2})

    client = MessageIO("http://message.local", "secret", transport=transport(handler))
    assert client.send("ops", "**done**") == {
        "ok": True,
        "target": "ops",
        "sent": 2,
    }


def test_receive_returns_common_message_shape():
    response = {
        "ok": True,
        "target": "ops",
        "messages": [
            {
                "message_id": 1,
                "sender": {"id": "u1", "name": "Rick"},
                "content": {"type": "text", "text": "continue"},
                "received_at": "2026-08-05 10:00:00",
            }
        ],
        "ack_required": True,
        "lease_token": "a" * 32,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/messages/receive"
        assert json.loads(request.content) == {"target": "ops", "limit": 2, "ack": False}
        return httpx.Response(200, json=response)

    client = MessageIO("http://message.local", "secret", transport=transport(handler))
    assert client.receive_response("ops", limit=2, ack=False) == response


def test_acknowledge_uses_target_and_lease_token():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/messages/acknowledge"
        assert json.loads(request.content) == {
            "target": "ops",
            "message_ids": [1, 2],
            "lease_token": "a" * 32,
        }
        return httpx.Response(200, json={"ok": True, "target": "ops", "acked": 2})

    client = MessageIO("http://message.local", "secret", transport=transport(handler))
    assert client.acknowledge("ops", [1, 2], lease_token="a" * 32)["acked"] == 2


def test_structured_server_error_is_compact():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": {"ok": False, "platform": "offline"}})

    client = MessageIO("http://message.local", "secret", transport=transport(handler))
    with pytest.raises(MessageIOError, match=r'^503: \{"ok":false,"platform":"offline"\}$'):
        client.ready()


def test_client_reads_message_io_environment(monkeypatch):
    monkeypatch.setenv("MESSAGE_IO_URL", "https://message.example")
    monkeypatch.setenv("MESSAGE_IO_API_KEY", "secret")

    client = MessageIO()

    assert client.base_url == "https://message.example"
    assert client.api_key == "secret"
