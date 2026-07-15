import threading

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
    acked = store.ack_messages(
        "test",
        [third[0]["message_id"]],
        lease_token=third[0]["lease_token"],
    )
    fourth = store.pop_unread("test", 100, ack=False, lease_seconds=60)

    assert len(first) == 1
    assert second == []
    assert third[0]["text"] == "hello"
    assert acked == [
        {"message_id": third[0]["message_id"], "external_message_id": "om_1"}
    ]
    assert fourth == []


def test_expired_lease_cannot_ack_a_new_lease(tmp_path):
    store = MessageStore(str(tmp_path / "messages.sqlite3"))
    store.add_message(
        IncomingMessage("oc_1", "om_1", None, None, "text", "hello", {})
    )

    first = store.pop_unread("oc_1", 1, ack=False, lease_seconds=60)[0]
    with store.connect() as conn:
        conn.execute(
            "UPDATE unread_messages SET lease_until = datetime('now', '-1 second')"
        )
    second = store.pop_unread("oc_1", 1, ack=False, lease_seconds=60)[0]

    stale_ack = store.ack_messages(
        "oc_1", [first["message_id"]], lease_token=first["lease_token"]
    )
    current_ack = store.ack_messages(
        "oc_1", [second["message_id"]], lease_token=second["lease_token"]
    )

    assert first["lease_token"] != second["lease_token"]
    assert stale_ack == []
    assert len(current_ack) == 1


def test_concurrent_leases_do_not_return_the_same_message(tmp_path):
    store = MessageStore(str(tmp_path / "messages.sqlite3"))
    for index in range(20):
        store.add_message(
            IncomingMessage(
                "oc_1", f"om_{index}", None, None, "text", str(index), {}
            )
        )

    barrier = threading.Barrier(2)
    results: list[list[dict]] = []
    errors: list[BaseException] = []

    def lease_messages() -> None:
        try:
            barrier.wait(timeout=2)
            results.append(store.pop_unread("oc_1", 20, ack=False, lease_seconds=60))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=lease_messages) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    claimed_ids = [
        {message["message_id"] for message in batch}
        for batch in results
    ]
    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 2
    assert claimed_ids[0].isdisjoint(claimed_ids[1])
    assert len(claimed_ids[0] | claimed_ids[1]) == 20


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


def test_rebinding_migrates_legacy_pending_messages_to_chat_id(tmp_path):
    store = MessageStore(str(tmp_path / "messages.sqlite3"))
    store.bind_group(alias="old", chat_id="oc_1")
    store.add_message(
        IncomingMessage(
            group_id="old",
            external_message_id="om_1",
            sender_id=None,
            sender_name=None,
            message_type="text",
            text="pending",
            raw={"event": {"message": {"chat_id": "oc_1"}}},
        )
    )

    store.bind_group(alias="new", chat_id="oc_1")

    assert store.resolve_alias("old") is None
    assert store.pop_unread("oc_1", 10)[0]["text"] == "pending"


def test_schema_upgrade_recovers_chat_id_from_raw_event(tmp_path):
    db_path = str(tmp_path / "messages.sqlite3")
    store = MessageStore(db_path)
    store.add_message(
        IncomingMessage(
            group_id="deleted-alias",
            external_message_id="om_1",
            sender_id=None,
            sender_name=None,
            message_type="text",
            text="pending",
            raw={"event": {"message": {"chat_id": "oc_1"}}},
        )
    )
    with store.connect() as conn:
        conn.execute("PRAGMA user_version = 2")

    upgraded = MessageStore(db_path)

    assert upgraded.pop_unread("oc_1", 10)[0]["text"] == "pending"


def test_mark_processed_is_idempotent(tmp_path):
    store = MessageStore(str(tmp_path / "messages.sqlite3"))

    assert store.mark_processed("om_1") is True
    assert store.mark_processed("om_1") is False
