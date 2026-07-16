from pathlib import Path

from thread_recap.service import HookService, INTERNAL_TEXT_MARKER
from thread_recap.store import Store


def _payload(event: str, turn: str, prompt: str | None = None) -> dict[str, str]:
    result = {
        "hook_event_name": event,
        "session_id": "session-1",
        "turn_id": turn,
        "cwd": "/workspace",
    }
    if prompt is not None:
        result["prompt"] = prompt
    return result


def test_hook_service_tracks_normal_turn_until_stop(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    service = HookService(store, now=lambda: 100.0)

    submitted = service.handle(_payload("UserPromptSubmit", "turn-1", "继续"))
    stopped = service.handle(_payload("Stop", "turn-1"))

    assert submitted.wake_worker is True
    assert stopped.wake_worker is True
    state = store.get_session("session-1")
    assert state is not None
    assert state.phase == "waiting_completion"


def test_hook_service_marks_internal_turn_and_never_wakes_recap(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    service = HookService(store, now=lambda: 100.0)

    result = service.handle(
        _payload("UserPromptSubmit", "internal-turn", INTERNAL_TEXT_MARKER)
    )
    stopped = service.handle(_payload("Stop", "internal-turn"))

    assert result.wake_worker is False
    assert stopped.wake_worker is False
