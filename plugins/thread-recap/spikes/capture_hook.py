#!/usr/bin/env python3
"""Capture redacted Codex hook payloads for the feasibility probe only."""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any, Mapping


REDACTED = "[REDACTED]"
_SENSITIVE_WORDS = {
    "authorization",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "key",
    "keys",
    "password",
    "passwords",
    "secret",
    "secrets",
    "token",
    "tokens",
}
_SENSITIVE_COMPACT_NAMES = {
    "accesstoken",
    "accesstokens",
    "apikey",
    "apikeys",
    "clientsecret",
    "clientsecrets",
}
_LOCAL_APPEND_LOCK = threading.Lock()


def _key_words(key: str) -> set[str]:
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", key)
    return {
        word.casefold()
        for word in re.findall(r"[A-Za-z0-9]+", separated)
        if word
    }


def _is_sensitive_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    words = _key_words(key)
    collapsed = "".join(words)
    return bool(words & _SENSITIVE_WORDS) or collapsed in _SENSITIVE_COMPACT_NAMES


def redact_sensitive(value: Any) -> Any:
    """Return a recursively redacted copy of JSON-compatible data."""

    if isinstance(value, dict):
        return {
            key: REDACTED if _is_sensitive_key(key) else redact_sensitive(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(child) for child in value]
    if isinstance(value, tuple):
        return [redact_sensitive(child) for child in value]
    return value


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Lock the target and append one JSONL record with one write syscall."""

    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
    flags |= getattr(os, "O_BINARY", 0)
    with _LOCAL_APPEND_LOCK:
        descriptor = os.open(path, flags, 0o600)
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                written = os.write(descriptor, encoded)
                if written != len(encoded):
                    raise OSError("incomplete JSONL append")
                os.fsync(descriptor)
            finally:
                os.lseek(descriptor, 0, os.SEEK_SET)
                if os.name == "nt":
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _transcript_snapshot(
    transcript_path: object, current_turn_id: object
) -> dict[str, bool]:
    snapshot = {
        "exists": False,
        "nonempty": False,
        "ends_with_newline": False,
        "jsonl_valid": False,
        "current_turn_seen": False,
        "current_turn_completed": False,
    }
    if not isinstance(transcript_path, str) or not transcript_path:
        return snapshot

    try:
        path = Path(transcript_path)
        snapshot["exists"] = path.is_file()
        if not snapshot["exists"]:
            return snapshot
        raw = path.read_bytes()
        snapshot["nonempty"] = bool(raw)
        snapshot["ends_with_newline"] = raw.endswith(b"\n")
        lines = [line for line in raw.decode("utf-8").splitlines() if line.strip()]
        records = [json.loads(line) for line in lines]
        snapshot["jsonl_valid"] = bool(records) and all(
            isinstance(record, dict) for record in records
        )
        if not snapshot["jsonl_valid"] or not isinstance(current_turn_id, str):
            return snapshot
        for record in records:
            payload = record.get("payload")
            if not isinstance(payload, dict) or payload.get("turn_id") != current_turn_id:
                continue
            snapshot["current_turn_seen"] = True
            if record.get("type") == "event_msg" and payload.get("type") == "task_complete":
                snapshot["current_turn_completed"] = True
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        pass
    return snapshot


def build_record(
    payload: dict[str, Any], *, captured_at: str | None = None
) -> dict[str, Any]:
    record = redact_sensitive(payload)
    record["captured_at"] = captured_at or datetime.now(timezone.utc).isoformat()
    if payload.get("hook_event_name") == "Stop":
        record["transcript_snapshot"] = _transcript_snapshot(
            payload.get("transcript_path"), payload.get("turn_id")
        )
    return record


def capture(
    source: IO[str],
    *,
    env: Mapping[str, str] | None = None,
    captured_at: str | None = None,
) -> Path:
    environment = os.environ if env is None else env
    raw_plugin_data = environment.get("PLUGIN_DATA", "")
    if not raw_plugin_data.strip():
        raise ValueError("PLUGIN_DATA is required")
    payload = json.load(source)
    if not isinstance(payload, dict):
        raise ValueError("hook payload must be a JSON object")
    output = Path(raw_plugin_data) / "hook-probe.jsonl"
    append_jsonl(output, build_record(payload, captured_at=captured_at))
    return output


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments:
        print("capture_hook accepts no command-line arguments", file=sys.stderr)
        return 2

    try:
        capture(sys.stdin)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"capture_hook failed: {type(error).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
