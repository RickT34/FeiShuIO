import sqlite3
import threading

import pytest

from message_io.domain import Destination, IncomingMessage, MessageContent, Sender
from message_io.store import MessageStore


def incoming(
    *,
    platform="feishu",
    account_id="default",
    conversation_id="chat-1",
    external_id="external-1",
    text="hello",
):
    return IncomingMessage(
        destination=Destination(platform, account_id, conversation_id),
        external_message_id=external_id,
        sender=Sender(id="user-1", name="Rick"),
        content=MessageContent(type="text", text=text),
        raw={"provider": "private"},
    )


def test_messages_received_before_binding_become_available_without_rewriting(tmp_path):
    store = MessageStore(str(tmp_path / "messages.db"))
    message = incoming()

    assert store.add_message_once(message) is True
    assert store.bind_destination(alias="ops", destination=message.destination) is True

    messages, _ = store.receive("ops", 10)
    assert messages == [
        {
            "message_id": 1,
            "sender": {"id": "user-1", "name": "Rick"},
            "content": {"type": "text", "text": "hello"},
            "received_at": messages[0]["received_at"],
        }
    ]
    assert "provider" not in str(messages)


def test_external_ids_are_namespaced_by_platform_and_account(tmp_path):
    store = MessageStore(str(tmp_path / "messages.db"))
    feishu = incoming(platform="feishu", conversation_id="same", text="from feishu")
    slack = incoming(platform="slack", conversation_id="same", text="from slack")
    second_account = incoming(
        platform="feishu",
        account_id="secondary",
        conversation_id="same",
        text="from second account",
    )
    store.bind_destination(alias="feishu-ops", destination=feishu.destination)
    store.bind_destination(alias="slack-ops", destination=slack.destination)
    store.bind_destination(alias="feishu-secondary", destination=second_account.destination)

    assert store.add_message_once(feishu) is True
    assert store.add_message_once(slack) is True
    assert store.add_message_once(second_account) is True

    assert store.receive("feishu-ops", 10)[0][0]["content"]["text"] == "from feishu"
    assert store.receive("slack-ops", 10)[0][0]["content"]["text"] == "from slack"
    assert (
        store.receive("feishu-secondary", 10)[0][0]["content"]["text"]
        == "from second account"
    )


def test_duplicate_event_is_idempotent_within_one_platform_account(tmp_path):
    store = MessageStore(str(tmp_path / "messages.db"))
    message = incoming()

    assert store.add_message_once(message) is True
    assert store.add_message_once(message) is False


def test_same_alias_resolves_all_destinations_and_rebinding_is_local(tmp_path):
    store = MessageStore(str(tmp_path / "messages.db"))
    old = Destination("feishu", "default", "old-chat")
    new = Destination("slack", "workspace-a", "new-channel")
    store.bind_destination(alias="ops", destination=old)

    assert store.bind_destination(alias="ops", destination=new) is True
    assert store.resolve_targets("ops") == [old, new]

    assert store.bind_destination(alias="other", destination=new) is True
    assert store.resolve_targets("ops") == [old]
    assert store.resolve_targets("other") == [new]


def test_event_bind_allows_same_alias_on_multiple_conversations(tmp_path):
    store = MessageStore(str(tmp_path / "messages.db"))
    first = Destination("feishu", "default", "chat-1")
    second = Destination("feishu", "default", "chat-2")

    assert store.bind_destination_once(
        external_message_id="bind-1", destination=first, alias="ops"
    ) == (True, True, 1)
    assert store.bind_destination_once(
        external_message_id="bind-2", destination=second, alias="ops"
    ) == (True, True, 2)

    assert store.resolve_targets("ops") == [first, second]


def test_binding_queries_return_current_alias_and_aggregate_counts(tmp_path):
    store = MessageStore(str(tmp_path / "messages.db"))
    first = Destination("feishu", "default", "chat-1")
    second = Destination("slack", "workspace", "channel-1")
    third = Destination("feishu", "default", "chat-2")
    store.bind_destination(alias="ops", destination=first)
    store.bind_destination(alias="ops", destination=second)
    store.bind_destination(alias="alerts", destination=third)

    assert store.destination_alias(first) == "ops"
    assert store.destination_alias(Destination("feishu", "default", "missing")) is None
    assert store.list_bindings() == [("alerts", 1), ("ops", 2)]


def test_unbind_removes_only_current_destination_and_is_event_idempotent(tmp_path):
    store = MessageStore(str(tmp_path / "messages.db"))
    first = Destination("feishu", "default", "chat-1")
    second = Destination("feishu", "default", "chat-2")
    store.bind_destination(alias="ops", destination=first)
    store.bind_destination(alias="ops", destination=second)

    assert store.unbind_destination_once(
        external_message_id="unbind-1", destination=first
    ) == (True, "ops", 1)
    assert store.unbind_destination_once(
        external_message_id="unbind-1", destination=first
    ) == (False, None, 0)
    assert store.resolve_targets("ops") == [second]


def test_receive_merges_same_alias_in_global_order_with_aggregate_limit(tmp_path):
    store = MessageStore(str(tmp_path / "messages.db"))
    first = incoming(conversation_id="chat-1", external_id="one", text="one")
    second = incoming(conversation_id="chat-2", external_id="two", text="two")
    third = incoming(conversation_id="chat-1", external_id="three", text="three")
    store.bind_destination(alias="ops", destination=first.destination)
    store.bind_destination(alias="ops", destination=second.destination)
    for message in (first, second, third):
        store.add_message_once(message)

    messages, _ = store.receive("ops", 2)

    assert [message["content"]["text"] for message in messages] == ["one", "two"]
    assert [message["content"]["text"] for message in store.receive("ops", 2)[0]] == [
        "three"
    ]


def test_lease_requires_current_token_and_redelivers_after_expiry(tmp_path):
    db = tmp_path / "messages.db"
    store = MessageStore(str(db))
    message = incoming()
    store.bind_destination(alias="ops", destination=message.destination)
    store.add_message_once(message)

    messages, first_token = store.receive("ops", 10, ack=False, lease_seconds=300)
    assert first_token and len(first_token) == 32
    assert store.ack_messages("ops", [messages[0]["message_id"]], lease_token="x" * 32) == []

    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE messages SET lease_until = datetime('now', '-1 second')")
    redelivered, second_token = store.receive("ops", 10, ack=False)

    assert redelivered[0]["message_id"] == messages[0]["message_id"]
    assert second_token != first_token
    assert store.ack_messages(
        "ops", [messages[0]["message_id"]], lease_token=first_token
    ) == []
    assert len(
        store.ack_messages(
            "ops", [messages[0]["message_id"]], lease_token=second_token
        )
    ) == 1


def test_lease_ack_survives_destination_rebinding(tmp_path):
    store = MessageStore(str(tmp_path / "messages.db"))
    message = incoming()
    store.bind_destination(alias="ops", destination=message.destination)
    store.add_message_once(message)

    messages, lease_token = store.receive("ops", 10, ack=False)
    store.bind_destination(alias="other", destination=message.destination)

    assert lease_token is not None
    assert len(
        store.ack_messages(
            "ops", [messages[0]["message_id"]], lease_token=lease_token
        )
    ) == 1


def test_unknown_target_is_not_silently_created(tmp_path):
    store = MessageStore(str(tmp_path / "messages.db"))

    with pytest.raises(KeyError, match="missing"):
        store.receive("missing", 10)


def test_concurrent_consumers_cannot_lease_same_messages_across_destinations(tmp_path):
    store = MessageStore(str(tmp_path / "messages.db"))
    destinations = [
        Destination("feishu", "default", "chat-1"),
        Destination("slack", "workspace", "channel-1"),
    ]
    for destination in destinations:
        store.bind_destination(alias="ops", destination=destination)
    for index in range(20):
        store.add_message_once(
            incoming(
                platform=destinations[index % 2].platform,
                account_id=destinations[index % 2].account_id,
                conversation_id=destinations[index % 2].conversation_id,
                external_id=f"external-{index}",
                text=str(index),
            )
        )
    barrier = threading.Barrier(2)
    batches = []
    errors = []

    def receive_batch():
        try:
            barrier.wait(timeout=2)
            batches.append(store.receive("ops", 20, ack=False)[0])
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=receive_batch) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert errors == []
    claimed_ids = [message["message_id"] for batch in batches for message in batch]
    assert len(claimed_ids) == 20
    assert len(set(claimed_ids)) == 20


def test_legacy_schema_requires_explicit_migration(tmp_path):
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE group_bindings(alias TEXT, chat_id TEXT)")
        conn.execute("PRAGMA user_version = 3")

    with pytest.raises(RuntimeError, match="migrate_v3_to_v5.py"):
        MessageStore(str(db))
