import threading

from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1
import pytest

from feishu_io.events import IncomingMessage
from feishu_io.handlers import handle_incoming_message_sync
from feishu_io.listener import ListenerService, handle_lark_message_event
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
    assert store.pop_unread("test", 100)[0]["text"] == "hi"


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
