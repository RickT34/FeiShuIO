import asyncio
import threading

from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1
import pytest

from message_io.domain import Destination, IncomingMessage, MessageContent, Sender
from message_io.handlers import handle_incoming_message_sync
from message_io.listener import ListenerService, ManagedWsClient, handle_lark_message_event
from message_io.store import MessageStore


class FakeFeishuAdapter:
    platform = "feishu"
    account_id = "default"

    def __init__(self):
        self.sent = []

    async def send(self, *, destination, content):
        self.sent.append((destination, content))

    async def mark_delivered(self, *, external_message_id):
        pass


def lark_event(message_id, text, *, chat_id="oc_test"):
    return P2ImMessageReceiveV1(
        {
            "schema": "2.0",
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_id": message_id,
                    "chat_id": chat_id,
                    "message_type": "text",
                    "content": '{"text":"' + text + '"}',
                },
            },
        }
    )


def test_feishu_listener_normalizes_binding_and_message(tmp_path):
    store = MessageStore(str(tmp_path / "messages.sqlite3"))
    adapter = FakeFeishuAdapter()

    handle_lark_message_event(lark_event("om_bind", "/bind ops"), store=store, adapter=adapter)
    handle_lark_message_event(lark_event("om_2", "hi"), store=store, adapter=adapter)

    assert store.resolve_targets("ops") == [
        Destination("feishu", "default", "oc_test")
    ]
    assert adapter.sent == [
        (
            Destination("feishu", "default", "oc_test"),
            MessageContent(
                type="markdown",
                text="已绑定当前会话为 `ops`。该 alias 共绑定 1 个会话。",
            ),
        )
    ]
    assert store.receive("ops", 100)[0][0]["content"]["text"] == "hi"


def test_duplicate_bind_event_has_no_duplicate_confirmation(tmp_path):
    store = MessageStore(str(tmp_path / "messages.sqlite3"))
    adapter = FakeFeishuAdapter()
    event = lark_event("om_bind", "/bind ops")

    handle_lark_message_event(event, store=store, adapter=adapter)
    handle_lark_message_event(event, store=store, adapter=adapter)

    assert len(adapter.sent) == 1


def test_repeating_bind_as_a_new_command_reports_current_count(tmp_path):
    store = MessageStore(str(tmp_path / "messages.sqlite3"))
    adapter = FakeFeishuAdapter()

    handle_lark_message_event(
        lark_event("bind-1", "/bind ops"), store=store, adapter=adapter
    )
    handle_lark_message_event(
        lark_event("bind-2", "/bind ops"), store=store, adapter=adapter
    )

    assert len(adapter.sent) == 2
    assert adapter.sent[-1][1].text.endswith("该 alias 共绑定 1 个会话。")


def test_listener_propagates_storage_failure(tmp_path, monkeypatch):
    store = MessageStore(str(tmp_path / "messages.sqlite3"))
    monkeypatch.setattr(
        store,
        "add_message_once",
        lambda _message: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        handle_lark_message_event(
            lark_event("om_1", "hello"), store=store, adapter=FakeFeishuAdapter()
        )


@pytest.mark.asyncio
async def test_sync_handler_can_run_while_event_loop_exists(tmp_path):
    store = MessageStore(str(tmp_path / "messages.sqlite3"))
    message = IncomingMessage(
        destination=Destination("feishu", "default", "oc_test"),
        external_message_id="om_1",
        sender=Sender(id="ou_1"),
        content=MessageContent(type="text", text="/bind ops"),
        raw={},
    )

    result = handle_incoming_message_sync(message=message, store=store)

    assert result == {
        "ok": True,
        "bound": "ops",
        "changed": True,
        "destination_count": 1,
    }
    assert store.resolve_targets("ops") == [message.destination]


def test_bind_confirmation_reports_shared_alias_destination_count(tmp_path):
    store = MessageStore(str(tmp_path / "messages.sqlite3"))
    adapter = FakeFeishuAdapter()
    handle_lark_message_event(
        lark_event("bind-1", "/bind ops", chat_id="chat-1"),
        store=store,
        adapter=adapter,
    )

    handle_lark_message_event(
        lark_event("bind-2", "/bind ops", chat_id="chat-2"),
        store=store,
        adapter=adapter,
    )

    assert adapter.sent[-1][1].text.endswith("该 alias 共绑定 2 个会话。")


def test_help_lists_every_binding_command_once_for_duplicate_event(tmp_path):
    store = MessageStore(str(tmp_path / "messages.sqlite3"))
    adapter = FakeFeishuAdapter()
    event = lark_event("help-1", "/help")

    handle_lark_message_event(event, store=store, adapter=adapter)
    handle_lark_message_event(event, store=store, adapter=adapter)

    assert len(adapter.sent) == 1
    assert adapter.sent[0][1].text == """**MessageIO 指令**
- `/bind <alias>`：绑定当前会话
- `/bind`：查看当前会话的绑定
- `/binds`：查看所有 alias 及其会话数
- `/unbind`：删除当前会话的绑定
- `/help`：查看帮助"""


def test_current_bind_command_shows_current_conversation_alias(tmp_path):
    store = MessageStore(str(tmp_path / "messages.sqlite3"))
    adapter = FakeFeishuAdapter()
    store.bind_destination(
        alias="ops", destination=Destination("feishu", "default", "oc_test")
    )

    handle_lark_message_event(
        lark_event("current-1", "/bind"), store=store, adapter=adapter
    )

    assert adapter.sent[0][1].text == "当前会话绑定为 `ops`。"


def test_all_bind_command_shows_only_aliases_and_aggregate_counts(tmp_path):
    store = MessageStore(str(tmp_path / "messages.sqlite3"))
    adapter = FakeFeishuAdapter()
    store.bind_destination(
        alias="ops", destination=Destination("feishu", "default", "oc_test")
    )
    store.bind_destination(
        alias="ops", destination=Destination("slack", "workspace", "channel-1")
    )

    handle_lark_message_event(
        lark_event("list-1", "/binds"), store=store, adapter=adapter
    )

    assert adapter.sent[0][1].text == "**所有绑定**\n- `ops`：2 个会话"
    assert "feishu" not in adapter.sent[0][1].text
    assert "slack" not in adapter.sent[0][1].text


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("/bind", "当前会话尚未绑定 alias。"),
        ("/binds", "当前没有任何绑定。"),
        ("/unbind", "当前会话尚未绑定 alias。"),
    ],
)
def test_binding_management_commands_handle_empty_state(
    tmp_path, command, expected
):
    store = MessageStore(str(tmp_path / "messages.sqlite3"))
    adapter = FakeFeishuAdapter()

    handle_lark_message_event(
        lark_event(f"empty-{command}", command), store=store, adapter=adapter
    )

    assert adapter.sent[0][1].text == expected


def test_invalid_bind_command_returns_help_without_enqueuing_message(tmp_path):
    store = MessageStore(str(tmp_path / "messages.sqlite3"))
    adapter = FakeFeishuAdapter()

    handle_lark_message_event(
        lark_event("invalid-1", "/bind invalid alias"),
        store=store,
        adapter=adapter,
    )

    assert adapter.sent[0][1].text.startswith("指令格式不正确。")
    assert store.list_bindings() == []
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


def test_unbind_command_removes_only_current_conversation(tmp_path):
    store = MessageStore(str(tmp_path / "messages.sqlite3"))
    adapter = FakeFeishuAdapter()
    current = Destination("feishu", "default", "oc_test")
    other = Destination("feishu", "default", "other-chat")
    store.bind_destination(alias="ops", destination=current)
    store.bind_destination(alias="ops", destination=other)

    handle_lark_message_event(
        lark_event("unbind-1", "/unbind"), store=store, adapter=adapter
    )

    assert store.destination_alias(current) is None
    assert store.resolve_targets("ops") == [other]
    assert adapter.sent[0][1].text == (
        "已删除当前会话的绑定 `ops`。该 alias 还绑定 1 个会话。"
    )


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
