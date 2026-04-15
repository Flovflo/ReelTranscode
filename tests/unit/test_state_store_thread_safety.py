from __future__ import annotations

import threading
from pathlib import Path

from reeltranscode.state_store import StateStore


def test_runtime_state_reads_and_writes_are_serialized(tmp_path: Path):
    state = StateStore(tmp_path / "state.db")
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            for _ in range(400):
                runtime = state.get_runtime_state()
                assert runtime.max_workers >= 0
                snapshot = state.status_snapshot(limit=1)
                assert "runtime" in snapshot
                state.is_watch_paused()
        except BaseException as exc:  # pragma: no cover - regression guard
            errors.append(exc)

    def writer() -> None:
        try:
            for i in range(400):
                state.update_runtime_state(
                    watch_running=(i % 2 == 0),
                    queued_paths=i,
                    active_workers=i % 3,
                    max_workers=2,
                )
                state.set_watch_paused(i % 2 == 1)
        except BaseException as exc:  # pragma: no cover - regression guard
            errors.append(exc)

    try:
        threads = [
            threading.Thread(target=reader),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
            threading.Thread(target=writer),
            threading.Thread(target=writer),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
    finally:
        state.close()
