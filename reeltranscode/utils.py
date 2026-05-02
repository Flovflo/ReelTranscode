from __future__ import annotations

from dataclasses import dataclass
import errno
import logging
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reeltranscode.config import AppConfig

RUNTIME_TEMP_DIRNAME = ".reeltranscode-tmp"
TRANSIENT_MEDIA_MARKERS = (".tmp.", ".part.", ".partial.")
GENERATED_METADATA_PATH_PARTS = frozenset(
    {
        ".@__thumb",
        ".appledouble",
        ".temporaryitems",
        ".trashes",
        "@eadir",
        "@recycle",
        "#recycle",
    }
)


@dataclass(slots=True)
class PublishResult:
    used_cross_device_fallback: bool
    staged_path: Path | None = None


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


def is_runtime_temp_path(path: Path) -> bool:
    try:
        candidate = path.expanduser().resolve()
    except OSError:
        candidate = path.expanduser()
    return RUNTIME_TEMP_DIRNAME in candidate.parts


def is_transient_media_path(path: Path) -> bool:
    name = path.name.lower()
    if not name:
        return False
    if name.startswith("."):
        return True
    return any(marker in name for marker in TRANSIENT_MEDIA_MARKERS)


def is_generated_metadata_path(path: Path) -> bool:
    try:
        candidate = path.expanduser().resolve()
    except OSError:
        candidate = path.expanduser()
    return any(part.lower() in GENERATED_METADATA_PATH_PARTS for part in candidate.parts)


def is_media_file(path: Path, allowed_extensions: set[str]) -> bool:
    return (
        path.is_file()
        and path.suffix.lower() in allowed_extensions
        and not is_runtime_temp_path(path)
        and not is_transient_media_path(path)
        and not is_generated_metadata_path(path)
    )


def path_contains(parent: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def paths_overlap(left: Path, right: Path) -> bool:
    left_resolved = left.resolve()
    right_resolved = right.resolve()
    return path_contains(left_resolved, right_resolved) or path_contains(right_resolved, left_resolved)


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


def atomic_replace(src: Path, dst: Path) -> PublishResult:
    ensure_parent(dst)
    try:
        os.replace(src, dst)
        _fsync_parent_dir(dst.parent)
        return PublishResult(used_cross_device_fallback=False, staged_path=dst)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        # Cross-device publish is staged onto the destination volume first so the final
        # switch remains an atomic replace local to the target filesystem.
        staging_path = dst.parent / f".{dst.name}.{uuid.uuid4().hex[:10]}.publish"
        try:
            shutil.copy2(str(src), str(staging_path))
            if staging_path.stat().st_size != src.stat().st_size:
                raise OSError(errno.EIO, f"Cross-device staging copy size mismatch for {dst}")
            _fsync_file(staging_path)
            os.replace(staging_path, dst)
            _fsync_parent_dir(dst.parent)
        except Exception:
            if staging_path.exists():
                try:
                    staging_path.unlink()
                except OSError:
                    pass
            raise
        finally:
            if staging_path.exists():
                try:
                    staging_path.unlink()
                except OSError:
                    pass

        if src.exists():
            src.unlink()
        return PublishResult(used_cross_device_fallback=True, staged_path=dst)


def _fsync_file(path: Path) -> None:
    try:
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
    except OSError:
        return


def _fsync_parent_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return

    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
