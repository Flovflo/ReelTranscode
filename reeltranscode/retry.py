from __future__ import annotations

import time
from collections.abc import Callable

from reeltranscode.config import RetryConfig


def run_with_retry(fn: Callable[[], None], retry: RetryConfig) -> None:
    attempt = 0
    delay = retry.backoff_initial_seconds
    while True:
        attempt += 1
        try:
            fn()
            return
        except Exception:
            if attempt >= retry.max_attempts:
                raise
            time.sleep(delay)
            delay = min(delay * 2, retry.backoff_max_seconds)
