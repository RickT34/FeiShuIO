#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from message_io.store import MessageStore, SCHEMA_VERSION


ENV_RENAMES = {
    "FEISHU_IO_API_KEY": "MESSAGE_IO_API_KEY",
    "FEISHU_IO_DB": "MESSAGE_IO_DB",
    "FEISHU_IO_ENABLE_WS": "FEISHU_LISTENER_ENABLED",
    "FEISHU_IO_HOST": "MESSAGE_IO_HOST",
    "FEISHU_IO_PORT": "MESSAGE_IO_PORT",
    "FEISHU_IO_LOG_LEVEL": "MESSAGE_IO_LOG_LEVEL",
    "FEISHU_MARK_READ_REACTION": "FEISHU_MARK_DELIVERED_REACTION",
    "FEISHU_READ_REACTION_EMOJI": "FEISHU_DELIVERED_REACTION_EMOJI",
    "FEISHU_MESSAGE_LEASE_SECONDS": "MESSAGE_IO_LEASE_SECONDS",
    "FEISHU_DELIVERED_RETENTION_DAYS": "MESSAGE_IO_DELIVERED_RETENTION_DAYS",
    "FEISHU_PROCESSED_RETENTION_DAYS": "MESSAGE_IO_PROCESSED_RETENTION_DAYS",
}


def migrate_database(source: Path, output: Path, *, account_id: str) -> dict[str, int]:
    if source.resolve() == output.resolve():
        raise ValueError("output database must differ from the source database")
    if not source.is_file():
        raise ValueError(f"source database does not exist: {source}")
    if output.exists():
        raise ValueError(f"output database already exists: {output}")

    source_conn = sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True)
    source_conn.row_factory = sqlite3.Row
    try:
        version = int(source_conn.execute("PRAGMA user_version").fetchone()[0])
        if version != 3:
            raise ValueError(f"expected schema version 3, found {version}")
        bindings = source_conn.execute(
            "SELECT alias, chat_id, created_at, updated_at FROM group_bindings"
        ).fetchall()
        messages = source_conn.execute(
            "SELECT * FROM unread_messages ORDER BY message_id"
        ).fetchall()
        processed = source_conn.execute(
            "SELECT external_message_id, created_at FROM processed_messages"
        ).fetchall()
    finally:
        source_conn.close()

    output.parent.mkdir(parents=True, exist_ok=True)
    MessageStore(str(output))
    try:
        with sqlite3.connect(output) as target:
            target.execute("PRAGMA foreign_keys = ON")
            target.execute("BEGIN IMMEDIATE")
            for binding in bindings:
                target.execute(
                    """
                    INSERT INTO destinations (
                        alias, platform, account_id, conversation_id, created_at, updated_at
                    ) VALUES (?, 'feishu', ?, ?, ?, ?)
                    """,
                    (
                        binding["alias"],
                        account_id,
                        binding["chat_id"],
                        binding["created_at"],
                        binding["updated_at"],
                    ),
                )

            alias_to_chat = {
                str(binding["alias"]): str(binding["chat_id"])
                for binding in bindings
            }
            chat_to_alias = {
                str(binding["chat_id"]): str(binding["alias"])
                for binding in bindings
            }
            for message in messages:
                conversation_id = alias_to_chat.get(
                    str(message["group_id"]), str(message["group_id"])
                )
                lease_target = None
                if message["lease_token"] and message["lease_until"]:
                    group_id = str(message["group_id"])
                    if group_id in alias_to_chat:
                        lease_target = group_id
                    else:
                        lease_target = chat_to_alias.get(group_id)
                target.execute(
                    "INSERT INTO destinations(platform, account_id, conversation_id) "
                    "VALUES ('feishu', ?, ?) ON CONFLICT DO NOTHING",
                    (account_id, conversation_id),
                )
                destination_id = target.execute(
                    "SELECT destination_id FROM destinations WHERE platform = 'feishu' "
                    "AND account_id = ? AND conversation_id = ?",
                    (account_id, conversation_id),
                ).fetchone()[0]
                content_type = str(message["message_type"] or "unknown")
                if content_type not in {
                    "text", "image", "file", "audio", "video", "unknown"
                }:
                    content_type = "unknown"
                text = str(message["text"] or f"[{content_type}]")
                target.execute(
                    """
                    INSERT INTO messages (
                        message_id, destination_id, platform, account_id,
                        external_message_id, sender_id, sender_name, content_type,
                        text, private_payload_json, received_at, delivered_at,
                        lease_until, lease_token, lease_target
                    ) VALUES (?, ?, 'feishu', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message["message_id"],
                        destination_id,
                        account_id,
                        message["external_message_id"],
                        message["sender_id"],
                        message["sender_name"],
                        content_type,
                        text,
                        message["raw_json"],
                        message["created_at"],
                        message["delivered_at"],
                        message["lease_until"],
                        message["lease_token"],
                        lease_target,
                    ),
                )

            for row in processed:
                target.execute(
                    "INSERT INTO processed_messages("
                    "platform, account_id, external_message_id, created_at"
                    ") VALUES ('feishu', ?, ?, ?)",
                    (account_id, row["external_message_id"], row["created_at"]),
                )
            target.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            target.commit()
    except Exception:
        _remove_output_database(output)
        raise

    return {
        "bindings": len(bindings),
        "messages": len(messages),
        "processed_messages": len(processed),
    }


def _remove_output_database(path: Path) -> None:
    path.unlink(missing_ok=True)
    Path(f"{path}-wal").unlink(missing_ok=True)
    Path(f"{path}-shm").unlink(missing_ok=True)


def migrate_env(source: Path, output: Path, *, database: Path, account_id: str) -> None:
    if not source.is_file():
        raise ValueError(f"source env file does not exist: {source}")
    if output.exists():
        raise ValueError(f"output env file already exists: {output}")
    lines: list[str] = []
    seen: set[str] = set()
    for line in source.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            lines.append(line)
            continue
        key, value = line.split("=", 1)
        new_key = ENV_RENAMES.get(key.strip(), key.strip())
        if new_key == "MESSAGE_IO_DB":
            value = str(database)
        seen.add(new_key)
        lines.append(f"{new_key}={value}")
    if "MESSAGE_IO_DB" not in seen:
        lines.append(f"MESSAGE_IO_DB={database}")
    if "FEISHU_ACCOUNT_ID" not in seen:
        lines.append(f"FEISHU_ACCOUNT_ID={account_id}")
    if "FEISHU_ENABLED" not in seen:
        lines.append("FEISHU_ENABLED=true")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Migrate a legacy schema-v3 deployment to MessageIO schema v5."
    )
    parser.add_argument("database", type=Path, help="Existing schema-v3 SQLite database")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--account-id", default="default")
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--env-output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = args.output or args.database.with_name(
        f"{args.database.stem}.v5{args.database.suffix or '.sqlite3'}"
    )
    counts = migrate_database(args.database, output, account_id=args.account_id)
    if args.env_file:
        env_output = args.env_output or args.env_file.with_name(
            f"{args.env_file.name}.v5"
        )
        migrate_env(
            args.env_file,
            env_output,
            database=output,
            account_id=args.account_id,
        )
        print(f"wrote config: {env_output}")
    print(f"wrote database: {output}")
    print(
        "migrated "
        f"{counts['bindings']} bindings, {counts['messages']} messages, "
        f"{counts['processed_messages']} processed ids"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
