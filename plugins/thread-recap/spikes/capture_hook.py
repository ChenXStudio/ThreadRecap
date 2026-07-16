#!/usr/bin/env python3
"""Capture redacted Codex hook payloads for the feasibility probe only."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any, Iterator


REDACTED = "[REDACTED]"
_SENSITIVE_WORDS = {"authorization", "cookie", "key", "secret", "token"}
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
    return bool(words & _SENSITIVE_WORDS) or any(
        collapsed.endswith(word) for word in _SENSITIVE_WORDS
    )


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


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_name(path.name + ".lock")
    with _LOCAL_APPEND_LOCK, lock_path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            # Appending the sentinel avoids reading byte zero while another
            # process has that byte locked. The lock file is probe-only data.
            handle.seek(0, os.SEEK_END)
            handle.write(b"\0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append one complete JSONL record while excluding concurrent writers."""

    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    with _exclusive_lock(path):
        with path.open("ab") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())


def _transcript_snapshot(transcript_path: object) -> dict[str, bool]:
    snapshot = {"exists": False, "nonempty": False, "ends_with_newline": False}
    if not isinstance(transcript_path, str) or not transcript_path:
        return snapshot

    try:
        path = Path(transcript_path)
        size = path.stat().st_size
        snapshot["exists"] = path.is_file()
        snapshot["nonempty"] = snapshot["exists"] and size > 0
        if snapshot["nonempty"]:
            with path.open("rb") as handle:
                handle.seek(-1, os.SEEK_END)
                snapshot["ends_with_newline"] = handle.read(1) == b"\n"
    except OSError:
        pass
    return snapshot


def build_record(
    payload: dict[str, Any], *, captured_at: str | None = None
) -> dict[str, Any]:
    record = redact_sensitive(payload)
    record["captured_at"] = captured_at or datetime.now(timezone.utc).isoformat()
    if payload.get("hook_event_name") == "Stop":
        record["transcript_snapshot"] = _transcript_snapshot(
            payload.get("transcript_path")
        )
    return record


def capture(
    source: IO[str], *, plugin_data: Path, captured_at: str | None = None
) -> Path:
    payload = json.load(source)
    if not isinstance(payload, dict):
        raise ValueError("hook payload must be a JSON object")
    output = plugin_data / "hook-probe.jsonl"
    append_jsonl(output, build_record(payload, captured_at=captured_at))
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--plugin-data",
        type=Path,
        help="Writable probe directory (defaults to PLUGIN_DATA).",
    )
    args = parser.parse_args(argv)
    plugin_data = args.plugin_data
    if plugin_data is None:
        raw_plugin_data = os.environ.get("PLUGIN_DATA", "")
        if not raw_plugin_data.strip():
            parser.error("PLUGIN_DATA is required")
        plugin_data = Path(raw_plugin_data)

    try:
        capture(sys.stdin, plugin_data=plugin_data)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"capture_hook failed: {type(error).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
