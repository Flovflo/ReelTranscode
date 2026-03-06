from __future__ import annotations

import queue
import threading
from unittest.mock import patch

from reeltranscode.config import AppConfig
from reeltranscode.watcher import LibraryWatcher, QueuedPath


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
    assert item.seeded is True


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
    assert item.seeded is True


def test_worker_skips_stability_wait_for_seeded_items(tmp_path):
    root = tmp_path / "watch"
    root.mkdir(parents=True)
    media = root / "movie.mkv"
    media.write_bytes(b"data")

    cfg = AppConfig.from_dict({"watch": {"folders": [str(root)]}})
    watcher = LibraryWatcher(cfg)
    work_queue: queue.Queue[QueuedPath] = queue.Queue()
    work_queue.put(QueuedPath(path=media, source_root=root, seeded=True))
    processed: list[tuple[str, str]] = []

    def process_fn(path, source_root):  # noqa: ANN001
        processed.append((str(path), str(source_root)))
        watcher.stop()

    with patch("reeltranscode.watcher.wait_for_stable_file", side_effect=AssertionError("must not be called")):
        worker = threading.Thread(target=watcher._worker, args=(work_queue, process_fn), daemon=True)  # noqa: SLF001
        worker.start()
        worker.join(timeout=2)

    assert processed == [(str(media), str(root))]


def test_worker_waits_for_non_seeded_items(tmp_path):
    root = tmp_path / "watch"
    root.mkdir(parents=True)
    media = root / "movie.mkv"
    media.write_bytes(b"data")

    cfg = AppConfig.from_dict({"watch": {"folders": [str(root)]}})
    watcher = LibraryWatcher(cfg)
    work_queue: queue.Queue[QueuedPath] = queue.Queue()
    work_queue.put(QueuedPath(path=media, source_root=root, seeded=False))
    processed: list[tuple[str, str]] = []

    def process_fn(path, source_root):  # noqa: ANN001
        processed.append((str(path), str(source_root)))
        watcher.stop()

    with patch("reeltranscode.watcher.wait_for_stable_file", return_value=True) as wait_mock:
        worker = threading.Thread(target=watcher._worker, args=(work_queue, process_fn), daemon=True)  # noqa: SLF001
        worker.start()
        worker.join(timeout=2)

    wait_mock.assert_called_once()
    assert processed == [(str(media), str(root))]
