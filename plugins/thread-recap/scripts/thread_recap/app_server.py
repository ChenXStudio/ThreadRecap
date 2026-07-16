from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Callable

from .service import INTERNAL_CLIENT_PREFIX, INTERNAL_TEXT_MARKER


class AppServerError(RuntimeError):
    """A bounded diagnostic code without conversation content."""


_EOF = object()
THREAD_SOURCE_KINDS = ["cli", "vscode", "exec", "appServer", "unknown"]
WINDOWS_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def app_server_process_options(platform_name: str = os.name) -> dict[str, int]:
    if platform_name == "nt":
        return {"creationflags": WINDOWS_CREATE_NO_WINDOW}
    return {}


@dataclass(frozen=True)
class TurnSnapshot:
    turn_id: str
    status: str
    completed_at: float | None
    internal: bool
    has_user_message: bool
    client_ids: tuple[str, ...]


@dataclass(frozen=True)
class ThreadSnapshot:
    thread_id: str
    turns: tuple[TurnSnapshot, ...]

    @classmethod
    def from_thread(cls, thread: dict[str, Any]) -> "ThreadSnapshot":
        thread_id = thread.get("id")
        if not isinstance(thread_id, str):
            raise AppServerError("invalid_thread")
        parsed: list[TurnSnapshot] = []
        raw_turns = thread.get("turns")
        if not isinstance(raw_turns, list):
            raise AppServerError("invalid_thread_turns")
        for raw in raw_turns:
            if not isinstance(raw, dict):
                continue
            turn_id, status = raw.get("id"), raw.get("status")
            if not isinstance(turn_id, str) or not isinstance(status, str):
                continue
            completed = raw.get("completedAt")
            completed_at = float(completed) if isinstance(completed, (int, float)) else None
            items = raw.get("items") if isinstance(raw.get("items"), list) else []
            user_items = [item for item in items if isinstance(item, dict) and item.get("type") == "userMessage"]
            client_ids = tuple(
                item["clientId"]
                for item in user_items
                if isinstance(item.get("clientId"), str)
            )
            internal = any(
                client_id.startswith(INTERNAL_CLIENT_PREFIX) for client_id in client_ids
            )
            parsed.append(
                TurnSnapshot(
                    turn_id, status, completed_at, internal, bool(user_items), client_ids
                )
            )
        return cls(thread_id, tuple(parsed))

    def turn(self, turn_id: str) -> TurnSnapshot:
        for turn in self.turns:
            if turn.turn_id == turn_id:
                return turn
        raise AppServerError("turn_not_found")

    @property
    def latest_normal_turn_id(self) -> str | None:
        for turn in reversed(self.turns):
            if turn.has_user_message and not turn.internal:
                return turn.turn_id
        return None

    def recap_turn(self, covered_turn_id: str, generation: int) -> TurnSnapshot | None:
        expected = recap_client_id(covered_turn_id, generation)
        return next(
            (turn for turn in self.turns if expected in turn.client_ids), None
        )


def recap_client_id(covered_turn_id: str, generation: int) -> str:
    return f"{INTERNAL_CLIENT_PREFIX}{generation}:{covered_turn_id}"


class AppServerClient:
    def __init__(self, reader: IO[str], writer: IO[str]) -> None:
        self.reader = reader
        self.writer = writer
        self.next_id = 1
        self.messages: queue.Queue[object] = queue.Queue()
        self.pending: dict[object, dict[str, Any]] = {}
        self.notifications: list[dict[str, Any]] = []
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _read_loop(self) -> None:
        try:
            for line in self.reader:
                if line.strip():
                    self.messages.put(json.loads(line))
        except (OSError, json.JSONDecodeError) as error:
            self.messages.put(error)
        finally:
            self.messages.put(_EOF)

    def _send(self, message: dict[str, Any]) -> None:
        self.writer.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.writer.flush()

    def _receive(self, deadline: float) -> dict[str, Any]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AppServerError("app_server_timeout")
        try:
            message = self.messages.get(timeout=remaining)
        except queue.Empty as error:
            raise AppServerError("app_server_timeout") from error
        if message is _EOF:
            raise AppServerError("app_server_closed")
        if isinstance(message, BaseException) or not isinstance(message, dict):
            raise AppServerError("invalid_app_server_message")
        return message

    def request(
        self, method: str, params: dict[str, Any] | None = None, *, timeout: float
    ) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        self._send({"method": method, "id": request_id, "params": params or {}})
        deadline = time.monotonic() + timeout
        while True:
            message = self.pending.pop(request_id, None) or self._receive(deadline)
            if "id" not in message:
                self.notifications.append(message)
                continue
            if message["id"] != request_id:
                self.pending[message["id"]] = message
                continue
            if "error" in message:
                raise AppServerError(f"app_server_error:{method}")
            result = message.get("result")
            if not isinstance(result, dict):
                raise AppServerError(f"invalid_app_server_result:{method}")
            return result

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"method": method, "params": params or {}})

    def wait_for(
        self,
        method: str,
        predicate: Callable[[dict[str, Any]], bool],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            for index, notification in enumerate(self.notifications):
                params = notification.get("params")
                if notification.get("method") == method and isinstance(params, dict) and predicate(params):
                    self.notifications.pop(index)
                    return params
            message = self._receive(deadline)
            if "id" in message:
                self.pending[message["id"]] = message
            else:
                self.notifications.append(message)


def discover_codex(env: dict[str, str] | None = None) -> str:
    environment = os.environ if env is None else env
    override = environment.get("THREAD_RECAP_CODEX")
    if override:
        path = Path(override)
        if path.is_file():
            return str(path)
        raise AppServerError("codex_override_not_found")
    discovered = shutil.which("codex")
    if discovered is None:
        raise AppServerError("codex_not_found")
    return discovered


class _Connection:
    def __init__(self, codex: str, timeout: float) -> None:
        self.timeout = timeout
        try:
            self.process = subprocess.Popen(
                [codex, "app-server", "--listen", "stdio://"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
                **app_server_process_options(),
            )
        except OSError as error:
            raise AppServerError("codex_start_failed") from error
        if self.process.stdin is None or self.process.stdout is None:
            self.close()
            raise AppServerError("codex_stdio_unavailable")
        self.client = AppServerClient(self.process.stdout, self.process.stdin)
        self.client.request(
            "initialize",
            {"clientInfo": {"name": "thread_recap", "title": "ThreadRecap", "version": "0.1.0"}},
            timeout=timeout,
        )
        self.client.notify("initialized", {})

    def close(self) -> None:
        process = getattr(self, "process", None)
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def __enter__(self) -> AppServerClient:
        return self.client

    def __exit__(self, *_: object) -> None:
        self.close()


class CodexGateway:
    def __init__(self, codex: str | None = None, *, timeout: float = 60.0) -> None:
        self.codex = codex or discover_codex()
        self.timeout = timeout

    def _find_thread(self, client: AppServerClient, session_id: str) -> str:
        cursor: str | None = None
        candidates: set[str] = set()
        for _ in range(100):
            params: dict[str, Any] = {
                "sourceKinds": THREAD_SOURCE_KINDS,
                "archived": False,
                "limit": 100,
                "sortKey": "updated_at",
            }
            if cursor:
                params["cursor"] = cursor
            page = client.request("thread/list", params, timeout=self.timeout)
            data = page.get("data")
            if not isinstance(data, list):
                raise AppServerError("invalid_thread_list")
            for thread in data:
                if not isinstance(thread, dict) or not isinstance(thread.get("id"), str):
                    continue
                if thread["id"] == session_id or thread.get("sessionId") == session_id:
                    candidates.add(thread["id"])
            cursor = page.get("nextCursor")
            if not isinstance(cursor, str) or not cursor:
                break
        if len(candidates) != 1:
            raise AppServerError("thread_mapping_not_found" if not candidates else "thread_mapping_ambiguous")
        return next(iter(candidates))

    def read_session(self, session_id: str) -> ThreadSnapshot:
        with _Connection(self.codex, self.timeout) as client:
            thread_id = self._find_thread(client, session_id)
            result = client.request(
                "thread/read", {"threadId": thread_id, "includeTurns": True}, timeout=self.timeout
            )
        thread = result.get("thread")
        if not isinstance(thread, dict):
            raise AppServerError("invalid_thread_read")
        return ThreadSnapshot.from_thread(thread)

    def submit_recap(self, thread_id: str, covered_turn_id: str, generation: int) -> str:
        client_id = recap_client_id(covered_turn_id, generation)
        prompt = (
            f"{INTERNAL_TEXT_MARKER}\n"
            "$thread-recap\n"
            "本任务已经连续空闲五分钟。请生成一份简洁的阶段总结并写在本任务底部。"
            "若已有 ThreadRecap，总结其后的新增普通回合并刷新整体状态。"
            f"最后必须原样输出：[thread-recap:summary:v1 covered-through={covered_turn_id}]"
        )
        with _Connection(self.codex, max(self.timeout, 300.0)) as client:
            resumed = client.request("thread/resume", {"threadId": thread_id}, timeout=self.timeout)
            thread = resumed.get("thread")
            if not isinstance(thread, dict) or thread.get("id") != thread_id:
                raise AppServerError("thread_resume_mismatch")
            started = client.request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "clientUserMessageId": client_id,
                    "input": [{"type": "text", "text": prompt}],
                },
                timeout=self.timeout,
            )
            turn = started.get("turn")
            if not isinstance(turn, dict) or not isinstance(turn.get("id"), str):
                raise AppServerError("invalid_turn_start")
            turn_id = turn["id"]
            completed = client.wait_for(
                "turn/completed",
                lambda params: params.get("threadId") == thread_id
                and isinstance(params.get("turn"), dict)
                and params["turn"].get("id") == turn_id,
                timeout=max(self.timeout, 300.0),
            )
            completed_turn = completed.get("turn")
            if not isinstance(completed_turn, dict) or completed_turn.get("status") != "completed":
                raise AppServerError("recap_turn_not_completed")
            return turn_id
