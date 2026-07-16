#!/usr/bin/env python3
"""Probe stable Codex app-server writeback to an existing thread."""

from __future__ import annotations

import argparse
import json
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import IO, Any, Callable


VISIBLE_TRIGGER_TEXT = "⏱ 会话已冷却，使用 $thread-recap 生成阶段摘要"
INTERNAL_TEXT_MARKER = "[thread-recap:internal:v1]"
INTERNAL_CLIENT_ID = "thread-recap-internal:feasibility-v1"
_SOURCE_KINDS = ["cli", "exec", "appServer"]
_EOF = object()


class ProbeError(RuntimeError):
    """A sanitized feasibility failure safe to include in a report."""


def _contains_internal_string(value: Any) -> bool:
    if isinstance(value, str):
        return (
            value.startswith("thread-recap-internal:")
            or VISIBLE_TRIGGER_TEXT in value
            or INTERNAL_TEXT_MARKER in value
        )
    if isinstance(value, dict):
        return any(_contains_internal_string(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_internal_string(child) for child in value)
    return False


def is_internal_trigger(payload: Any) -> bool:
    return _contains_internal_string(payload)


class JsonlRpcClient:
    """Small JSONL client that pairs IDs while retaining notifications."""

    def __init__(self, reader: IO[str], writer: IO[str]) -> None:
        self._reader = reader
        self._writer = writer
        self._next_id = 1
        self._messages: queue.Queue[object] = queue.Queue()
        self._pending_responses: dict[object, dict[str, Any]] = {}
        self._notifications: list[dict[str, Any]] = []
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def _read_loop(self) -> None:
        try:
            for line in self._reader:
                if line.strip():
                    self._messages.put(json.loads(line))
        except (OSError, json.JSONDecodeError) as error:
            self._messages.put(error)
        finally:
            self._messages.put(_EOF)

    def _send(self, message: dict[str, Any]) -> None:
        self._writer.write(json.dumps(message, ensure_ascii=False) + "\n")
        self._writer.flush()

    def _receive(self, deadline: float) -> dict[str, Any]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProbeError("app_server_timeout")
        try:
            message = self._messages.get(timeout=remaining)
        except queue.Empty as error:
            raise ProbeError("app_server_timeout") from error
        if message is _EOF:
            raise ProbeError("app_server_closed")
        if isinstance(message, BaseException):
            raise ProbeError("invalid_app_server_jsonl") from message
        if not isinstance(message, dict):
            raise ProbeError("invalid_app_server_message")
        return message

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float,
    ) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        request = {"method": method, "id": request_id, "params": params or {}}
        self._send(request)
        deadline = time.monotonic() + timeout

        while True:
            response = self._pending_responses.pop(request_id, None)
            if response is None:
                response = self._receive(deadline)
            if "id" not in response:
                self._notifications.append(response)
                continue
            if response["id"] != request_id:
                self._pending_responses[response["id"]] = response
                continue
            if "error" in response:
                raise ProbeError(f"app_server_error:{method}")
            result = response.get("result")
            if not isinstance(result, dict):
                raise ProbeError(f"invalid_app_server_result:{method}")
            return result

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"method": method, "params": params or {}})

    def wait_for_notification(
        self,
        method: str,
        predicate: Callable[[dict[str, Any]], bool],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            for index, notification in enumerate(self._notifications):
                if notification.get("method") == method:
                    params = notification.get("params")
                    if isinstance(params, dict) and predicate(params):
                        self._notifications.pop(index)
                        return params
            message = self._receive(deadline)
            if "id" in message:
                self._pending_responses[message["id"]] = message
            else:
                self._notifications.append(message)

    def wait_for_turn_completed(
        self, thread_id: str, turn_id: str, *, timeout: float
    ) -> dict[str, Any]:
        return self.wait_for_notification(
            "turn/completed",
            lambda params: params.get("threadId") == thread_id
            and isinstance(params.get("turn"), dict)
            and params["turn"].get("id") == turn_id,
            timeout=timeout,
        )


def _find_thread(client: Any, session_id: str, timeout: float) -> dict[str, Any]:
    cursor: str | None = None
    candidates: dict[str, dict[str, Any]] = {}
    for _ in range(100):
        params: dict[str, Any] = {
            "sourceKinds": _SOURCE_KINDS,
            "archived": False,
            "limit": 100,
            "sortKey": "updated_at",
        }
        if cursor is not None:
            params["cursor"] = cursor
        page = client.request("thread/list", params, timeout=timeout)
        data = page.get("data")
        if not isinstance(data, list):
            raise ProbeError("invalid_thread_list")
        for thread in data:
            if not isinstance(thread, dict) or not isinstance(thread.get("id"), str):
                continue
            if thread["id"] == session_id or thread.get("sessionId") == session_id:
                candidates[thread["id"]] = thread
        cursor = page.get("nextCursor")
        if not isinstance(cursor, str) or not cursor:
            break

    exact = [thread for thread in candidates.values() if thread["id"] == session_id]
    if len(exact) == 1:
        return exact[0]
    if len(candidates) == 1:
        return next(iter(candidates.values()))
    if not candidates:
        raise ProbeError("session_thread_mapping_not_found")
    raise ProbeError("session_thread_mapping_ambiguous")


def _marked_result_persisted(
    thread: dict[str, Any], turn_id: str
) -> tuple[bool, bool]:
    turns = thread.get("turns")
    if not isinstance(turns, list):
        return False, False
    turn = next(
        (
            item
            for item in turns
            if isinstance(item, dict) and item.get("id") == turn_id
        ),
        None,
    )
    if turn is None or not isinstance(turn.get("items"), list):
        return False, False
    items = turn["items"]
    marker = any(
        isinstance(item, dict)
        and item.get("type") == "userMessage"
        and item.get("clientId") == INTERNAL_CLIENT_ID
        for item in items
    )
    result = marker and any(
        isinstance(item, dict)
        and item.get("type") == "agentMessage"
        and isinstance(item.get("text"), str)
        and bool(item["text"].strip())
        for item in items
    )
    return marker, result


def probe_existing_thread(
    client: Any, *, session_id: str, timeout: float = 300
) -> dict[str, bool]:
    initialized = client.request(
        "initialize",
        {
            "clientInfo": {
                "name": "thread_recap_feasibility_probe",
                "title": "ThreadRecap Feasibility Probe",
                "version": "0.1.0",
            }
        },
        timeout=timeout,
    )
    client.notify("initialized", {})
    thread = _find_thread(client, session_id, timeout)
    thread_id = thread["id"]
    session_mapping = thread_id == session_id or thread.get("sessionId") == session_id
    non_ephemeral = thread.get("ephemeral") is False

    resumed = client.request(
        "thread/resume", {"threadId": thread_id}, timeout=timeout
    )
    resumed_thread = resumed.get("thread")
    resumed_same_thread = (
        isinstance(resumed_thread, dict) and resumed_thread.get("id") == thread_id
    )

    started = client.request(
        "turn/start",
        {
            "threadId": thread_id,
            "clientUserMessageId": INTERNAL_CLIENT_ID,
            "input": [{"type": "text", "text": VISIBLE_TRIGGER_TEXT}],
        },
        timeout=timeout,
    )
    turn = started.get("turn")
    if not isinstance(turn, dict) or not isinstance(turn.get("id"), str):
        raise ProbeError("invalid_turn_start")
    turn_id = turn["id"]
    completed = client.wait_for_turn_completed(thread_id, turn_id, timeout=timeout)
    completed_turn = completed.get("turn")
    turn_completed = (
        isinstance(completed_turn, dict)
        and completed_turn.get("id") == turn_id
        and completed_turn.get("status") == "completed"
    )

    read = client.request(
        "thread/read", {"threadId": thread_id, "includeTurns": True}, timeout=timeout
    )
    read_thread = read.get("thread")
    marker_persisted, result_in_marked_turn = (
        _marked_result_persisted(read_thread, turn_id)
        if isinstance(read_thread, dict) and read_thread.get("id") == thread_id
        else (False, False)
    )

    return {
        "initialized": isinstance(initialized, dict),
        "session_thread_mapping": session_mapping,
        "thread_id_matches_session_id": thread_id == session_id,
        "non_ephemeral": non_ephemeral,
        "resumed_same_thread": resumed_same_thread,
        "turn_completed": turn_completed,
        "internal_client_id_persisted": marker_persisted,
        "result_in_marked_turn": result_in_marked_turn,
    }


def _codex_path(explicit: Path | None) -> str:
    if explicit is not None:
        if not explicit.is_file():
            raise ProbeError("codex_not_found")
        return str(explicit)
    discovered = shutil.which("codex")
    if discovered is None:
        raise ProbeError("codex_not_found")
    return discovered


def _run(args: argparse.Namespace) -> dict[str, Any]:
    codex = _codex_path(args.codex)
    try:
        process = subprocess.Popen(
            [codex, "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
    except OSError as error:
        raise ProbeError("codex_start_failed") from error
    if process.stdin is None or process.stdout is None:
        process.kill()
        raise ProbeError("codex_stdio_unavailable")
    client = JsonlRpcClient(process.stdout, process.stdin)
    try:
        checks = probe_existing_thread(
            client, session_id=args.session_id, timeout=args.timeout
        )
        return {"go": all(checks.values()), "checks": checks}
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", type=Path)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args(argv)
    try:
        result = _run(args)
    except ProbeError as error:
        result = {"go": False, "error": str(error)}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["go"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
