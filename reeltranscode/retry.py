from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from reeltranscode.config import RetryConfig

T = TypeVar("T")

def run_with_retry(fn: Callable[[], T], retry: RetryConfig) -> T:
    attempt = 0
    delay = retry.backoff_initial_seconds
    while True:
        attempt += 1
        try:
            return fn()
        except Exception:
            if attempt >= retry.max_attempts:
                raise
            time.sleep(delay)
            delay = min(delay * 2, retry.backoff_max_seconds)
