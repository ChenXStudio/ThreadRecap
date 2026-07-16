from __future__ import annotations

import time


def utc_epoch() -> float:
    return time.time()


def monotonic() -> float:
    return time.monotonic()
