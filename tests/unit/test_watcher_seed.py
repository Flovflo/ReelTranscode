from __future__ import annotations

import queue

from reeltranscode.config import AppConfig
from reeltranscode.watcher import LibraryWatcher


def test_seed_existing_files_recursive(tmp_path):
    root = tmp_path / "watch"
    nested = root / "nested"
    nested.mkdir(parents=True)
    media_file = nested / "movie.mkv"
    media_file.write_bytes(b"data")
    (nested / "notes.txt").write_text("ignore", encoding="utf-8")

    cfg = AppConfig.from_dict(
        {
            "watch": {
                "folders": [str(root)],
                "recursive": True,
                "allowed_extensions": [".mkv", ".mp4"],
            }
        }
    )
    watcher = LibraryWatcher(cfg)
    work_queue: queue.Queue = queue.Queue()

    queued = watcher._seed_existing_files(root, work_queue)  # noqa: SLF001 - tested behavior

    assert queued == 1
    item = work_queue.get_nowait()
    assert item.path == media_file


def test_seed_existing_files_non_recursive(tmp_path):
    root = tmp_path / "watch"
    nested = root / "nested"
    nested.mkdir(parents=True)
    top_media = root / "top.mkv"
    nested_media = nested / "nested.mkv"
    top_media.write_bytes(b"top")
    nested_media.write_bytes(b"nested")

    cfg = AppConfig.from_dict(
        {
            "watch": {
                "folders": [str(root)],
                "recursive": False,
                "allowed_extensions": [".mkv"],
            }
        }
    )
    watcher = LibraryWatcher(cfg)
    work_queue: queue.Queue = queue.Queue()

    queued = watcher._seed_existing_files(root, work_queue)  # noqa: SLF001 - tested behavior

    assert queued == 1
    item = work_queue.get_nowait()
    assert item.path == top_media
