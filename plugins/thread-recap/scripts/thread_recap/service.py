from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .clock import utc_epoch
from .model import HookResult
from .store import Store


INTERNAL_TEXT_MARKER = "[thread-recap:internal:v1]"
INTERNAL_CLIENT_PREFIX = "thread-recap-internal:"


def _contains_internal_marker(value: Any) -> bool:
    if isinstance(value, str):
        return INTERNAL_TEXT_MARKER in value or value.startswith(INTERNAL_CLIENT_PREFIX)
    if isinstance(value, dict):
        return any(_contains_internal_marker(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_internal_marker(child) for child in value)
    return False


class HookService:
    def __init__(self, store: Store, *, now: Callable[[], float] = utc_epoch) -> None:
        self.store = store
        self.now = now

    def handle(self, payload: dict[str, Any]) -> HookResult:
        event = payload.get("hook_event_name")
        session_id = payload.get("session_id")
        if not isinstance(event, str) or not isinstance(session_id, str) or not session_id:
            raise ValueError("hook payload is missing event or session id")

        if event == "SessionStart":
            return HookResult(wake_worker=True)

        turn_id = payload.get("turn_id")
        if not isinstance(turn_id, str) or not turn_id:
            raise ValueError("turn-scoped hook payload is missing turn id")

        if event == "UserPromptSubmit":
            if _contains_internal_marker(payload):
                self.store.record_internal_turn(session_id, turn_id)
                return HookResult()
            self.store.record_user_prompt(session_id, turn_id, now=self.now())
            return HookResult(wake_worker=True)

        if event == "Stop":
            return HookResult(wake_worker=self.store.record_stop(session_id, turn_id))

        return HookResult()
