import os
import tempfile

from fastapi.testclient import TestClient

from feishu_io.config import get_settings
from feishu_io.server import app, get_feishu_client, get_store


class FakeFeishuClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.reactions: list[tuple[str, str]] = []

    async def send_markdown(self, *, chat_id: str, text: str) -> dict:
        self.sent.append((chat_id, text))
        return {"code": 0, "msg": "ok"}

    async def add_reaction(self, *, message_id: str, emoji_type: str) -> dict:
        self.reactions.append((message_id, emoji_type))
        return {"code": 0, "msg": "ok"}


def make_client(tmp_path):
    os.environ["FEISHU_IO_API_KEY"] = "secret-key"
    os.environ["FEISHU_IO_DB"] = str(tmp_path / "messages.sqlite3")
    os.environ["FEISHU_APP_ID"] = "cli_test"
    os.environ["FEISHU_APP_SECRET"] = "secret"
    os.environ["FEISHU_EVENT_VERIFY_TOKEN"] = "verify"
    os.environ["FEISHU_IO_ENABLE_WS"] = "false"

    fake = FakeFeishuClient()
    get_settings.cache_clear()
    get_store.cache_clear()
    get_feishu_client.cache_clear()
    app.dependency_overrides[get_feishu_client] = lambda: fake
    return TestClient(app), fake


def test_bind_send_and_recv_flow(tmp_path):
    client, fake = make_client(tmp_path)

    bind = client.post(
        "/feishu/events",
        json={
            "header": {"token": "verify"},
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_id": "om_bind",
                    "chat_id": "oc_test",
                    "message_type": "text",
                    "content": '{"text":"/bind test"}',
                },
            },
        },
    )

    send = client.post(
        "/send_markdown",
        headers={"X-API-Key": "secret-key"},
        json={"id": "test", "text": "hello **world**"},
    )

    message = client.post(
        "/feishu/events",
        json={
            "header": {"token": "verify"},
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_2"}},
                "message": {
                    "message_id": "om_2",
                    "chat_id": "oc_test",
                    "message_type": "text",
                    "content": '{"text":"hi"}',
                },
            },
        },
    )

    recv = client.post(
        "/recv_unread",
        headers={"Authorization": "Bearer secret-key"},
        json={"id": "test"},
    )

    app.dependency_overrides.clear()

    assert bind.status_code == 200
    assert bind.json() == {"ok": True, "bound": "test", "changed": True}
    assert send.status_code == 200
    assert message.status_code == 200
    assert fake.sent == [
        ("oc_test", "已绑定当前群为 `test`。"),
        ("oc_test", "hello **world**"),
    ]
    assert recv.status_code == 200
    assert recv.json()["messages"][0]["text"] == "hi"
    assert recv.json()["messages"][0]["external_message_id"] == "om_2"
    assert fake.reactions == [("om_2", "OK")]


def test_business_api_requires_key(tmp_path):
    client, _ = make_client(tmp_path)

    response = client.post("/recv_unread", json={"id": "test"})

    app.dependency_overrides.clear()

    assert response.status_code == 401
