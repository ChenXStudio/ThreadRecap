from __future__ import annotations

import sqlite3
from pathlib import Path

from .model import SessionState


COOLDOWN_SECONDS = 300.0


class Store:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER NOT NULL
                );
                INSERT INTO schema_version(version)
                SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_version);

                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    thread_id TEXT,
                    active_turn_id TEXT,
                    generation INTEGER NOT NULL DEFAULT 0,
                    phase TEXT NOT NULL DEFAULT 'idle',
                    last_activity_at REAL,
                    completed_at REAL,
                    due_at REAL,
                    last_summarized_turn_id TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL,
                    last_error TEXT
                );
                CREATE INDEX IF NOT EXISTS sessions_pending
                    ON sessions(phase, due_at, next_attempt_at);

                CREATE TABLE IF NOT EXISTS internal_turns (
                    session_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    PRIMARY KEY(session_id, turn_id)
                );

                CREATE TABLE IF NOT EXISTS worker_lease (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    owner TEXT NOT NULL,
                    expires_at REAL NOT NULL
                );
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(sessions)").fetchall()
            }
            if "last_activity_at" not in columns:
                connection.execute(
                    "ALTER TABLE sessions ADD COLUMN last_activity_at REAL"
                )
            connection.execute("UPDATE schema_version SET version = 2")

    @staticmethod
    def _state(row: sqlite3.Row | None) -> SessionState | None:
        if row is None:
            return None
        return SessionState(**dict(row))

    def get_session(self, session_id: str) -> SessionState | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return self._state(row)

    def record_user_prompt(
        self, session_id: str, turn_id: str, *, now: float
    ) -> SessionState:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO sessions(
                    session_id, active_turn_id, generation, phase, last_activity_at,
                    completed_at, due_at, retry_count, next_attempt_at, last_error
                ) VALUES (?, ?, 1, 'working', ?, NULL, NULL, 0, NULL, NULL)
                ON CONFLICT(session_id) DO UPDATE SET
                    active_turn_id = excluded.active_turn_id,
                    generation = sessions.generation + 1,
                    phase = 'working',
                    last_activity_at = excluded.last_activity_at,
                    completed_at = NULL,
                    due_at = NULL,
                    retry_count = 0,
                    next_attempt_at = NULL,
                    last_error = NULL
                """,
                (session_id, turn_id, now),
            )
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        state = self._state(row)
        assert state is not None
        return state

    def record_internal_turn(self, session_id: str, turn_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO internal_turns(session_id, turn_id) VALUES (?, ?)",
                (session_id, turn_id),
            )

    def record_stop(self, session_id: str, turn_id: str) -> bool:
        with self._connect() as connection:
            internal = connection.execute(
                "SELECT 1 FROM internal_turns WHERE session_id = ? AND turn_id = ?",
                (session_id, turn_id),
            ).fetchone()
            if internal is not None:
                return False
            cursor = connection.execute(
                """
                UPDATE sessions SET phase = 'waiting_completion'
                WHERE session_id = ? AND active_turn_id = ?
                  AND phase IN ('working', 'waiting_completion')
                """,
                (session_id, turn_id),
            )
            return cursor.rowcount == 1

    def mark_turn_completed(
        self,
        session_id: str,
        generation: int,
        thread_id: str,
        turn_id: str,
        *,
        completed_at: float,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE sessions SET
                    thread_id = ?, phase = 'cooling', completed_at = ?, due_at = ?,
                    retry_count = 0, next_attempt_at = NULL, last_error = NULL
                WHERE session_id = ? AND generation = ? AND active_turn_id = ?
                  AND phase = 'waiting_completion'
                """,
                (
                    thread_id,
                    completed_at,
                    completed_at + COOLDOWN_SECONDS,
                    session_id,
                    generation,
                    turn_id,
                ),
            )
            return cursor.rowcount == 1

    def claim_summary(self, session_id: str, generation: int, *, now: float) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE sessions SET phase = 'generating'
                WHERE session_id = ? AND generation = ? AND phase = 'cooling'
                  AND due_at <= ? AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                """,
                (session_id, generation, now, now),
            )
            return cursor.rowcount == 1

    def finish_summary(
        self, session_id: str, generation: int, summary_turn_id: str
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE sessions SET
                    phase = 'idle', last_summarized_turn_id = active_turn_id,
                    retry_count = 0, next_attempt_at = NULL, last_error = NULL
                WHERE session_id = ? AND generation = ? AND phase = 'generating'
                """,
                (session_id, generation),
            )
            return cursor.rowcount == 1

    def mark_superseded(self, session_id: str, generation: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE sessions SET phase = 'superseded'
                WHERE session_id = ? AND generation = ?
                  AND phase IN ('waiting_completion', 'cooling', 'generating')
                """,
                (session_id, generation),
            )
            return cursor.rowcount == 1

    def record_failure(
        self, session_id: str, generation: int, code: str, *, now: float
    ) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT retry_count, phase FROM sessions WHERE session_id = ? AND generation = ?",
                (session_id, generation),
            ).fetchone()
            if row is None:
                return
            retries = int(row["retry_count"]) + 1
            if retries >= 3:
                phase = "failed"
            elif row["phase"] == "generating":
                phase = "cooling"
            else:
                phase = row["phase"]
            connection.execute(
                """
                UPDATE sessions SET phase = ?, retry_count = ?, next_attempt_at = ?,
                    last_error = ?
                WHERE session_id = ? AND generation = ?
                """,
                (phase, retries, now + min(60.0, 5.0 * (2 ** (retries - 1))), code,
                 session_id, generation),
            )

    def pending_sessions(self) -> list[SessionState]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM sessions
                WHERE phase IN ('waiting_completion', 'cooling', 'generating')
                ORDER BY COALESCE(due_at, 0), session_id
                """
            ).fetchall()
        return [state for row in rows if (state := self._state(row)) is not None]

    def acquire_worker_lease(self, owner: str, *, now: float, ttl: float) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT owner, expires_at FROM worker_lease WHERE singleton = 1"
            ).fetchone()
            if row is not None and row["owner"] != owner and row["expires_at"] >= now:
                return False
            connection.execute(
                """
                INSERT INTO worker_lease(singleton, owner, expires_at) VALUES (1, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET owner = excluded.owner,
                    expires_at = excluded.expires_at
                """,
                (owner, now + ttl),
            )
            return True

    def renew_worker_lease(self, owner: str, *, now: float, ttl: float) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE worker_lease SET expires_at = ? WHERE singleton = 1 AND owner = ?",
                (now + ttl, owner),
            )
            return cursor.rowcount == 1

    def release_worker_lease(self, owner: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM worker_lease WHERE singleton = 1 AND owner = ?", (owner,)
            )
