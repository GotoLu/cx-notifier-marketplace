"""Concurrent-safe local delivery state and sanitized diagnostics."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .security import sanitize_text, sha256_short


QUESTION_SUMMARY_MAX_CHARS = 160
QUESTION_CONTEXT_MAX_AGE_SECONDS = 86400


@dataclass(frozen=True)
class QuestionContext:
    summary: str
    context_id: str


class DeliveryState:
    """SQLite-backed best-effort deduplication for concurrent hook processes."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir.expanduser()
        directory_existed = self.data_dir.exists()
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt" and not directory_existed:
            os.chmod(self.data_dir, 0o700)
        self.path = self.data_dir / "deliveries.sqlite3"
        self._connection = sqlite3.connect(
            self.path,
            timeout=1.0,
            isolation_level=None,
            check_same_thread=False,
        )
        if os.name != "nt":
            os.chmod(self.path, 0o600)
        self._lock = threading.Lock()
        with self._lock:
            self._connection.execute("PRAGMA busy_timeout=1000")
            # DELETE mode avoids persistent -wal/-shm files with separate permissions.
            # Transactions are tiny, and busy_timeout covers the expected hook concurrency.
            self._connection.execute("PRAGMA journal_mode=DELETE")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS deliveries (
                    delivery_key TEXT PRIMARY KEY,
                    notification_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS question_contexts (
                    context_key TEXT PRIMARY KEY,
                    question_summary TEXT NOT NULL,
                    context_id TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

    @staticmethod
    def channel_key(event_key: str, channel_name: str, channel_type: str) -> str:
        return sha256_short(f"{event_key}\0{channel_type}\0{channel_name}", 40)

    def claim(
        self,
        delivery_key: str,
        notification_id: str,
        *,
        dedupe_ttl_seconds: int,
        pending_ttl_seconds: int = 30,
    ) -> bool:
        now = time.time()
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._connection.execute(
                    "SELECT status, updated_at FROM deliveries WHERE delivery_key = ?",
                    (delivery_key,),
                ).fetchone()
                if row:
                    status, updated_at = str(row[0]), float(row[1])
                    if status == "sent" and now - updated_at < dedupe_ttl_seconds:
                        self._connection.execute("COMMIT")
                        return False
                    if status == "pending" and now - updated_at < pending_ttl_seconds:
                        self._connection.execute("COMMIT")
                        return False
                self._connection.execute(
                    """
                    INSERT INTO deliveries(delivery_key, notification_id, status, updated_at)
                    VALUES (?, ?, 'pending', ?)
                    ON CONFLICT(delivery_key) DO UPDATE SET
                        notification_id = excluded.notification_id,
                        status = 'pending',
                        updated_at = excluded.updated_at
                    """,
                    (delivery_key, notification_id, now),
                )
                self._connection.execute("COMMIT")
                return True
            except Exception:
                try:
                    self._connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    def mark_sent(self, delivery_key: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE deliveries SET status = 'sent', updated_at = ? WHERE delivery_key = ?",
                (time.time(), delivery_key),
            )

    def mark_failed(self, delivery_key: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE deliveries SET status = 'failed', updated_at = ? WHERE delivery_key = ?",
                (time.time(), delivery_key),
            )

    @staticmethod
    def question_context_key(
        client: str,
        session_id: str,
        turn_id: str | None = None,
    ) -> str:
        return sha256_short(f"{client}\0{session_id}\0{turn_id or ''}", 40)

    def remember_question(
        self,
        *,
        client: str,
        session_id: str,
        turn_id: str | None,
        prompt: str,
    ) -> QuestionContext | None:
        """Persist only a redacted, bounded question summary for a later Stop."""

        summary = sanitize_text(prompt, QUESTION_SUMMARY_MAX_CHARS)
        if not summary:
            return None
        now = time.time()
        context_id = sha256_short(
            f"{now!r}\0{time.time_ns()}\0{os.getpid()}\0{threading.get_ident()}",
            32,
        )
        context_key = self.question_context_key(client, session_id, turn_id)
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO question_contexts(
                    context_key, question_summary, context_id, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(context_key) DO UPDATE SET
                    question_summary = excluded.question_summary,
                    context_id = excluded.context_id,
                    updated_at = excluded.updated_at
                """,
                (context_key, summary, context_id, now),
            )
            self._connection.execute(
                "DELETE FROM question_contexts WHERE updated_at < ?",
                (now - QUESTION_CONTEXT_MAX_AGE_SECONDS,),
            )
        return QuestionContext(summary=summary, context_id=context_id)

    def load_question(
        self,
        *,
        client: str,
        session_id: str,
        turn_id: str | None,
    ) -> QuestionContext | None:
        """Load the current turn's question, with a session fallback for Claude Code."""

        keys = [self.question_context_key(client, session_id, turn_id)]
        if turn_id:
            keys.append(self.question_context_key(client, session_id))
        cutoff = time.time() - QUESTION_CONTEXT_MAX_AGE_SECONDS
        with self._lock:
            for context_key in keys:
                row = self._connection.execute(
                    """
                    SELECT question_summary, context_id
                    FROM question_contexts
                    WHERE context_key = ? AND updated_at >= ?
                    """,
                    (context_key, cutoff),
                ).fetchone()
                if row:
                    return QuestionContext(summary=str(row[0]), context_id=str(row[1]))
        return None

    def purge(self, retention_seconds: int) -> None:
        with self._lock:
            self._connection.execute(
                "DELETE FROM deliveries WHERE updated_at < ?",
                (time.time() - retention_seconds,),
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class SafeLogger:
    """Append diagnostics that never include payloads, URLs, or credentials."""

    def __init__(self, data_dir: Path, max_bytes: int = 262144) -> None:
        self.data_dir = data_dir.expanduser()
        directory_existed = self.data_dir.exists()
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt" and not directory_existed:
            os.chmod(self.data_dir, 0o700)
        self.path = self.data_dir / "events.log"
        self.max_bytes = max_bytes
        self._lock = threading.Lock()

    def write(self, code: str, **fields: Any) -> None:
        record: dict[str, Any] = {
            "time": int(time.time()),
            "code": sanitize_text(code, 80),
        }
        for key, value in fields.items():
            if key not in {"channel", "event", "status", "attempt", "notification_id"}:
                continue
            if value is not None:
                record[key] = sanitize_text(value, 100)
        encoded = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        with self._lock:
            try:
                if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                    backup = self.path.with_suffix(".log.1")
                    try:
                        backup.unlink()
                    except FileNotFoundError:
                        pass
                    self.path.replace(backup)
                descriptor = os.open(
                    self.path,
                    os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                    0o600,
                )
                with os.fdopen(descriptor, "ab") as handle:
                    handle.write(encoded)
                if os.name != "nt":
                    os.chmod(self.path, 0o600)
            except OSError:
                pass
