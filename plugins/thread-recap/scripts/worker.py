#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from thread_recap.app_server import CodexGateway
from thread_recap.runtime import RecapWorker
from thread_recap.store import Store


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-data", required=True, type=Path)
    args = parser.parse_args()
    return RecapWorker(Store(args.plugin_data / "state.db"), CodexGateway()).run()


if __name__ == "__main__":
    raise SystemExit(main())
