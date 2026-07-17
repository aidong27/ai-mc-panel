from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    def migrate(self) -> None:
        with self._lock, self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS schema_meta (
                    version INTEGER NOT NULL
                );
                INSERT INTO schema_meta(version)
                SELECT 0 WHERE NOT EXISTS (SELECT 1 FROM schema_meta);

                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('owner', 'viewer')),
                    disabled INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ws_tickets (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    expires_at TEXT NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS confirmations (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    action TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    params_hash TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    state TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    first_confirmed_at TEXT,
                    result_json TEXT
                );

                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY,
                    confirmation_id TEXT REFERENCES confirmations(id),
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    action TEXT NOT NULL,
                    state TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    result_json TEXT
                );

                CREATE TABLE IF NOT EXISTS audit_events (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    user_id INTEGER REFERENCES users(id),
                    action TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    confirmation_id TEXT,
                    params_summary TEXT NOT NULL,
                    result_summary TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ai_usage (
                    day TEXT PRIMARY KEY,
                    request_count INTEGER NOT NULL DEFAULT 0,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS panel_settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS player_activity_events (
                    event_id TEXT PRIMARY KEY,
                    player_name TEXT NOT NULL,
                    joined_at TEXT NOT NULL,
                    imported_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS player_activity_coverage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_at TEXT NOT NULL,
                    end_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK(start_at <= end_at)
                );

                CREATE TABLE IF NOT EXISTS player_activity_state (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    last_attempt_at TEXT NOT NULL,
                    last_success_at TEXT,
                    last_scan_complete INTEGER NOT NULL DEFAULT 0,
                    last_scan_truncated INTEGER NOT NULL DEFAULT 0,
                    last_error_code TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_audit_created_at
                    ON audit_events(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_sessions_user_id
                    ON sessions(user_id);
                CREATE INDEX IF NOT EXISTS idx_player_activity_joined_at
                    ON player_activity_events(joined_at);
                CREATE INDEX IF NOT EXISTS idx_player_activity_player_joined
                    ON player_activity_events(player_name, joined_at DESC);
                CREATE INDEX IF NOT EXISTS idx_player_activity_coverage_range
                    ON player_activity_coverage(start_at, end_at);
                UPDATE schema_meta SET version = 2;
                COMMIT;
                """
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self._lock, self.connect() as connection:
            cursor = connection.execute(sql, params)
            return cursor.rowcount

    def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(sql, params).fetchone()
            return None if row is None else dict(row)

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql, params).fetchall()]

    def add_audit(
        self,
        *,
        event_id: str,
        user_id: int | None,
        action: str,
        risk: str,
        outcome: str,
        request_id: str,
        confirmation_id: str | None,
        params_summary: dict[str, Any],
        result_summary: dict[str, Any],
    ) -> None:
        self.execute(
            """
            INSERT INTO audit_events(
                id, created_at, user_id, action, risk, outcome, request_id,
                confirmation_id, params_summary, result_summary
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                utc_now(),
                user_id,
                action,
                risk,
                outcome,
                request_id,
                confirmation_id,
                json.dumps(params_summary, ensure_ascii=False, sort_keys=True),
                json.dumps(result_summary, ensure_ascii=False, sort_keys=True),
            ),
        )
