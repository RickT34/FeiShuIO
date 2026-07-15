import base64
import hashlib
import json
import os

from Crypto.Cipher import AES
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


def make_client(
    tmp_path,
    *,
    verify_token="verify",
    encrypt_key="",
    enable_ws="false",
):
    os.environ["FEISHU_IO_API_KEY"] = "secret-key"
    os.environ["FEISHU_IO_DB"] = str(tmp_path / "messages.sqlite3")
    os.environ["FEISHU_APP_ID"] = "cli_test"
    os.environ["FEISHU_APP_SECRET"] = "secret"
    os.environ["FEISHU_EVENT_VERIFY_TOKEN"] = verify_token
    os.environ["FEISHU_EVENT_ENCRYPT_KEY"] = encrypt_key
    os.environ["FEISHU_IO_ENABLE_WS"] = enable_ws

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
            "schema": "2.0",
            "header": {
                "token": "verify",
                "event_type": "im.message.receive_v1",
            },
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
            "schema": "2.0",
            "header": {
                "token": "verify",
                "event_type": "im.message.receive_v1",
            },
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
    assert bind.json() == {"msg": "success"}
    assert send.status_code == 200
    assert message.status_code == 200
    assert fake.sent == [
        ("oc_test", "已绑定当前群为 `test`。"),
        ("oc_test", "hello **world**"),
    ]
    assert recv.status_code == 200
    assert recv.json()["messages"][0]["text"] == "hi"
    assert recv.json()["messages"][0]["external_message_id"] == "om_2"
    assert fake.reactions == [("om_2", "Get")]


def test_recv_unread_can_require_explicit_ack(tmp_path):
    client, fake = make_client(tmp_path)
    client.post(
        "/feishu/events",
        json={
            "schema": "2.0",
            "header": {
                "token": "verify",
                "event_type": "im.message.receive_v1",
            },
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
    client.post(
        "/feishu/events",
        json={
            "schema": "2.0",
            "header": {
                "token": "verify",
                "event_type": "im.message.receive_v1",
            },
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_2"}},
                "message": {
                    "message_id": "om_2",
                    "chat_id": "oc_test",
                    "message_type": "text",
                    "content": '{"text":"leased"}',
                },
            },
        },
    )

    leased = client.post(
        "/recv_unread",
        headers={"X-API-Key": "secret-key"},
        json={"id": "test", "ack": False},
    )
    empty_while_leased = client.post(
        "/recv_unread",
        headers={"X-API-Key": "secret-key"},
        json={"id": "test", "ack": False},
    )
    message_id = leased.json()["messages"][0]["message_id"]
    lease_token = leased.json()["messages"][0]["lease_token"]
    ack = client.post(
        "/ack_messages",
        headers={"X-API-Key": "secret-key"},
        json={
            "id": "test",
            "message_ids": [message_id],
            "lease_token": lease_token,
        },
    )
    empty_after_ack = client.post(
        "/recv_unread",
        headers={"X-API-Key": "secret-key"},
        json={"id": "test", "ack": False},
    )

    app.dependency_overrides.clear()

    assert leased.status_code == 200
    assert leased.json()["ack_required"] is True
    assert leased.json()["messages"][0]["text"] == "leased"
    assert empty_while_leased.json()["messages"] == []
    assert ack.status_code == 200
    assert ack.json()["acked"] == 1
    assert empty_after_ack.json()["messages"] == []
    assert ("om_2", "Get") in fake.reactions


def test_ready_reports_database_status(tmp_path):
    client, _ = make_client(tmp_path)

    response = client.get("/ready")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["checks"]["database"]["ok"] is True


def test_ready_rejects_live_retry_thread_without_connection(tmp_path, monkeypatch):
    client, _ = make_client(tmp_path, enable_ws="true")
    monkeypatch.setattr(
        "feishu_io.server.listener_status",
        lambda: {"running": True, "connected": False, "last_error": "auth failed"},
    )

    response = client.get("/ready")

    app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["detail"]["checks"]["listener"]["connected"] is False


def test_business_api_requires_key(tmp_path):
    client, _ = make_client(tmp_path)

    response = client.post("/recv_unread", json={"id": "test"})

    app.dependency_overrides.clear()

    assert response.status_code == 401


def test_http_event_callback_is_disabled_without_verification_token(tmp_path):
    client, _ = make_client(tmp_path, verify_token="")

    response = client.post("/feishu/events", json={"schema": "2.0"})

    app.dependency_overrides.clear()

    assert response.status_code == 503
    assert "disabled" in response.json()["detail"]


def test_http_event_callback_decrypts_and_verifies_signed_payload(tmp_path):
    encrypt_key = "encrypt-secret"
    client, fake = make_client(tmp_path, encrypt_key=encrypt_key)
    event = {
        "schema": "2.0",
        "header": {
            "token": "verify",
            "event_type": "im.message.receive_v1",
        },
        "event": {
            "sender": {"sender_id": {"open_id": "ou_1"}},
            "message": {
                "message_id": "om_bind",
                "chat_id": "oc_test",
                "message_type": "text",
                "content": '{"text":"/bind test"}',
            },
        },
    }
    plaintext = json.dumps(event).encode()
    padding = AES.block_size - len(plaintext) % AES.block_size
    padded = plaintext + bytes([padding]) * padding
    iv = b"0" * AES.block_size
    cipher = AES.new(hashlib.sha256(encrypt_key.encode()).digest(), AES.MODE_CBC, iv)
    encrypted = base64.b64encode(iv + cipher.encrypt(padded)).decode()
    body = json.dumps({"encrypt": encrypted}).encode()
    timestamp = "1783940000"
    nonce = "test-nonce"
    signature = hashlib.sha256(
        (timestamp + nonce + encrypt_key).encode() + body
    ).hexdigest()

    response = client.post(
        "/feishu/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Lark-Request-Timestamp": timestamp,
            "X-Lark-Request-Nonce": nonce,
            "X-Lark-Signature": signature,
        },
    )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert fake.sent == [("oc_test", "已绑定当前群为 `test`。")]


def test_message_received_before_binding_is_available_after_binding(tmp_path):
    client, _ = make_client(tmp_path)

    message = client.post(
        "/feishu/events",
        json={
            "schema": "2.0",
            "header": {
                "token": "verify",
                "event_type": "im.message.receive_v1",
            },
            "event": {
                "sender": {"sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_id": "om_1",
                    "chat_id": "oc_test",
                    "message_type": "text",
                    "content": '{"text":"before bind"}',
                },
            },
        },
    )
    bind = client.post(
        "/feishu/events",
        json={
            "schema": "2.0",
            "header": {
                "token": "verify",
                "event_type": "im.message.receive_v1",
            },
            "event": {
                "sender": {"sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_id": "om_bind",
                    "chat_id": "oc_test",
                    "message_type": "text",
                    "content": '{"text":"/bind test"}',
                },
            },
        },
    )
    received = client.post(
        "/recv_unread",
        headers={"X-API-Key": "secret-key"},
        json={"id": "test"},
    )

    app.dependency_overrides.clear()

    assert message.status_code == 200
    assert bind.status_code == 200
    assert received.json()["messages"][0]["text"] == "before bind"
