from fastapi.testclient import TestClient
import pytest

from message_io.config import Settings, get_settings
from message_io.domain import Destination, IncomingMessage, MessageContent, Sender
from message_io.platforms.feishu import FeishuAdapter
from message_io.platforms.base import PlatformError
from message_io.platforms.registry import PlatformRegistry
from message_io.server import app, get_platforms, get_store
from message_io.store import MessageStore


class FakeAdapter:
    platform = "feishu"
    account_id = "default"

    def __init__(self):
        self.sent = []
        self.marked = []

    async def send(self, *, destination, content):
        self.sent.append((destination, content))

    async def mark_delivered(self, *, external_message_id):
        self.marked.append(external_message_id)


@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def settings(tmp_path, **overrides):
    values = {
        "MESSAGE_IO_API_KEY": "secret-key",
        "MESSAGE_IO_DB": str(tmp_path / "messages.db"),
        "FEISHU_APP_ID": "cli_test",
        "FEISHU_APP_SECRET": "secret",
        "FEISHU_LISTENER_ENABLED": False,
        "FEISHU_MARK_DELIVERED_REACTION": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def make_client(tmp_path, *, adapter=None, listener=None, configured_settings=None):
    configured_settings = configured_settings or settings(tmp_path)
    store = MessageStore(configured_settings.db_path)
    adapter = adapter or FakeAdapter()
    platforms = PlatformRegistry()
    platforms.register(adapter, listener=listener)
    app.dependency_overrides[get_settings] = lambda: configured_settings
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_platforms] = lambda: platforms
    return TestClient(app), store, adapter


def bind_with_message(store, *, text="hello", external_id="om_1"):
    destination = Destination("feishu", "default", "oc_1")
    store.bind_destination(alias="ops", destination=destination)
    store.add_message_once(
        IncomingMessage(
            destination=destination,
            external_message_id=external_id,
            sender=Sender(id="ou_1", name="Rick"),
            content=MessageContent(type="text", text=text),
            raw={"event": {"provider_secret": True}},
        )
    )
    return destination


def test_send_routes_common_content_through_adapter(tmp_path):
    client, store, adapter = make_client(tmp_path)
    destination = Destination("feishu", "default", "oc_1")
    store.bind_destination(alias="ops", destination=destination)

    response = client.post(
        "/messages/send",
        headers={"X-API-Key": "secret-key"},
        json={
            "target": "ops",
            "content": {"type": "markdown", "text": "**done**"},
        },
    )
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"ok": True, "target": "ops", "sent": 1}
    assert adapter.sent == [
        (destination, MessageContent(type="markdown", text="**done**"))
    ]


def test_platform_send_failure_is_reported_as_bad_gateway(tmp_path):
    class FailingAdapter(FakeAdapter):
        async def send(self, *, destination, content):
            raise PlatformError("provider unavailable")

    client, store, _ = make_client(tmp_path, adapter=FailingAdapter())
    store.bind_destination(
        alias="ops", destination=Destination("feishu", "default", "oc_1")
    )

    response = client.post(
        "/messages/send",
        headers={"X-API-Key": "secret-key"},
        json={"target": "ops", "content": {"type": "text", "text": "hello"}},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == {"target": "ops", "sent": 0, "failed": 1}


def test_send_broadcasts_to_every_destination(tmp_path):
    configured = settings(tmp_path)
    store = MessageStore(configured.db_path)
    feishu = FakeAdapter()
    slack = FakeAdapter()
    slack.platform = "slack"
    slack.account_id = "workspace"
    platforms = PlatformRegistry()
    platforms.register(feishu)
    platforms.register(slack)
    feishu_destination = Destination("feishu", "default", "oc_1")
    slack_destination = Destination("slack", "workspace", "channel_1")
    store.bind_destination(alias="ops", destination=feishu_destination)
    store.bind_destination(alias="ops", destination=slack_destination)
    app.dependency_overrides[get_settings] = lambda: configured
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_platforms] = lambda: platforms

    response = TestClient(app).post(
        "/messages/send",
        headers={"X-API-Key": "secret-key"},
        json={"target": "ops", "content": {"type": "text", "text": "hello"}},
    )

    assert response.json() == {"ok": True, "target": "ops", "sent": 2}
    assert [item[0] for item in feishu.sent + slack.sent] == [
        feishu_destination,
        slack_destination,
    ]


def test_partial_broadcast_failure_still_attempts_all_destinations(tmp_path):
    class FailingAdapter(FakeAdapter):
        async def send(self, *, destination, content):
            self.sent.append((destination, content))
            raise PlatformError("provider-specific secret")

    configured = settings(tmp_path)
    store = MessageStore(configured.db_path)
    failing = FailingAdapter()
    successful = FakeAdapter()
    successful.platform = "slack"
    successful.account_id = "workspace"
    platforms = PlatformRegistry()
    platforms.register(failing)
    platforms.register(successful)
    store.bind_destination(
        alias="ops", destination=Destination("feishu", "default", "oc_1")
    )
    store.bind_destination(
        alias="ops", destination=Destination("slack", "workspace", "channel_1")
    )
    app.dependency_overrides[get_settings] = lambda: configured
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_platforms] = lambda: platforms

    response = TestClient(app).post(
        "/messages/send",
        headers={"X-API-Key": "secret-key"},
        json={"target": "ops", "content": {"type": "text", "text": "hello"}},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == {"target": "ops", "sent": 1, "failed": 1}
    assert len(failing.sent) == 1
    assert len(successful.sent) == 1


def test_unknown_content_type_is_rejected_before_platform_adapter(tmp_path):
    client, store, adapter = make_client(tmp_path)
    store.bind_destination(
        alias="ops", destination=Destination("feishu", "default", "oc_1")
    )

    response = client.post(
        "/messages/send",
        headers={"X-API-Key": "secret-key"},
        json={"target": "ops", "content": {"type": "feishu-card", "text": "hi"}},
    )

    assert response.status_code == 422
    assert adapter.sent == []


def test_receive_never_exposes_provider_payload_or_provider_field_names(tmp_path):
    client, store, adapter = make_client(tmp_path)
    bind_with_message(store)

    response = client.post(
        "/messages/receive",
        headers={"X-API-Key": "secret-key"},
        json={"target": "ops"},
    )
    app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["messages"][0] == {
        "message_id": 1,
        "sender": {"id": "ou_1", "name": "Rick"},
        "content": {"type": "text", "text": "hello"},
        "received_at": payload["messages"][0]["received_at"],
    }
    serialized = response.text
    assert "external_message_id" not in serialized
    assert "raw" not in serialized
    assert "platform" not in serialized
    assert adapter.marked == ["om_1"]


def test_lease_and_acknowledge_preserve_reliable_delivery(tmp_path):
    client, store, adapter = make_client(tmp_path)
    bind_with_message(store)

    leased = client.post(
        "/messages/receive",
        headers={"X-API-Key": "secret-key"},
        json={"target": "ops", "ack": False},
    ).json()
    assert adapter.marked == []
    store.bind_destination(
        alias="other", destination=Destination("feishu", "default", "oc_1")
    )
    acknowledged = client.post(
        "/messages/acknowledge",
        headers={"X-API-Key": "secret-key"},
        json={
            "target": "ops",
            "message_ids": [leased["messages"][0]["message_id"]],
            "lease_token": leased["lease_token"],
        },
    )
    app.dependency_overrides.clear()

    assert leased["ack_required"] is True
    assert "lease_token" not in leased["messages"][0]
    assert acknowledged.json() == {"ok": True, "target": "ops", "acked": 1}
    assert adapter.marked == ["om_1"]


def test_unknown_target_and_missing_auth_are_rejected(tmp_path):
    client, _, _ = make_client(tmp_path)

    unauthorized = client.post(
        "/messages/receive", json={"target": "missing"}
    )
    missing = client.post(
        "/messages/receive",
        headers={"X-API-Key": "secret-key"},
        json={"target": "missing"},
    )
    app.dependency_overrides.clear()

    assert unauthorized.status_code == 401
    assert missing.status_code == 404
    assert missing.json()["detail"].startswith("unknown target")


def test_ready_reports_registered_platforms_without_provider_details_in_messages(tmp_path):
    client, _, _ = make_client(tmp_path)

    response = client.get("/ready")
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["checks"]["messaging"] == {
        "ok": True,
        "configured": 1,
        "connected": 1,
    }
    assert "feishu" not in response.text.lower()


def test_ready_fails_when_registered_listener_is_disconnected(tmp_path):
    class OfflineListener:
        def status(self):
            return {"running": True, "connected": False, "last_error": "auth failed"}

    client, _, _ = make_client(tmp_path, listener=OfflineListener())

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"]["checks"]["messaging"] == {
        "ok": False,
        "configured": 1,
        "connected": 0,
    }


def test_settings_allow_deployment_without_feishu_credentials():
    configured = Settings(
        _env_file=None,
        MESSAGE_IO_API_KEY="secret",
        FEISHU_ENABLED=False,
    )

    assert configured.feishu_enabled is False


def test_settings_require_credentials_for_enabled_feishu():
    with pytest.raises(ValueError, match="required when FEISHU_ENABLED=true"):
        Settings(_env_file=None, MESSAGE_IO_API_KEY="secret", FEISHU_ENABLED=True)


def test_feishu_http_callback_uses_adapter_boundary(tmp_path):
    configured = settings(
        tmp_path,
        FEISHU_EVENT_VERIFY_TOKEN="verify",
    )
    real_adapter = FeishuAdapter(
        account_id="default",
        app_id="cli_test",
        app_secret="secret",
        reaction_emoji=None,
    )
    client, store, _ = make_client(
        tmp_path, adapter=real_adapter, configured_settings=configured
    )

    response = client.post(
        "/platforms/feishu/events",
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
                    "chat_id": "oc_1",
                    "message_type": "text",
                    "content": '{"text":"before bind"}',
                },
            },
        },
    )
    app.dependency_overrides.clear()

    assert response.status_code == 200
    store.bind_destination(
        alias="ops", destination=Destination("feishu", "default", "oc_1")
    )
    assert store.receive("ops", 10)[0][0]["content"]["text"] == "before bind"


def test_feishu_http_callback_is_disabled_without_verification_token(tmp_path):
    configured = settings(tmp_path, FEISHU_EVENT_VERIFY_TOKEN=None)
    client, _, _ = make_client(tmp_path, configured_settings=configured)

    response = client.post("/platforms/feishu/events", json={"schema": "2.0"})

    assert response.status_code == 503
    assert "disabled" in response.json()["detail"]
