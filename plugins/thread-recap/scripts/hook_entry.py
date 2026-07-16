#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from thread_recap.runtime import spawn_detached_worker
from thread_recap.service import HookService
from thread_recap.store import Store


def _log(plugin_data: Path, message: str) -> None:
    try:
        plugin_data.mkdir(parents=True, exist_ok=True)
        with (plugin_data / "hook.log").open("a", encoding="utf-8") as handle:
            handle.write(message + "\n")
    except OSError:
        pass


def main() -> int:
    root_value = os.environ.get("PLUGIN_ROOT", "")
    data_value = os.environ.get("PLUGIN_DATA", "")
    if not root_value or not data_value:
        print("ThreadRecap requires PLUGIN_ROOT and PLUGIN_DATA", file=sys.stderr)
        return 2
    plugin_root, plugin_data = Path(root_value), Path(data_value)
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook payload must be an object")
        result = HookService(Store(plugin_data / "state.db")).handle(payload)
        if result.wake_worker:
            spawn_detached_worker(plugin_root, plugin_data)
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        _log(plugin_data, f"hook_error:{type(error).__name__}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
