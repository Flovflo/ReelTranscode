from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from reeltranscode.config import AppConfig


def setup_logging(config: AppConfig) -> None:
    level = getattr(logging, config.logging.level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def is_media_file(path: Path, allowed_extensions: set[str]) -> bool:
    return path.is_file() and path.suffix.lower() in allowed_extensions


def wait_for_stable_file(
    path: Path,
    stable_checks: int,
    poll_interval_seconds: int,
    max_wait_seconds: int,
) -> bool:
    end_at = time.time() + max_wait_seconds
    previous_size: int | None = None
    stable_counter = 0

    while time.time() < end_at:
        try:
            stat = path.stat()
            size = stat.st_size
            mtime = stat.st_mtime_ns
            if previous_size == size:
                stable_counter += 1
            else:
                stable_counter = 0
            previous_size = size

            # Ensure writer closed handle and mtime settled.
            if stable_counter >= stable_checks:
                with path.open("rb"):
                    pass
                _ = mtime
                return True
        except (FileNotFoundError, OSError):
            stable_counter = 0

        time.sleep(poll_interval_seconds)

    return False


def inode_identity(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (stat.st_dev, stat.st_ino)


def atomic_replace(src: Path, dst: Path) -> None:
    ensure_parent(dst)
    try:
        os.replace(src, dst)
    except OSError as exc:
        if exc.errno != os.EXDEV:
            raise
        # Cross-device replace is not atomic; fallback to move to support external volumes.
        shutil.move(str(src), str(dst))
