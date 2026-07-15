import asyncio
import threading

from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1
import pytest

from feishu_io.events import IncomingMessage
from feishu_io.handlers import handle_incoming_message_sync
from feishu_io.listener import (
    ListenerService,
    ManagedWsClient,
    handle_lark_message_event,
)
from feishu_io.store import MessageStore


class FakeFeishuClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_markdown(self, *, chat_id: str, text: str) -> dict:
        self.sent.append((chat_id, text))
        return {"code": 0, "msg": "ok"}


def test_ws_event_handler_binds_and_stores_messages(tmp_path):
    store = MessageStore(str(tmp_path / "messages.sqlite3"))
    client = FakeFeishuClient()

    handle_lark_message_event(
        P2ImMessageReceiveV1(
            {
                "schema": "2.0",
                "header": {"event_type": "im.message.receive_v1"},
                "event": {
                    "sender": {
                        "sender_type": "user",
                        "sender_id": {"open_id": "ou_1"},
                    },
                    "message": {
                        "message_id": "om_bind",
                        "chat_id": "oc_test",
                        "message_type": "text",
                        "content": '{"text":"/bind test"}',
                    },
                },
            }
        ),
        store=store,
        client=client,
    )
    handle_lark_message_event(
        P2ImMessageReceiveV1(
            {
                "schema": "2.0",
                "header": {"event_type": "im.message.receive_v1"},
                "event": {
                    "sender": {
                        "sender_type": "user",
                        "sender_id": {"open_id": "ou_2"},
                    },
                    "message": {
                        "message_id": "om_2",
                        "chat_id": "oc_test",
                        "message_type": "text",
                        "content": '{"text":"hi"}',
                    },
                },
            }
        ),
        store=store,
        client=client,
    )

    assert store.resolve_alias("test") == "oc_test"
    assert client.sent == [("oc_test", "已绑定当前群为 `test`。")]
    assert store.pop_unread("oc_test", 100)[0]["text"] == "hi"


def test_ws_event_handler_deduplicates_bind_events(tmp_path):
    store = MessageStore(str(tmp_path / "messages.sqlite3"))
    client = FakeFeishuClient()
    event = P2ImMessageReceiveV1(
        {
            "schema": "2.0",
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {
                    "sender_type": "user",
                    "sender_id": {"open_id": "ou_1"},
                },
                "message": {
                    "message_id": "om_bind",
                    "chat_id": "oc_test",
                    "message_type": "text",
                    "content": '{"text":"/bind test"}',
                },
            },
        }
    )

    handle_lark_message_event(event, store=store, client=client)
    handle_lark_message_event(event, store=store, client=client)

    assert store.resolve_alias("test") == "oc_test"
    assert client.sent == [("oc_test", "已绑定当前群为 `test`。")]


def test_ws_event_handler_propagates_storage_failures(tmp_path, monkeypatch):
    store = MessageStore(str(tmp_path / "messages.sqlite3"))
    client = FakeFeishuClient()
    event = P2ImMessageReceiveV1(
        {
            "schema": "2.0",
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_id": "om_1",
                    "chat_id": "oc_test",
                    "message_type": "text",
                    "content": '{"text":"hello"}',
                },
            },
        }
    )
    monkeypatch.setattr(
        store,
        "add_message_once",
        lambda _message: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        handle_lark_message_event(event, store=store, client=client)


@pytest.mark.asyncio
async def test_sync_handler_can_run_while_event_loop_exists(tmp_path):
    store = MessageStore(str(tmp_path / "messages.sqlite3"))

    result = handle_incoming_message_sync(
        message=IncomingMessage(
            group_id="oc_test",
            external_message_id="om_1",
            sender_id="ou_1",
            sender_name="user",
            message_type="text",
            text="/bind test",
            raw={},
        ),
        store=store,
    )

    assert result == {"ok": True, "bound": "test", "changed": True}
    assert store.resolve_alias("test") == "oc_test"


def test_listener_service_can_restart_after_thread_exits(monkeypatch):
    service = ListenerService()
    started = 0

    def fake_run():
        nonlocal started
        started += 1

    monkeypatch.setattr(service, "run", fake_run)

    first = service.start()
    first.join(timeout=1)
    second = service.start()
    second.join(timeout=1)

    assert started == 2


def test_listener_service_does_not_start_second_live_thread(monkeypatch):
    service = ListenerService()
    entered = threading.Event()
    release = threading.Event()
    started = 0

    def fake_run():
        nonlocal started
        started += 1
        entered.set()
        release.wait(timeout=1)

    monkeypatch.setattr(service, "run", fake_run)

    first = service.start()
    assert entered.wait(timeout=1)
    second = service.start()
    release.set()
    first.join(timeout=1)

    assert first is second
    assert started == 1


def test_listener_status_is_not_connected_during_retry(monkeypatch):
    class RetrySettings:
        listener_retry_base_delay = 10.0
        listener_retry_max_delay = 10.0

    service = ListenerService(settings_factory=RetrySettings)
    attempted = threading.Event()

    def fail(_settings):
        attempted.set()
        raise RuntimeError("authentication failed")

    monkeypatch.setattr(service, "_run_once", fail)
    service.start()
    assert attempted.wait(timeout=1)

    status = service.status()
    service.stop()

    assert status["running"] is True
    assert status["connected"] is False
    assert status["last_error"] == "authentication failed"


def test_listener_stop_closes_client_and_joins_thread():
    service = ListenerService()
    release = threading.Event()

    class ClosableClient:
        def close(self):
            release.set()

    thread = threading.Thread(target=release.wait)
    service._ws_client = ClosableClient()
    service._thread = thread
    service._connected = True
    thread.start()

    service.stop(timeout=1)

    assert thread.is_alive() is False
    assert service.status()["connected"] is False


def test_managed_ws_client_start_and_close_complete_cleanly(monkeypatch):
    states = []
    connected = threading.Event()
    client = ManagedWsClient(
        "cli_test",
        "secret",
        event_handler=None,
        on_connection_change=lambda state, error: states.append((state, error)),
    )

    async def fake_connect():
        client._notify_connection(True, None)
        connected.set()

    async def fake_disconnect():
        client._notify_connection(False, None)

    async def fake_ping_loop():
        await asyncio.Event().wait()

    monkeypatch.setattr(client, "_connect", fake_connect)
    monkeypatch.setattr(client, "_disconnect", fake_disconnect)
    monkeypatch.setattr(client, "_ping_loop", fake_ping_loop)

    thread = threading.Thread(target=client.start)
    thread.start()
    assert connected.wait(timeout=1)
    client.close()
    thread.join(timeout=1)

    assert thread.is_alive() is False
    assert states == [(True, None), (False, None)]


def test_managed_ws_client_close_before_start_does_not_connect(monkeypatch):
    connected = False
    client = ManagedWsClient(
        "cli_test",
        "secret",
        event_handler=None,
        on_connection_change=lambda _state, _error: None,
    )

    async def fake_connect():
        nonlocal connected
        connected = True

    monkeypatch.setattr(client, "_connect", fake_connect)

    client.close()
    client.start()

    assert connected is False
