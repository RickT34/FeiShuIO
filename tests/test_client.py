import json
import stat

import httpx
import pytest

from feishu_io.client import FeishuIO, FeishuIOError
from feishu_io.client_config import load_client_config, save_client_config


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


def test_python_client_ack_includes_lease_token():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ack_messages"
        assert json.loads(request.content) == {
            "id": "test",
            "message_ids": [1, 2],
            "lease_token": "a" * 32,
        }
        return httpx.Response(200, json={"ok": True, "id": "test", "acked": 2})

    client = FeishuIO("http://feishuio.local", "secret", transport=make_transport(handler))

    response = client.ack_messages(
        "test", [1, 2], lease_token="a" * 32
    )

    assert response["acked"] == 2


def test_python_client_uses_persisted_config(tmp_path):
    config_path = tmp_path / "client.json"
    save_client_config(
        url="http://feishuio.local/",
        api_key="saved-secret",
        path=config_path,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-API-Key"] == "saved-secret"
        return httpx.Response(200, json={"ok": True})

    client = FeishuIO(config_path=config_path, transport=make_transport(handler))

    assert client.base_url == "http://feishuio.local"
    assert client.health() == {"ok": True}
    assert load_client_config(config_path).api_key == "saved-secret"
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


def test_explicit_client_values_override_environment_and_config(tmp_path, monkeypatch):
    config_path = tmp_path / "client.json"
    save_client_config(
        url="http://saved.local",
        api_key="saved-secret",
        path=config_path,
    )
    monkeypatch.setenv("FEISHU_IO_URL", "http://environment.local")
    monkeypatch.setenv("FEISHU_IO_API_KEY", "environment-secret")

    client = FeishuIO(
        "http://explicit.local/",
        "explicit-secret",
        config_path=config_path,
    )

    assert client.base_url == "http://explicit.local"
    assert client.api_key == "explicit-secret"


def test_client_wraps_connection_errors():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = FeishuIO("http://offline.local", "secret", transport=make_transport(handler))

    with pytest.raises(FeishuIOError, match="connection refused"):
        client.health()


@pytest.mark.parametrize("url", ["feishu.example.com", "", "ftp://example.com"])
def test_client_rejects_invalid_server_url(url):
    with pytest.raises(ValueError, match="absolute http"):
        FeishuIO(url, "secret")


def test_client_reports_corrupt_saved_config(tmp_path):
    config_path = tmp_path / "client.json"
    config_path.write_text("{", encoding="utf-8")

    with pytest.raises(ValueError, match="cannot read client config"):
        FeishuIO(config_path=config_path)
