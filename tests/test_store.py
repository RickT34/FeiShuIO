from feishu_io.events import IncomingMessage
from feishu_io.store import MessageStore


def test_pop_unread_marks_messages_delivered(tmp_path):
    store = MessageStore(str(tmp_path / "messages.sqlite3"))
    store.add_message(
        IncomingMessage(
            group_id="test",
            external_message_id="om_1",
            sender_id="ou_1",
            sender_name="user",
            message_type="text",
            text="hello",
            raw={"event": "payload"},
        )
    )

    first = store.pop_unread("test", 100)
    second = store.pop_unread("test", 100)

    assert len(first) == 1
    assert first[0]["text"] == "hello"
    assert second == []


def test_pop_unread_can_lease_and_redeliver_expired_messages(tmp_path):
    store = MessageStore(str(tmp_path / "messages.sqlite3"))
    store.add_message(
        IncomingMessage(
            group_id="test",
            external_message_id="om_1",
            sender_id="ou_1",
            sender_name="user",
            message_type="text",
            text="hello",
            raw={"event": "payload"},
        )
    )

    first = store.pop_unread("test", 100, ack=False, lease_seconds=60)
    second = store.pop_unread("test", 100, ack=False, lease_seconds=60)

    with store.connect() as conn:
        conn.execute(
            """
            UPDATE unread_messages
            SET lease_until = datetime('now', '-1 second')
            WHERE message_id = ?
            """,
            (first[0]["message_id"],),
        )

    third = store.pop_unread("test", 100, ack=False, lease_seconds=60)
    acked = store.ack_messages("test", [third[0]["message_id"]])
    fourth = store.pop_unread("test", 100, ack=False, lease_seconds=60)

    assert len(first) == 1
    assert second == []
    assert third[0]["text"] == "hello"
    assert acked == [
        {"message_id": third[0]["message_id"], "external_message_id": "om_1"}
    ]
    assert fourth == []


def test_bind_group_resolves_alias_and_chat_id(tmp_path):
    store = MessageStore(str(tmp_path / "messages.sqlite3"))

    store.bind_group(alias="test", chat_id="oc_1")

    assert store.resolve_alias("test") == "oc_1"
    assert store.resolve_chat_id("oc_1") == "test"


def test_bind_group_replaces_old_alias_for_same_chat(tmp_path):
    store = MessageStore(str(tmp_path / "messages.sqlite3"))

    assert store.bind_group(alias="old", chat_id="oc_1") is True
    assert store.bind_group(alias="new", chat_id="oc_1") is True

    assert store.resolve_alias("old") is None
    assert store.resolve_alias("new") == "oc_1"
    assert store.resolve_chat_id("oc_1") == "new"


def test_mark_processed_is_idempotent(tmp_path):
    store = MessageStore(str(tmp_path / "messages.sqlite3"))

    assert store.mark_processed("om_1") is True
    assert store.mark_processed("om_1") is False
