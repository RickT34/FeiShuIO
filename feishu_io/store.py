from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from feishu_io.events import IncomingMessage


SCHEMA_VERSION = 3


class MessageStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.init_db()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        path = Path(self.db_path)
        if path.parent != Path("."):
            path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            current_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if current_version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"database schema version {current_version} is newer than supported "
                    f"version {SCHEMA_VERSION}"
                )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS group_bindings (
                    alias TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_messages (
                    external_message_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS unread_messages (
                    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    external_message_id TEXT UNIQUE,
                    group_id TEXT NOT NULL,
                    sender_id TEXT,
                    sender_name TEXT,
                    message_type TEXT NOT NULL,
                    text TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    delivered_at TEXT,
                    lease_until TEXT,
                    lease_token TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_unread_group_delivered
                ON unread_messages (group_id, delivered_at, message_id)
                """
            )
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(unread_messages)").fetchall()
            }
            if "lease_until" not in columns:
                conn.execute("ALTER TABLE unread_messages ADD COLUMN lease_until TEXT")
            if "lease_token" not in columns:
                conn.execute("ALTER TABLE unread_messages ADD COLUMN lease_token TEXT")

            if current_version < 3:
                self._migrate_message_destinations(conn)
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_unread_group_lease
                ON unread_messages (group_id, delivered_at, lease_until, message_id)
                """
            )
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _migrate_message_destinations(self, conn: sqlite3.Connection) -> None:
        bindings = {
            str(row["alias"]): str(row["chat_id"])
            for row in conn.execute("SELECT alias, chat_id FROM group_bindings").fetchall()
        }
        rows = conn.execute(
            "SELECT message_id, group_id, raw_json FROM unread_messages"
        ).fetchall()
        for row in rows:
            chat_id = self._chat_id_from_raw_json(str(row["raw_json"]))
            destination = chat_id or bindings.get(str(row["group_id"]))
            if destination and destination != row["group_id"]:
                conn.execute(
                    "UPDATE unread_messages SET group_id = ? WHERE message_id = ?",
                    (destination, row["message_id"]),
                )

    @staticmethod
    def _chat_id_from_raw_json(raw_json: str) -> str | None:
        try:
            payload = json.loads(raw_json)
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        event = payload.get("event")
        if not isinstance(event, dict):
            return None
        message = event.get("message")
        if not isinstance(message, dict):
            return None
        chat_id = message.get("chat_id")
        return chat_id if isinstance(chat_id, str) and chat_id else None

    def mark_processed(self, external_message_id: str | None) -> bool:
        if not external_message_id:
            return True

        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO processed_messages (external_message_id)
                VALUES (?)
                ON CONFLICT(external_message_id) DO NOTHING
                """,
                (external_message_id,),
            )
            return cursor.rowcount > 0

    def bind_group(self, *, alias: str, chat_id: str) -> bool:
        with self.connect() as conn:
            return self._bind_group(conn, alias=alias, chat_id=chat_id)

    def bind_group_once(
        self,
        *,
        external_message_id: str | None,
        alias: str,
        chat_id: str,
    ) -> tuple[bool, bool]:
        with self.connect() as conn:
            if not self._mark_processed(conn, external_message_id):
                return False, False
            return True, self._bind_group(conn, alias=alias, chat_id=chat_id)

    def _bind_group(self, conn: sqlite3.Connection, *, alias: str, chat_id: str) -> bool:
        existing = conn.execute(
            """
            SELECT chat_id
            FROM group_bindings
            WHERE alias = ?
            """,
            (alias,),
        ).fetchone()
        if existing and existing["chat_id"] == chat_id:
            return False

        if existing:
            conn.execute(
                "UPDATE unread_messages SET group_id = ? WHERE group_id = ?",
                (existing["chat_id"], alias),
            )

        previous_alias = conn.execute(
            "SELECT alias FROM group_bindings WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        if previous_alias:
            conn.execute(
                "UPDATE unread_messages SET group_id = ? WHERE group_id = ?",
                (chat_id, previous_alias["alias"]),
            )

        conn.execute(
            """
            DELETE FROM group_bindings
            WHERE chat_id = ? AND alias != ?
            """,
            (chat_id, alias),
        )
        conn.execute(
            """
            INSERT INTO group_bindings (alias, chat_id)
            VALUES (?, ?)
            ON CONFLICT(alias) DO UPDATE SET
                chat_id = excluded.chat_id,
                updated_at = datetime('now')
            """,
            (alias, chat_id),
        )
        return True

    def resolve_alias(self, alias: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT chat_id
                FROM group_bindings
                WHERE alias = ?
                """,
                (alias,),
            ).fetchone()
        return str(row["chat_id"]) if row else None

    def resolve_chat_id(self, chat_id: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT alias
                FROM group_bindings
                WHERE chat_id = ?
                """,
                (chat_id,),
            ).fetchone()
        return str(row["alias"]) if row else None

    def add_message(
        self,
        message: IncomingMessage,
    ) -> int:
        with self.connect() as conn:
            cursor = self._insert_message(conn, message)
            return int(cursor.lastrowid or 0)

    def add_message_once(self, message: IncomingMessage) -> bool:
        with self.connect() as conn:
            if not self._mark_processed(conn, message.external_message_id):
                return False
            cursor = self._insert_message(conn, message)
            return cursor.rowcount > 0

    def _mark_processed(
        self,
        conn: sqlite3.Connection,
        external_message_id: str | None,
    ) -> bool:
        if not external_message_id:
            return True

        cursor = conn.execute(
            """
            INSERT INTO processed_messages (external_message_id)
            VALUES (?)
            ON CONFLICT(external_message_id) DO NOTHING
            """,
            (external_message_id,),
        )
        return cursor.rowcount > 0

    def _insert_message(
        self,
        conn: sqlite3.Connection,
        message: IncomingMessage,
    ) -> sqlite3.Cursor:
        return conn.execute(
            """
            INSERT INTO unread_messages (
                external_message_id, group_id, sender_id, sender_name,
                message_type, text, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(external_message_id) DO NOTHING
            """,
            (
                message.external_message_id,
                message.group_id,
                message.sender_id,
                message.sender_name,
                message.message_type,
                message.text,
                json.dumps(message.raw, ensure_ascii=False),
            ),
        )

    def pop_unread(
        self,
        group_id: str,
        limit: int,
        *,
        ack: bool = True,
        lease_seconds: int = 300,
    ) -> list[dict]:
        lease_token = None if ack else uuid.uuid4().hex
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT *
                FROM unread_messages
                WHERE group_id = ?
                  AND delivered_at IS NULL
                  AND (lease_until IS NULL OR lease_until <= datetime('now'))
                ORDER BY message_id ASC
                LIMIT ?
                """,
                (group_id, limit),
            ).fetchall()
            message_ids = [row["message_id"] for row in rows]
            if not message_ids:
                return []

            placeholders = ",".join("?" for _ in message_ids)
            if ack:
                conn.execute(
                    f"""
                    UPDATE unread_messages
                    SET delivered_at = datetime('now'),
                        lease_until = NULL,
                        lease_token = NULL
                    WHERE message_id IN ({placeholders})
                    """,
                    message_ids,
                )
            else:
                conn.execute(
                    f"""
                    UPDATE unread_messages
                    SET lease_until = datetime('now', '+' || ? || ' seconds'),
                        lease_token = ?
                    WHERE message_id IN ({placeholders})
                    """,
                    [lease_seconds, lease_token, *message_ids],
                )

        return [
            {
                "message_id": row["message_id"],
                "external_message_id": row["external_message_id"],
                "id": row["group_id"],
                "sender_id": row["sender_id"],
                "sender_name": row["sender_name"],
                "message_type": row["message_type"],
                "text": row["text"],
                "raw": json.loads(row["raw_json"]),
                "created_at": row["created_at"],
                "lease_token": lease_token,
            }
            for row in sorted(rows, key=lambda item: item["message_id"])
        ]

    def ack_messages(
        self,
        group_id: str,
        message_ids: list[int],
        *,
        lease_token: str,
    ) -> list[dict]:
        if not message_ids:
            return []

        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            placeholders = ",".join("?" for _ in message_ids)
            rows = conn.execute(
                f"""
                SELECT message_id, external_message_id
                FROM unread_messages
                WHERE group_id = ?
                  AND delivered_at IS NULL
                  AND lease_token = ?
                  AND lease_until > datetime('now')
                  AND message_id IN ({placeholders})
                """,
                [group_id, lease_token, *message_ids],
            ).fetchall()
            acked_ids = [row["message_id"] for row in rows]
            if not acked_ids:
                return []
            acked_placeholders = ",".join("?" for _ in acked_ids)
            conn.execute(
                f"""
                UPDATE unread_messages
                SET delivered_at = datetime('now'),
                    lease_until = NULL,
                    lease_token = NULL
                WHERE message_id IN ({acked_placeholders})
                  AND lease_token = ?
                """,
                [*acked_ids, lease_token],
            )
        return [
            {
                "message_id": row["message_id"],
                "external_message_id": row["external_message_id"],
            }
            for row in sorted(rows, key=lambda item: item["message_id"])
        ]

    def cleanup(self, *, delivered_retention_days: int, processed_retention_days: int) -> dict:
        with self.connect() as conn:
            delivered = conn.execute(
                """
                DELETE FROM unread_messages
                WHERE delivered_at IS NOT NULL
                  AND delivered_at < datetime('now', '-' || ? || ' days')
                """,
                (delivered_retention_days,),
            ).rowcount
            processed = conn.execute(
                """
                DELETE FROM processed_messages
                WHERE created_at < datetime('now', '-' || ? || ' days')
                """,
                (processed_retention_days,),
            ).rowcount
        return {
            "delivered_messages_deleted": int(delivered),
            "processed_messages_deleted": int(processed),
        }

    def health_check(self) -> dict:
        with self.connect() as conn:
            conn.execute("SELECT 1").fetchone()
            pending = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM unread_messages
                WHERE delivered_at IS NULL
                """
            ).fetchone()["count"]
            leased = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM unread_messages
                WHERE delivered_at IS NULL
                  AND lease_until IS NOT NULL
                  AND lease_until > datetime('now')
                """
            ).fetchone()["count"]
        return {"ok": True, "pending_messages": int(pending), "leased_messages": int(leased)}
