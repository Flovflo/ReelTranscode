from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from reeltranscode.config import AppConfig
from reeltranscode.models import JobStatus
from reeltranscode.scanner import iter_media_files
from reeltranscode.state_store import StateStore
from reeltranscode.watcher import LibraryWatcher, QueuedPath, _MediaEventHandler


def _make_watcher(cfg: AppConfig, tmp_path) -> tuple[LibraryWatcher, StateStore]:  # noqa: ANN001
    state = StateStore(tmp_path / "state.db")
    return LibraryWatcher(cfg, state), state


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
    watcher, state = _make_watcher(cfg, tmp_path)
    work_queue: queue.Queue = queue.Queue()

    try:
        queued = watcher._seed_existing_files(root, work_queue)  # noqa: SLF001 - tested behavior

        assert queued == 1
        item = work_queue.get_nowait()
        assert item.path == media_file
        assert item.seeded is True
    finally:
        state.close()


def test_seed_existing_files_includes_avi_by_default(tmp_path):
    root = tmp_path / "watch"
    root.mkdir(parents=True)
    media_file = root / "episode.avi"
    media_file.write_bytes(b"data")

    cfg = AppConfig.from_dict({"watch": {"folders": [str(root)]}})
    watcher, state = _make_watcher(cfg, tmp_path)
    work_queue: queue.Queue = queue.Queue()

    try:
        queued = watcher._seed_existing_files(root, work_queue)  # noqa: SLF001 - tested behavior

        assert queued == 1
        assert work_queue.get_nowait().path == media_file
    finally:
        state.close()


def test_find_seed_candidates_streams_before_process_exits(tmp_path):
    root = tmp_path / "watch"
    root.mkdir(parents=True)
    first = root / "first.mkv"
    second = root / "second.mkv"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    cfg = AppConfig.from_dict({"watch": {"folders": [str(root)]}})
    watcher, state = _make_watcher(cfg, tmp_path)
    script = (
        "import sys, time\n"
        "sys.stdout.buffer.write(sys.argv[1].encode() + b'\\0')\n"
        "sys.stdout.buffer.flush()\n"
        "time.sleep(2)\n"
        "sys.stdout.buffer.write(sys.argv[2].encode() + b'\\0')\n"
        "sys.stdout.buffer.flush()\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(first), str(second)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    iterator = watcher._stream_find_candidates(process, root)  # noqa: SLF001 - tested behavior

    try:
        started = time.monotonic()
        assert next(iterator) == first
        assert time.monotonic() - started < 1
    finally:
        iterator.close()
        state.close()

    assert process.poll() is not None


def test_find_seed_candidates_terminates_idle_subtree_scan(tmp_path):
    root = tmp_path / "watch"
    root.mkdir(parents=True)

    cfg = AppConfig.from_dict({"watch": {"folders": [str(root)]}})
    watcher, state = _make_watcher(cfg, tmp_path)
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    try:
        started = time.monotonic()
        with patch("reeltranscode.watcher.FIND_IDLE_TIMEOUT_SECONDS", 0.1):
            assert list(watcher._stream_find_candidates(process, root)) == []  # noqa: SLF001 - tested behavior
        assert time.monotonic() - started < 2
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        state.close()

    assert process.poll() is not None


def test_seed_existing_files_skips_unchanged_failed_records(tmp_path):
    root = tmp_path / "watch"
    root.mkdir(parents=True)
    media_file = root / "episode.mkv"
    media_file.write_bytes(b"data")
    stat = media_file.stat()

    cfg = AppConfig.from_dict({"watch": {"folders": [str(root)]}})
    watcher, state = _make_watcher(cfg, tmp_path)
    state.upsert_file_state(
        media_file,
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        "stream-fp",
        "metadata-fp",
        JobStatus.FAILED,
        "job-id",
        cfg.processing_policy_fingerprint(),
    )
    work_queue: queue.Queue = queue.Queue()

    try:
        queued = watcher._seed_existing_files(root, work_queue)  # noqa: SLF001 - tested behavior

        assert queued == 0
        assert work_queue.qsize() == 0
    finally:
        state.close()


def test_seed_existing_files_requeues_stale_failed_policy_records(tmp_path):
    root = tmp_path / "watch"
    root.mkdir(parents=True)
    media_file = root / "episode.mkv"
    media_file.write_bytes(b"data")
    stat = media_file.stat()

    cfg = AppConfig.from_dict({"watch": {"folders": [str(root)]}})
    watcher, state = _make_watcher(cfg, tmp_path)
    state.upsert_file_state(
        media_file,
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        "stream-fp",
        "metadata-fp",
        JobStatus.FAILED,
        "job-id",
        "policy:old",
    )
    work_queue: queue.Queue = queue.Queue()

    try:
        queued = watcher._seed_existing_files(root, work_queue)  # noqa: SLF001 - tested behavior

        assert queued == 1
        assert work_queue.get_nowait().path == media_file
    finally:
        state.close()


def test_seed_existing_files_keeps_unprobeable_analysis_failures_blocked(tmp_path):
    root = tmp_path / "watch"
    root.mkdir(parents=True)
    media_file = root / "corrupt.mkv"
    media_file.write_bytes(b"data")
    stat = media_file.stat()

    cfg = AppConfig.from_dict({"watch": {"folders": [str(root)]}})
    watcher, state = _make_watcher(cfg, tmp_path)
    state.upsert_file_state(
        media_file,
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        "analysis-failed:ProbeError",
        "analysis-failed:ProbeError",
        JobStatus.FAILED,
        "job-id",
        "policy:old",
    )
    work_queue: queue.Queue = queue.Queue()

    try:
        queued = watcher._seed_existing_files(root, work_queue)  # noqa: SLF001 - tested behavior

        assert queued == 0
        assert work_queue.qsize() == 0
    finally:
        state.close()


def test_priority_seed_allows_managed_output_root_and_ignores_mp4(tmp_path):
    watch_root = tmp_path / "watch"
    output_root = tmp_path / "optimized"
    output_root.mkdir(parents=True)
    mkv = output_root / "episode.mkv"
    mp4 = output_root / "episode.mp4"
    mkv.write_bytes(b"source")
    mp4.write_bytes(b"already optimized")

    cfg = AppConfig.from_dict(
        {
            "watch": {
                "folders": [str(watch_root)],
                "priority_folders": [str(output_root)],
                "priority_extensions": [".mkv"],
            },
            "output": {
                "output_root": str(output_root),
                "output_root_overrides": {str(output_root): str(output_root)},
                "delete_original_after_success_roots": [str(output_root)],
            },
        }
    )
    watcher, state = _make_watcher(cfg, tmp_path)
    work_queue: queue.Queue = queue.Queue()

    try:
        assert cfg.validate() == []
        queued = watcher._seed_existing_files(output_root, work_queue, priority=True)  # noqa: SLF001

        assert queued == 1
        item = work_queue.get_nowait()
        assert item.path == mkv
        assert item.priority is True
    finally:
        state.close()


def test_priority_queue_processes_priority_items_before_normal_items(tmp_path):
    normal_root = tmp_path / "watch"
    priority_root = tmp_path / "optimized"
    normal_root.mkdir()
    priority_root.mkdir()
    normal = normal_root / "normal.mkv"
    priority = priority_root / "priority.mkv"
    normal.write_bytes(b"normal")
    priority.write_bytes(b"priority")

    cfg = AppConfig.from_dict({"watch": {"folders": [str(normal_root)], "priority_folders": [str(priority_root)]}})
    watcher, state = _make_watcher(cfg, tmp_path)
    work_queue: queue.PriorityQueue = queue.PriorityQueue()

    try:
        assert watcher._enqueue_once(work_queue, QueuedPath(path=normal, source_root=normal_root)) is True  # noqa: SLF001
        assert watcher._enqueue_once(  # noqa: SLF001
            work_queue,
            QueuedPath(path=priority, source_root=priority_root, priority=True),
        ) is True

        queued = work_queue.get_nowait()
        assert queued.item.path == priority
    finally:
        state.close()


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
    watcher, state = _make_watcher(cfg, tmp_path)
    work_queue: queue.Queue = queue.Queue()

    try:
        queued = watcher._seed_existing_files(root, work_queue)  # noqa: SLF001 - tested behavior

        assert queued == 1
        item = work_queue.get_nowait()
        assert item.path == top_media
        assert item.seeded is True
    finally:
        state.close()


def test_worker_skips_stability_wait_for_seeded_items(tmp_path):
    root = tmp_path / "watch"
    root.mkdir(parents=True)
    media = root / "movie.mkv"
    media.write_bytes(b"data")
    old_timestamp = time.time() - 120
    os.utime(media, (old_timestamp, old_timestamp))

    cfg = AppConfig.from_dict({"watch": {"folders": [str(root)]}})
    watcher, state = _make_watcher(cfg, tmp_path)
    work_queue: queue.Queue[QueuedPath] = queue.Queue()
    work_queue.put(QueuedPath(path=media, source_root=root, seeded=True))
    processed: list[tuple[str, str]] = []

    try:
        def process_fn(path, source_root):  # noqa: ANN001
            processed.append((str(path), str(source_root)))
            watcher.stop()

        with patch("reeltranscode.watcher.wait_for_stable_file", side_effect=AssertionError("must not be called")):
            worker = threading.Thread(target=watcher._worker, args=(work_queue, process_fn), daemon=True)  # noqa: SLF001
            worker.start()
            worker.join(timeout=2)

        assert processed == [(str(media), str(root))]
    finally:
        state.close()


def test_worker_waits_for_recent_seeded_items(tmp_path):
    root = tmp_path / "watch"
    root.mkdir(parents=True)
    media = root / "movie.mkv"
    media.write_bytes(b"data")

    cfg = AppConfig.from_dict({"watch": {"folders": [str(root)], "stable_wait_seconds": 60}})
    watcher, state = _make_watcher(cfg, tmp_path)
    work_queue: queue.Queue[QueuedPath] = queue.Queue()
    work_queue.put(QueuedPath(path=media, source_root=root, seeded=True))
    processed: list[tuple[str, str]] = []

    try:
        def process_fn(path, source_root):  # noqa: ANN001
            processed.append((str(path), str(source_root)))
            watcher.stop()

        with patch("reeltranscode.watcher.wait_for_stable_file", return_value=True) as wait_mock:
            worker = threading.Thread(target=watcher._worker, args=(work_queue, process_fn), daemon=True)  # noqa: SLF001
            worker.start()
            worker.join(timeout=2)

        wait_mock.assert_called_once()
        assert processed == [(str(media), str(root))]
    finally:
        state.close()


def test_worker_waits_for_non_seeded_items(tmp_path):
    root = tmp_path / "watch"
    root.mkdir(parents=True)
    media = root / "movie.mkv"
    media.write_bytes(b"data")

    cfg = AppConfig.from_dict({"watch": {"folders": [str(root)]}})
    watcher, state = _make_watcher(cfg, tmp_path)
    work_queue: queue.Queue[QueuedPath] = queue.Queue()
    work_queue.put(QueuedPath(path=media, source_root=root, seeded=False))
    processed: list[tuple[str, str]] = []

    try:
        def process_fn(path, source_root):  # noqa: ANN001
            processed.append((str(path), str(source_root)))
            watcher.stop()

        with patch("reeltranscode.watcher.wait_for_stable_file", return_value=True) as wait_mock:
            worker = threading.Thread(target=watcher._worker, args=(work_queue, process_fn), daemon=True)  # noqa: SLF001
            worker.start()
            worker.join(timeout=2)

        wait_mock.assert_called_once()
        assert processed == [(str(media), str(root))]
    finally:
        state.close()


def test_run_forever_registers_observers_before_background_seed(tmp_path):
    root = tmp_path / "watch"
    root.mkdir(parents=True)
    cfg = AppConfig.from_dict(
        {
            "watch": {"folders": [str(root)], "use_filesystem_events": True},
            "concurrency": {"max_workers": 1},
        }
    )
    watcher, state = _make_watcher(cfg, tmp_path)
    events: list[str] = []

    class _FakeObserver:
        def schedule(self, handler, path, recursive):  # noqa: ANN001
            events.append(f"schedule:{path}:{recursive}")

        def start(self):
            events.append("observer_start")

        def stop(self):
            events.append("observer_stop")

        def join(self, timeout=None):  # noqa: ANN001
            events.append("observer_join")

    fake_thread = Mock()

    def _start_worker():
        events.append("worker_start")
        watcher.stop()

    fake_thread.start.side_effect = _start_worker

    try:
        with (
                patch.object(
                    watcher,
                    "_start_seed_scan",
                    side_effect=lambda root_path, work_queue, *, reason, priority=False: events.append(
                        f"seed:{root_path}:{reason}:{priority}"
                    )
                    or True,
                ),
            patch("reeltranscode.watcher.Observer", return_value=_FakeObserver()),
            patch("reeltranscode.watcher.threading.Thread", return_value=fake_thread),
        ):
            watcher.run_forever(lambda *_args, **_kwargs: None)

        assert events[:4] == ["worker_start", f"schedule:{root}:True", "observer_start", f"seed:{root}:initial:False"]
    finally:
        state.close()


def test_start_seed_scan_does_not_start_duplicate_root_scans(tmp_path):
    root = tmp_path / "watch"
    root.mkdir(parents=True)
    cfg = AppConfig.from_dict({"watch": {"folders": [str(root)]}})
    watcher, state = _make_watcher(cfg, tmp_path)
    work_queue: queue.Queue[QueuedPath] = queue.Queue()
    started: list[str] = []

    class _FakeThread:
        def __init__(self, *, target, args, daemon, name):  # noqa: ANN001
            started.append(name)
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            return None

    try:
        with patch("reeltranscode.watcher.threading.Thread", side_effect=_FakeThread):
            assert watcher._start_seed_scan(root, work_queue, reason="initial") is True  # noqa: SLF001
            assert watcher._start_seed_scan(root, work_queue, reason="periodic") is False  # noqa: SLF001

        assert started == ["reeltranscode-seed-watch"]
    finally:
        state.close()


def test_seed_existing_files_skips_managed_output_and_temp_paths(tmp_path):
    root = tmp_path / "watch"
    optimized = root / "optimized"
    temp_dir = root / "tmp"
    optimized.mkdir(parents=True)
    temp_dir.mkdir(parents=True)
    source_media = root / "movie.mkv"
    optimized_media = optimized / "movie.mp4"
    temp_media = temp_dir / "movie.tmp.mp4"
    source_media.write_bytes(b"source")
    optimized_media.write_bytes(b"optimized")
    temp_media.write_bytes(b"temp")

    cfg = AppConfig.from_dict(
        {
            "watch": {"folders": [str(root)]},
            "output": {"output_root": str(optimized)},
            "paths": {"temp_dir": str(temp_dir)},
        }
    )
    watcher, state = _make_watcher(cfg, tmp_path)
    work_queue: queue.Queue = queue.Queue()

    try:
        queued = watcher._seed_existing_files(root, work_queue)  # noqa: SLF001 - tested behavior

        assert queued == 1
        item = work_queue.get_nowait()
        assert item.path == source_media
    finally:
        state.close()


def test_seed_existing_files_skips_hidden_transient_media(tmp_path):
    root = tmp_path / "watch"
    root.mkdir(parents=True)
    hidden_temp = root / ".movie.tmp.mp4"
    real_media = root / "movie.mkv"
    hidden_temp.write_bytes(b"temp")
    real_media.write_bytes(b"real")

    cfg = AppConfig.from_dict(
        {
            "watch": {
                "folders": [str(root)],
                "allowed_extensions": [".mkv", ".mp4"],
            }
        }
    )
    watcher, state = _make_watcher(cfg, tmp_path)
    work_queue: queue.Queue = queue.Queue()

    try:
        queued = watcher._seed_existing_files(root, work_queue)  # noqa: SLF001 - tested behavior

        assert queued == 1
        item = work_queue.get_nowait()
        assert item.path == real_media
    finally:
        state.close()


def test_seed_existing_files_skips_generated_thumbnail_directories(tmp_path):
    root = tmp_path / "watch"
    thumb_dir = root / "Show" / "S01" / ".@__thumb"
    thumb_dir.mkdir(parents=True)
    thumbnail_media = thumb_dir / "s100S01E01.avi"
    source_media = root / "Show" / "S01" / "S01E01.avi"
    thumbnail_media.write_bytes(b"thumb")
    source_media.write_bytes(b"episode")

    cfg = AppConfig.from_dict({"watch": {"folders": [str(root)]}})
    watcher, state = _make_watcher(cfg, tmp_path)
    work_queue: queue.Queue = queue.Queue()

    try:
        queued = watcher._seed_existing_files(root, work_queue)  # noqa: SLF001 - tested behavior

        assert queued == 1
        item = work_queue.get_nowait()
        assert item.path == source_media
    finally:
        state.close()


def test_scanner_skips_managed_output_and_temp_paths(tmp_path):
    root = tmp_path / "watch"
    optimized = root / "optimized"
    temp_dir = root / "tmp"
    optimized.mkdir(parents=True)
    temp_dir.mkdir(parents=True)
    source_media = root / "movie.mkv"
    optimized_media = optimized / "movie.mp4"
    temp_media = temp_dir / "movie.tmp.mp4"
    source_media.write_bytes(b"source")
    optimized_media.write_bytes(b"optimized")
    temp_media.write_bytes(b"temp")

    cfg = AppConfig.from_dict(
        {
            "watch": {"folders": [str(root)]},
            "output": {"output_root": str(optimized)},
            "paths": {"temp_dir": str(temp_dir)},
        }
    )

    files = iter_media_files(cfg)

    assert files == [(source_media, root)]


def test_scanner_skips_generated_thumbnail_directories(tmp_path):
    root = tmp_path / "watch"
    thumb_dir = root / "Show" / "S01" / ".@__thumb"
    thumb_dir.mkdir(parents=True)
    thumbnail_media = thumb_dir / "s100S01E01.avi"
    source_media = root / "Show" / "S01" / "S01E01.avi"
    thumbnail_media.write_bytes(b"thumb")
    source_media.write_bytes(b"episode")

    cfg = AppConfig.from_dict({"watch": {"folders": [str(root)]}})

    files = iter_media_files(cfg)

    assert files == [(source_media, root)]


def test_seed_existing_files_skips_dynamic_source_local_temp_workspace(tmp_path):
    root = tmp_path / "watch"
    source_workspace = root / ".reeltranscode-tmp"
    source_workspace.mkdir(parents=True)
    source_media = root / "movie.mkv"
    temp_media = source_workspace / "movie.tmp.mp4"
    source_media.write_bytes(b"source")
    temp_media.write_bytes(b"temp")

    cfg = AppConfig.from_dict(
        {
            "watch": {"folders": [str(root)]},
            "output": {"output_root": str(tmp_path / "optimized")},
            "paths": {"temp_dir": str(tmp_path / "tmp")},
        }
    )
    watcher, state = _make_watcher(cfg, tmp_path)
    work_queue: queue.Queue = queue.Queue()

    try:
        queued = watcher._seed_existing_files(root, work_queue)  # noqa: SLF001 - tested behavior

        assert queued == 1
        item = work_queue.get_nowait()
        assert item.path == source_media
    finally:
        state.close()


def test_seed_existing_files_skips_hidden_dot_temp_media_files(tmp_path):
    root = tmp_path / "watch"
    root.mkdir(parents=True)
    source_media = root / "movie.mkv"
    hidden_temp = root / ".movie.123abc.tmp.mp4"
    source_media.write_bytes(b"source")
    hidden_temp.write_bytes(b"temp")

    cfg = AppConfig.from_dict(
        {
            "watch": {"folders": [str(root)], "allowed_extensions": [".mkv", ".mp4"]},
            "output": {"output_root": str(tmp_path / "optimized")},
            "paths": {"temp_dir": str(tmp_path / "tmp")},
        }
    )
    watcher, state = _make_watcher(cfg, tmp_path)
    work_queue: queue.Queue = queue.Queue()

    try:
        queued = watcher._seed_existing_files(root, work_queue)  # noqa: SLF001 - tested behavior

        assert queued == 1
        item = work_queue.get_nowait()
        assert item.path == source_media
    finally:
        state.close()


def test_event_handler_skips_hidden_dot_temp_media_files(tmp_path):
    root = tmp_path / "watch"
    root.mkdir(parents=True)
    hidden_temp = root / ".movie.123abc.tmp.mp4"
    hidden_temp.write_bytes(b"temp")

    cfg = AppConfig.from_dict(
        {
            "watch": {"folders": [str(root)], "allowed_extensions": [".mp4"]},
            "output": {"output_root": str(tmp_path / "optimized")},
            "paths": {"temp_dir": str(tmp_path / "tmp")},
        }
    )
    watcher, state = _make_watcher(cfg, tmp_path)
    work_queue: queue.Queue[QueuedPath] = queue.Queue()
    handler = _MediaEventHandler(cfg, root, work_queue, watcher)

    try:
        handler.on_created(SimpleNamespace(src_path=str(hidden_temp), is_directory=False))

        assert work_queue.qsize() == 0
    finally:
        state.close()


def test_event_handler_skips_generated_thumbnail_directories(tmp_path):
    root = tmp_path / "watch"
    thumb_dir = root / "Show" / "S01" / ".@__thumb"
    thumb_dir.mkdir(parents=True)
    thumbnail_media = thumb_dir / "s100S01E01.avi"
    thumbnail_media.write_bytes(b"thumb")

    cfg = AppConfig.from_dict({"watch": {"folders": [str(root)]}})
    watcher, state = _make_watcher(cfg, tmp_path)
    work_queue: queue.Queue[QueuedPath] = queue.Queue()
    handler = _MediaEventHandler(cfg, root, work_queue, watcher)

    try:
        handler.on_created(SimpleNamespace(src_path=str(thumbnail_media), is_directory=False))

        assert work_queue.qsize() == 0
    finally:
        state.close()


def test_scanner_skips_dynamic_source_local_temp_workspace(tmp_path):
    root = tmp_path / "watch"
    source_workspace = root / ".reeltranscode-tmp"
    source_workspace.mkdir(parents=True)
    source_media = root / "movie.mkv"
    temp_media = source_workspace / "movie.tmp.mp4"
    source_media.write_bytes(b"source")
    temp_media.write_bytes(b"temp")

    cfg = AppConfig.from_dict(
        {
            "watch": {"folders": [str(root)]},
            "output": {"output_root": str(tmp_path / "optimized")},
            "paths": {"temp_dir": str(tmp_path / "tmp")},
        }
    )

    files = iter_media_files(cfg)

    assert files == [(source_media, root)]


def test_watcher_dedupes_seeded_file_against_immediate_filesystem_event(tmp_path):
    root = tmp_path / "watch"
    root.mkdir(parents=True)
    media = root / "Show" / "s1" / "episode.mkv"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"data")

    cfg = AppConfig.from_dict(
        {
            "watch": {
                "folders": [str(root)],
                "recursive": True,
                "allowed_extensions": [".mkv"],
            }
        }
    )
    watcher, state = _make_watcher(cfg, tmp_path)
    work_queue: queue.Queue[QueuedPath] = queue.Queue()
    handler = _MediaEventHandler(cfg, root, work_queue, watcher)

    try:
        queued = watcher._seed_existing_files(root, work_queue)  # noqa: SLF001 - tested behavior
        handler.on_modified(SimpleNamespace(src_path=str(media), is_directory=False))

        assert queued == 1
        assert work_queue.qsize() == 1
    finally:
        state.close()
