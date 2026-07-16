from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from .app_server import AppServerError, CodexGateway
from .clock import utc_epoch
from .store import Store


class RecapWorker:
    def __init__(
        self,
        store: Store,
        gateway: CodexGateway,
        *,
        now: Callable[[], float] = utc_epoch,
    ) -> None:
        self.store = store
        self.gateway = gateway
        self.now = now

    def process_once(self) -> float | None:
        current_time = self.now()
        states = self.store.pending_sessions()
        for state in states:
            try:
                if state.next_attempt_at is not None and state.next_attempt_at > current_time:
                    continue
                if state.phase == "waiting_completion":
                    snapshot = self.gateway.read_session(state.session_id)
                    turn = snapshot.turn(state.active_turn_id or "")
                    if turn.status == "completed":
                        self.store.mark_turn_completed(
                            state.session_id,
                            state.generation,
                            snapshot.thread_id,
                            turn.turn_id,
                            # completedAt is stable in current schemas. Falling back
                            # to observation time is conservative on older versions.
                            completed_at=turn.completed_at or current_time,
                        )
                    elif turn.status in {"failed", "interrupted"}:
                        self.store.mark_superseded(state.session_id, state.generation)
                    continue

                if state.phase == "generating":
                    snapshot = self.gateway.read_session(state.session_id)
                    recap = snapshot.recap_turn(
                        state.active_turn_id or "", state.generation
                    )
                    if recap is not None and recap.status == "completed":
                        self.store.finish_summary(
                            state.session_id, state.generation, recap.turn_id
                        )
                    elif recap is None or recap.status in {"failed", "interrupted"}:
                        self.store.record_failure(
                            state.session_id,
                            state.generation,
                            "worker_recovered_generating",
                            now=current_time,
                        )
                    continue

                if state.due_at is None or state.due_at > current_time:
                    continue
                snapshot = self.gateway.read_session(state.session_id)
                turn = snapshot.turn(state.active_turn_id or "")
                if (
                    turn.status != "completed"
                    or snapshot.latest_normal_turn_id != state.active_turn_id
                ):
                    self.store.mark_superseded(state.session_id, state.generation)
                    continue
                if not self.store.claim_summary(state.session_id, state.generation, now=current_time):
                    continue
                summary_turn = self.gateway.submit_recap(
                    snapshot.thread_id, state.active_turn_id or "", state.generation
                )
                self.store.finish_summary(state.session_id, state.generation, summary_turn)
            except AppServerError as error:
                self.store.record_failure(
                    state.session_id, state.generation, str(error), now=current_time
                )

        remaining = self.store.pending_sessions()
        if not remaining:
            return None
        delays: list[float] = []
        for state in remaining:
            target = state.next_attempt_at or state.due_at
            delays.append(2.0 if target is None else max(0.5, min(5.0, target - current_time)))
        return min(delays)

    def run(self) -> int:
        owner = str(uuid.uuid4())
        lease_ttl = 20.0
        if not self.store.acquire_worker_lease(owner, now=self.now(), ttl=lease_ttl):
            return 0
        heartbeat_stop = threading.Event()
        lease_lost = threading.Event()

        def heartbeat() -> None:
            while not heartbeat_stop.wait(5.0):
                if not self.store.renew_worker_lease(
                    owner, now=self.now(), ttl=lease_ttl
                ):
                    lease_lost.set()
                    return

        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        heartbeat_thread.start()
        try:
            while True:
                delay = self.process_once()
                if delay is None:
                    return 0
                if lease_lost.is_set():
                    return 0
                time.sleep(delay)
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=6.0)
            self.store.release_worker_lease(owner)


def spawn_detached_worker(plugin_root: Path, plugin_data: Path) -> None:
    worker = plugin_root / "scripts" / "worker.py"
    log_path = plugin_data / "worker.log"
    plugin_data.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        )
    else:
        kwargs["start_new_session"] = True
    with log_path.open("a", encoding="utf-8") as log:
        subprocess.Popen(
            [sys.executable, str(worker), "--plugin-data", str(plugin_data)],
            stdout=log,
            stderr=log,
            **kwargs,
        )
