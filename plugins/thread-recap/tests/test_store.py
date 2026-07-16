from pathlib import Path
import sqlite3

from thread_recap.store import Store


def test_completion_starts_cooldown_and_new_activity_invalidates_old_generation(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "plugin data" / "state.db")
    first = store.record_user_prompt("session-1", "turn-1", now=100.0)

    assert store.record_stop("session-1", "turn-1") is True
    assert store.mark_turn_completed(
        "session-1", first.generation, "thread-1", "turn-1", completed_at=120.0
    ) is True
    cooling = store.get_session("session-1")
    assert cooling is not None
    assert cooling.due_at == 420.0
    assert cooling.phase == "cooling"

    second = store.record_user_prompt("session-1", "turn-2", now=419.0)
    assert second.generation == first.generation + 1
    assert store.claim_summary("session-1", first.generation, now=420.0) is False


def test_internal_turn_is_ignored_and_its_stop_does_not_schedule(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    state = store.record_user_prompt("session-1", "turn-1", now=1.0)

    store.record_internal_turn("session-1", "internal-turn")

    assert store.record_stop("session-1", "internal-turn") is False
    assert store.get_session("session-1") == state


def test_summary_success_requires_same_generation(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    state = store.record_user_prompt("session-1", "turn-1", now=1.0)
    store.record_stop("session-1", "turn-1")
    store.mark_turn_completed(
        "session-1", state.generation, "thread-1", "turn-1", completed_at=2.0
    )
    assert store.claim_summary("session-1", state.generation, now=302.0)

    store.record_user_prompt("session-1", "turn-2", now=303.0)

    assert store.finish_summary("session-1", state.generation, "summary-turn") is False
    current = store.get_session("session-1")
    assert current is not None
    assert current.active_turn_id == "turn-2"
    assert current.last_summarized_turn_id is None


def test_global_worker_lease_is_single_owner_and_recoverable(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")

    assert store.acquire_worker_lease("worker-a", now=10.0, ttl=30.0)
    assert not store.acquire_worker_lease("worker-b", now=20.0, ttl=30.0)
    assert store.acquire_worker_lease("worker-b", now=41.0, ttl=30.0)


def test_transient_completion_read_failure_keeps_waiting_phase(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    state = store.record_user_prompt("session-1", "turn-1", now=1.0)
    store.record_stop("session-1", "turn-1")

    store.record_failure("session-1", state.generation, "app_server_timeout", now=2.0)

    current = store.get_session("session-1")
    assert current is not None
    assert current.phase == "waiting_completion"
    assert current.retry_count == 1


def test_store_migrates_pre_activity_timestamp_database(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_version(version INTEGER NOT NULL);
            INSERT INTO schema_version VALUES (1);
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY, thread_id TEXT, active_turn_id TEXT,
                generation INTEGER NOT NULL DEFAULT 0, phase TEXT NOT NULL DEFAULT 'idle',
                completed_at REAL, due_at REAL, last_summarized_turn_id TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0, next_attempt_at REAL, last_error TEXT
            );
            """
        )

    store = Store(path)

    assert store.record_user_prompt("session-1", "turn-1", now=42.0).last_activity_at == 42.0
