import importlib.util
import sqlite3
from pathlib import Path

from message_io.store import MessageStore


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_v3_to_v5.py"
SPEC = importlib.util.spec_from_file_location("message_io_migration", SCRIPT)
migration = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(migration)


def create_v3_database(path):
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE group_bindings (
                alias TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE processed_messages (
                external_message_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            );
            CREATE TABLE unread_messages (
                message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                external_message_id TEXT UNIQUE,
                group_id TEXT NOT NULL,
                sender_id TEXT,
                sender_name TEXT,
                message_type TEXT NOT NULL,
                text TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                delivered_at TEXT,
                lease_until TEXT,
                lease_token TEXT
            );
            INSERT INTO group_bindings VALUES (
                'ops', 'oc_1', '2026-01-01', '2026-01-02'
            );
            INSERT INTO unread_messages VALUES (
                7, 'om_1', 'oc_1', 'ou_1', 'Rick', 'text', 'continue',
                '{"provider":"private"}', '2026-01-03', NULL, NULL, NULL
            );
            INSERT INTO unread_messages VALUES (
                8, 'om_2', 'oc_1', 'ou_1', 'Rick', 'text', 'done',
                '{}', '2026-01-03', '2026-01-04', NULL, NULL
            );
            INSERT INTO unread_messages VALUES (
                9, 'om_3', 'oc_1', 'ou_1', 'Rick', 'text', 'leased',
                '{}', '2026-01-03', NULL, '2099-01-01', 'lease-token'
            );
            INSERT INTO processed_messages VALUES ('om_1', '2026-01-03');
            INSERT INTO processed_messages VALUES ('om_2', '2026-01-03');
            INSERT INTO processed_messages VALUES ('om_3', '2026-01-03');
            PRAGMA user_version = 3;
            """
        )


def test_migration_preserves_routing_ids_and_delivery_state(tmp_path):
    source = tmp_path / "v3.db"
    output = tmp_path / "v5.db"
    create_v3_database(source)

    counts = migration.migrate_database(source, output, account_id="primary")
    store = MessageStore(str(output))
    with sqlite3.connect(output) as conn:
        states = conn.execute(
            "SELECT message_id, delivered_at, lease_until, lease_token, lease_target "
            "FROM messages ORDER BY message_id"
        ).fetchall()
    messages, _ = store.receive("ops", 10)

    assert counts == {"bindings": 1, "messages": 3, "processed_messages": 3}
    assert messages[0]["message_id"] == 7
    assert messages[0]["content"] == {"type": "text", "text": "continue"}
    assert store.resolve_targets("ops")[0].account_id == "primary"
    assert states == [
        (7, None, None, None, None),
        (8, "2026-01-04", None, None, None),
        (9, None, "2099-01-01", "lease-token", "ops"),
    ]
    assert len(store.ack_messages("ops", [9], lease_token="lease-token")) == 1
    assert source.exists()


def test_env_migration_renames_generic_settings_without_overwriting_source(tmp_path):
    source = tmp_path / ".env"
    output = tmp_path / ".env.v5"
    source.write_text(
        "FEISHU_IO_API_KEY=secret\n"
        "FEISHU_IO_DB=old.db\n"
        "FEISHU_APP_ID=cli_test\n"
        "FEISHU_IO_ENABLE_WS=true\n",
        encoding="utf-8",
    )

    migration.migrate_env(
        source,
        output,
        database=tmp_path / "v5.db",
        account_id="primary",
    )

    migrated = output.read_text(encoding="utf-8")
    assert "MESSAGE_IO_API_KEY=secret" in migrated
    assert f"MESSAGE_IO_DB={tmp_path / 'v5.db'}" in migrated
    assert "FEISHU_LISTENER_ENABLED=true" in migrated
    assert "FEISHU_ACCOUNT_ID=primary" in migrated
    assert "FEISHU_ENABLED=true" in migrated
    assert "FEISHU_IO_API_KEY=secret" in source.read_text(encoding="utf-8")
