from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SessionState:
    session_id: str
    thread_id: str | None
    active_turn_id: str | None
    generation: int
    phase: str
    last_activity_at: float | None
    completed_at: float | None
    due_at: float | None
    last_summarized_turn_id: str | None
    retry_count: int
    next_attempt_at: float | None
    last_error: str | None


@dataclass(frozen=True)
class HookResult:
    wake_worker: bool = False
