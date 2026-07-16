from pathlib import Path

from thread_recap.app_server import ThreadSnapshot
from thread_recap.runtime import RecapWorker
from thread_recap.store import Store


class FakeGateway:
    def __init__(self, snapshots: list[ThreadSnapshot]) -> None:
        self.snapshots = snapshots
        self.submitted: list[tuple[str, str, int]] = []

    def read_session(self, session_id: str) -> ThreadSnapshot:
        return self.snapshots.pop(0)

    def submit_recap(self, thread_id: str, covered_turn_id: str, generation: int) -> str:
        self.submitted.append((thread_id, covered_turn_id, generation))
        return "summary-turn"


def _snapshot(status: str, completed_at: float = 10.0) -> ThreadSnapshot:
    return ThreadSnapshot.from_thread(
        {
            "id": "thread-1",
            "turns": [
                {
                    "id": "turn-1",
                    "status": status,
                    "completedAt": completed_at if status == "completed" else None,
                    "items": [{"type": "userMessage", "clientId": None}],
                }
            ],
        }
    )


def test_worker_starts_cooldown_only_after_app_server_completion(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    state = store.record_user_prompt("session-1", "turn-1", now=1.0)
    store.record_stop("session-1", "turn-1")
    worker = RecapWorker(store, FakeGateway([_snapshot("inProgress")]), now=lambda: 20.0)

    worker.process_once()

    current = store.get_session("session-1")
    assert current is not None
    assert current.generation == state.generation
    assert current.phase == "waiting_completion"
    assert current.due_at is None


def test_worker_generates_once_after_five_minutes_of_completed_idle(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    state = store.record_user_prompt("session-1", "turn-1", now=1.0)
    store.record_stop("session-1", "turn-1")
    gateway = FakeGateway([_snapshot("completed", 10.0), _snapshot("completed", 10.0)])
    clock = iter([20.0, 310.0])
    worker = RecapWorker(store, gateway, now=lambda: next(clock))

    worker.process_once()
    worker.process_once()

    assert gateway.submitted == [("thread-1", "turn-1", state.generation)]
    current = store.get_session("session-1")
    assert current is not None
    assert current.phase == "idle"
    assert current.last_summarized_turn_id == "turn-1"


def test_worker_refuses_due_summary_when_a_newer_normal_turn_exists(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    state = store.record_user_prompt("session-1", "turn-1", now=1.0)
    store.record_stop("session-1", "turn-1")
    store.mark_turn_completed(
        "session-1", state.generation, "thread-1", "turn-1", completed_at=10.0
    )
    newer = ThreadSnapshot.from_thread(
        {
            "id": "thread-1",
            "turns": [
                {"id": "turn-1", "status": "completed", "items": [{"type": "userMessage"}]},
                {"id": "turn-2", "status": "completed", "items": [{"type": "userMessage"}]},
            ],
        }
    )
    gateway = FakeGateway([newer])

    RecapWorker(store, gateway, now=lambda: 310.0).process_once()

    assert gateway.submitted == []
    assert store.get_session("session-1").phase == "superseded"


def test_detached_worker_uses_platform_detachment_and_explicit_paths(
    tmp_path: Path, monkeypatch
) -> None:
    import os
    import subprocess
    from thread_recap import runtime

    root, data = tmp_path / "root with space", tmp_path / "data 数据"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "worker.py").write_text("", encoding="utf-8")
    calls: list[tuple[list[str], dict[str, object]]] = []

    class FakeProcess:
        pass

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        calls.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    runtime.spawn_detached_worker(root, data)

    command, kwargs = calls[0]
    assert command[1:] == [str(root / "scripts" / "worker.py"), "--plugin-data", str(data)]
    if os.name == "nt":
        assert kwargs["creationflags"] & subprocess.DETACHED_PROCESS
        assert kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
    else:
        assert kwargs["start_new_session"] is True


def test_worker_recovers_completed_recap_without_generating_a_duplicate(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "state.db")
    state = store.record_user_prompt("session-1", "turn-1", now=1.0)
    store.record_stop("session-1", "turn-1")
    store.mark_turn_completed(
        "session-1", state.generation, "thread-1", "turn-1", completed_at=10.0
    )
    store.claim_summary("session-1", state.generation, now=310.0)
    recovered = ThreadSnapshot.from_thread(
        {
            "id": "thread-1",
            "turns": [
                {
                    "id": "turn-1",
                    "status": "completed",
                    "items": [{"type": "userMessage"}],
                },
                {
                    "id": "summary-turn",
                    "status": "completed",
                    "items": [
                        {
                            "type": "userMessage",
                            "clientId": f"thread-recap-internal:{state.generation}:turn-1",
                        }
                    ],
                },
            ],
        }
    )
    gateway = FakeGateway([recovered])

    RecapWorker(store, gateway, now=lambda: 311.0).process_once()

    assert gateway.submitted == []
    current = store.get_session("session-1")
    assert current is not None
    assert current.phase == "idle"
    assert current.last_summarized_turn_id == "turn-1"
