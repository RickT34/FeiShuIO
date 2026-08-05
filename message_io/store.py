from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from message_io.domain import DeliveryReference, Destination, IncomingMessage


SCHEMA_VERSION = 5


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
        conn.execute("PRAGMA foreign_keys = ON")
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
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            if version not in (0, SCHEMA_VERSION):
                raise RuntimeError(
                    f"database schema version {version} is not supported; "
                    "run scripts/migrate_v3_to_v5.py"
                )
            if version == 0 and {"group_bindings", "unread_messages"} & tables:
                raise RuntimeError(
                    "legacy database without a schema version; "
                    "run scripts/migrate_v3_to_v5.py"
                )
            self._create_schema(conn)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @staticmethod
    def _create_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS destinations (
                destination_id INTEGER PRIMARY KEY AUTOINCREMENT,
                alias TEXT,
                platform TEXT NOT NULL,
                account_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(platform, account_id, conversation_id)
            );

            CREATE TABLE IF NOT EXISTS processed_messages (
                platform TEXT NOT NULL,
                account_id TEXT NOT NULL,
                external_message_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY(platform, account_id, external_message_id)
            );

            CREATE TABLE IF NOT EXISTS messages (
                message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                destination_id INTEGER NOT NULL REFERENCES destinations(destination_id),
                platform TEXT NOT NULL,
                account_id TEXT NOT NULL,
                external_message_id TEXT,
                sender_id TEXT,
                sender_name TEXT,
                content_type TEXT NOT NULL,
                text TEXT NOT NULL,
                private_payload_json TEXT NOT NULL,
                received_at TEXT NOT NULL DEFAULT (datetime('now')),
                delivered_at TEXT,
                lease_until TEXT,
                lease_token TEXT,
                lease_target TEXT,
                UNIQUE(platform, account_id, external_message_id)
            );

            CREATE INDEX IF NOT EXISTS idx_destinations_alias
            ON destinations(alias);
            CREATE INDEX IF NOT EXISTS idx_messages_delivery
            ON messages(destination_id, delivered_at, lease_until, message_id);
            """
        )

    def bind_destination_once(
        self,
        *,
        external_message_id: str | None,
        destination: Destination,
        alias: str,
    ) -> tuple[bool, bool, int]:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not self._mark_processed(conn, destination, external_message_id):
                return False, False, 0
            destination_id = self._ensure_destination(conn, destination)
            row = conn.execute(
                "SELECT alias FROM destinations WHERE destination_id = ?",
                (destination_id,),
            ).fetchone()
            if row and row["alias"] == alias:
                return True, False, self._alias_count(conn, alias)
            conn.execute(
                "UPDATE destinations SET alias = ?, updated_at = datetime('now') "
                "WHERE destination_id = ?",
                (alias, destination_id),
            )
            return True, True, self._alias_count(conn, alias)

    def bind_destination(self, *, alias: str, destination: Destination) -> bool:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            destination_id = self._ensure_destination(conn, destination)
            row = conn.execute(
                "SELECT alias FROM destinations WHERE destination_id = ?",
                (destination_id,),
            ).fetchone()
            if row and row["alias"] == alias:
                return False
            conn.execute(
                "UPDATE destinations SET alias = ?, updated_at = datetime('now') "
                "WHERE destination_id = ?",
                (alias, destination_id),
            )
            return True

    def resolve_targets(self, alias: str) -> list[Destination]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT platform, account_id, conversation_id "
                "FROM destinations WHERE alias = ? ORDER BY destination_id ASC",
                (alias,),
            ).fetchall()
        return [
            Destination(
                platform=str(row["platform"]),
                account_id=str(row["account_id"]),
                conversation_id=str(row["conversation_id"]),
            )
            for row in rows
        ]

    def destination_alias(self, destination: Destination) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT alias FROM destinations WHERE platform = ? "
                "AND account_id = ? AND conversation_id = ?",
                (
                    destination.platform,
                    destination.account_id,
                    destination.conversation_id,
                ),
            ).fetchone()
        if row is None or row["alias"] is None:
            return None
        return str(row["alias"])

    def list_bindings(self) -> list[tuple[str, int]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT alias, COUNT(*) AS destination_count FROM destinations "
                "WHERE alias IS NOT NULL GROUP BY alias ORDER BY alias ASC"
            ).fetchall()
        return [
            (str(row["alias"]), int(row["destination_count"])) for row in rows
        ]

    def mark_processed_once(
        self,
        *,
        external_message_id: str | None,
        destination: Destination,
    ) -> bool:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            return self._mark_processed(conn, destination, external_message_id)

    def unbind_destination_once(
        self,
        *,
        external_message_id: str | None,
        destination: Destination,
    ) -> tuple[bool, str | None, int]:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not self._mark_processed(conn, destination, external_message_id):
                return False, None, 0
            row = conn.execute(
                "SELECT destination_id, alias FROM destinations WHERE platform = ? "
                "AND account_id = ? AND conversation_id = ?",
                (
                    destination.platform,
                    destination.account_id,
                    destination.conversation_id,
                ),
            ).fetchone()
            if row is None or row["alias"] is None:
                return True, None, 0
            alias = str(row["alias"])
            conn.execute(
                "UPDATE destinations SET alias = NULL, updated_at = datetime('now') "
                "WHERE destination_id = ?",
                (int(row["destination_id"]),),
            )
            return True, alias, self._alias_count(conn, alias)

    def add_message_once(self, message: IncomingMessage) -> bool:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not self._mark_processed(
                conn, message.destination, message.external_message_id
            ):
                return False
            destination_id = self._ensure_destination(conn, message.destination)
            cursor = conn.execute(
                """
                INSERT INTO messages (
                    destination_id, platform, account_id, external_message_id,
                    sender_id, sender_name, content_type, text, private_payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, account_id, external_message_id) DO NOTHING
                """,
                (
                    destination_id,
                    message.destination.platform,
                    message.destination.account_id,
                    message.external_message_id,
                    message.sender.id,
                    message.sender.name,
                    message.content.type,
                    message.content.text,
                    json.dumps(message.raw, ensure_ascii=False),
                ),
            )
            return cursor.rowcount > 0

    def receive(
        self,
        target: str,
        limit: int,
        *,
        ack: bool = True,
        lease_seconds: int = 300,
    ) -> tuple[list[dict], str | None]:
        lease_token = None if ack else uuid.uuid4().hex
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            destination_ids = self._destination_ids_for_alias(conn, target)
            destination_placeholders = ",".join("?" for _ in destination_ids)
            rows = conn.execute(
                f"""
                SELECT message_id, sender_id, sender_name, content_type, text, received_at
                FROM messages
                WHERE destination_id IN ({destination_placeholders})
                  AND delivered_at IS NULL
                  AND (lease_until IS NULL OR lease_until <= datetime('now'))
                ORDER BY message_id ASC
                LIMIT ?
                """,
                [*destination_ids, limit],
            ).fetchall()
            message_ids = [int(row["message_id"]) for row in rows]
            if not message_ids:
                return [], None
            placeholders = ",".join("?" for _ in message_ids)
            if ack:
                conn.execute(
                    f"UPDATE messages SET delivered_at = datetime('now'), "
                    f"lease_until = NULL, lease_token = NULL, lease_target = NULL "
                    f"WHERE message_id IN ({placeholders})",
                    message_ids,
                )
            else:
                conn.execute(
                    f"UPDATE messages SET lease_until = "
                    f"datetime('now', '+' || ? || ' seconds'), lease_token = ?, "
                    f"lease_target = ? "
                    f"WHERE message_id IN ({placeholders})",
                    [lease_seconds, lease_token, target, *message_ids],
                )
        return [self._public_message(row) for row in rows], lease_token

    def ack_messages(
        self,
        target: str,
        message_ids: list[int],
        *,
        lease_token: str,
    ) -> list[DeliveryReference]:
        if not message_ids:
            return []
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            placeholders = ",".join("?" for _ in message_ids)
            rows = conn.execute(
                f"""
                SELECT message_id, platform, account_id, external_message_id
                FROM messages
                WHERE lease_target = ? AND delivered_at IS NULL
                  AND lease_token = ? AND lease_until > datetime('now')
                  AND message_id IN ({placeholders})
                """,
                [target, lease_token, *message_ids],
            ).fetchall()
            acked_ids = [int(row["message_id"]) for row in rows]
            if not acked_ids:
                return []
            acked_placeholders = ",".join("?" for _ in acked_ids)
            conn.execute(
                f"UPDATE messages SET delivered_at = datetime('now'), "
                f"lease_until = NULL, lease_token = NULL, lease_target = NULL "
                f"WHERE message_id IN ({acked_placeholders}) AND lease_token = ?",
                [*acked_ids, lease_token],
            )
        return [
            DeliveryReference(
                platform=str(row["platform"]),
                account_id=str(row["account_id"]),
                external_message_id=(
                    str(row["external_message_id"])
                    if row["external_message_id"] is not None
                    else None
                ),
            )
            for row in rows
        ]

    def delivery_references(self, message_ids: list[int]) -> list[DeliveryReference]:
        if not message_ids:
            return []
        with self.connect() as conn:
            placeholders = ",".join("?" for _ in message_ids)
            rows = conn.execute(
                f"SELECT platform, account_id, external_message_id FROM messages "
                f"WHERE message_id IN ({placeholders})",
                message_ids,
            ).fetchall()
        return [
            DeliveryReference(
                platform=str(row["platform"]),
                account_id=str(row["account_id"]),
                external_message_id=(
                    str(row["external_message_id"])
                    if row["external_message_id"] is not None
                    else None
                ),
            )
            for row in rows
        ]

    def cleanup(
        self, *, delivered_retention_days: int, processed_retention_days: int
    ) -> dict[str, int]:
        with self.connect() as conn:
            delivered = conn.execute(
                "DELETE FROM messages WHERE delivered_at IS NOT NULL "
                "AND delivered_at < datetime('now', '-' || ? || ' days')",
                (delivered_retention_days,),
            ).rowcount
            processed = conn.execute(
                "DELETE FROM processed_messages WHERE "
                "created_at < datetime('now', '-' || ? || ' days')",
                (processed_retention_days,),
            ).rowcount
        return {
            "delivered_messages_deleted": int(delivered),
            "processed_messages_deleted": int(processed),
        }

    def health_check(self) -> dict[str, int | bool]:
        with self.connect() as conn:
            conn.execute("SELECT 1").fetchone()
            pending = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE delivered_at IS NULL"
            ).fetchone()[0]
            leased = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE delivered_at IS NULL "
                "AND lease_until > datetime('now')"
            ).fetchone()[0]
        return {
            "ok": True,
            "pending_messages": int(pending),
            "leased_messages": int(leased),
        }

    @staticmethod
    def _ensure_destination(conn: sqlite3.Connection, destination: Destination) -> int:
        conn.execute(
            "INSERT INTO destinations(platform, account_id, conversation_id) "
            "VALUES (?, ?, ?) ON CONFLICT(platform, account_id, conversation_id) "
            "DO NOTHING",
            (destination.platform, destination.account_id, destination.conversation_id),
        )
        row = conn.execute(
            "SELECT destination_id FROM destinations WHERE platform = ? "
            "AND account_id = ? AND conversation_id = ?",
            (destination.platform, destination.account_id, destination.conversation_id),
        ).fetchone()
        return int(row["destination_id"])

    @staticmethod
    def _mark_processed(
        conn: sqlite3.Connection,
        destination: Destination,
        external_message_id: str | None,
    ) -> bool:
        if not external_message_id:
            return True
        cursor = conn.execute(
            "INSERT INTO processed_messages(platform, account_id, external_message_id) "
            "VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
            (destination.platform, destination.account_id, external_message_id),
        )
        return cursor.rowcount > 0

    @staticmethod
    def _alias_count(conn: sqlite3.Connection, alias: str) -> int:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM destinations WHERE alias = ?", (alias,)
            ).fetchone()[0]
        )

    @staticmethod
    def _destination_ids_for_alias(
        conn: sqlite3.Connection, alias: str
    ) -> list[int]:
        rows = conn.execute(
            "SELECT destination_id FROM destinations WHERE alias = ? "
            "ORDER BY destination_id ASC",
            (alias,),
        ).fetchall()
        if not rows:
            raise KeyError(alias)
        return [int(row["destination_id"]) for row in rows]

    @staticmethod
    def _public_message(row: sqlite3.Row) -> dict:
        return {
            "message_id": int(row["message_id"]),
            "sender": {"id": row["sender_id"], "name": row["sender_name"]},
            "content": {"type": row["content_type"], "text": row["text"]},
            "received_at": str(row["received_at"]),
        }
