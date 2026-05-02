from __future__ import annotations

import logging
import os
import queue
import select
import subprocess
import threading
import time
from dataclasses import dataclass
from dataclasses import field
from itertools import count
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers.polling import PollingObserver as Observer

from reeltranscode.config import AppConfig
from reeltranscode.state_store import StateStore
from reeltranscode.utils import GENERATED_METADATA_PATH_PARTS, RUNTIME_TEMP_DIRNAME, is_media_file, wait_for_stable_file

LOGGER = logging.getLogger(__name__)
FIND_IDLE_TIMEOUT_SECONDS = 60.0
RUNTIME_HEARTBEAT_INTERVAL_SECONDS = 30.0


@dataclass(slots=True)
class QueuedPath:
    path: Path
    source_root: Path
    seeded: bool = False
    priority: bool = False


@dataclass(order=True, slots=True)
class _PrioritizedQueuedPath:
    priority_rank: int
    sequence: int
    item: QueuedPath = field(compare=False)


class _MediaEventHandler(FileSystemEventHandler):
    def __init__(
        self,
        cfg: AppConfig,
        root: Path,
        work_queue: queue.Queue[QueuedPath],
        watcher: "LibraryWatcher",
        *,
        allowed_extensions: set[str] | None = None,
        allow_managed_paths: bool = False,
        priority: bool = False,
    ):
        self.cfg = cfg
        self.root = root
        self.work_queue = work_queue
        self.watcher = watcher
        self.allowed_extensions = allowed_extensions or cfg.watch.allowed_extensions
        self.allow_managed_paths = allow_managed_paths
        self.priority = priority
        self._recent: dict[str, float] = {}

    def on_created(self, event: FileSystemEvent) -> None:
        self._enqueue(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        self._enqueue(event, getattr(event, "dest_path", None))

    def on_modified(self, event: FileSystemEvent) -> None:
        self._enqueue(event)

    def _enqueue(self, event: FileSystemEvent, explicit_path: str | None = None) -> None:
        if event.is_directory:
            return
        raw = explicit_path or event.src_path
        path = Path(raw)
        if self.cfg.is_excluded_from_watch(path, allow_managed_paths=self.allow_managed_paths):
            return
        if not is_media_file(path, self.allowed_extensions):
            return

        key = str(path.resolve())
        now = time.time()
        previous = self._recent.get(key)
        if previous and (now - previous) < 5:
            return
        self._recent[key] = now
        self.watcher._enqueue_once(
            self.work_queue,
            QueuedPath(path=path, source_root=self.root, seeded=False, priority=self.priority),
        )


class LibraryWatcher:
    def __init__(self, cfg: AppConfig, state_store: StateStore):
        self.cfg = cfg
        self.state_store = state_store
        self._stop_event = threading.Event()
        self._queued_paths: set[Path] = set()
        self._inflight_paths: set[Path] = set()
        self._queued_paths_lock = threading.Lock()
        self._active_seed_roots: set[Path] = set()
        self._active_seed_roots_lock = threading.Lock()
        self._seed_scan_lock = threading.Lock()
        self._queue_sequence = count()
        self._processing_policy_fp = cfg.processing_policy_fingerprint()

    def run_forever(self, process_fn) -> None:
        if not self.cfg.watch.folders:
            raise RuntimeError("No watch folders configured")

        work_queue: queue.Queue[QueuedPath | _PrioritizedQueuedPath] = queue.PriorityQueue()
        observers: list[Observer] = []
        watch_roots: list[Path] = []
        priority_roots: list[Path] = []
        self.state_store.update_runtime_state(
            watch_running=True,
            queued_paths=0,
            active_workers=0,
            max_workers=self.cfg.concurrency.max_workers,
        )

        for root in self.cfg.watch.priority_folders:
            if self.cfg.is_excluded_from_watch(root, allow_managed_paths=True):
                LOGGER.warning("Skipping priority watch folder because it is transient/generated: %s", root)
                continue
            priority_roots.append(root)

        for root in self.cfg.watch.folders:
            if self.cfg.is_excluded_from_watch(root):
                LOGGER.warning("Skipping watch folder because it overlaps a managed path: %s", root)
                continue
            watch_roots.append(root)

        workers = [
            threading.Thread(target=self._worker, args=(work_queue, process_fn), daemon=True)
            for _ in range(max(1, self.cfg.concurrency.max_workers))
        ]
        for worker in workers:
            worker.start()

        if self.cfg.watch.use_filesystem_events:
            for root in priority_roots:
                handler = _MediaEventHandler(
                    self.cfg,
                    root,
                    work_queue,
                    self,
                    allowed_extensions=self.cfg.watch.priority_extensions,
                    allow_managed_paths=True,
                    priority=True,
                )
                observer = Observer(timeout=max(1, self.cfg.watch.poll_interval_seconds))
                observer.schedule(handler, str(root), recursive=self.cfg.watch.recursive)
                observer.start()
                observers.append(observer)
                LOGGER.info("Watching priority folder with polling observer: %s", root)
            for root in watch_roots:
                handler = _MediaEventHandler(self.cfg, root, work_queue, self)
                observer = Observer(timeout=max(1, self.cfg.watch.poll_interval_seconds))
                observer.schedule(handler, str(root), recursive=self.cfg.watch.recursive)
                observer.start()
                observers.append(observer)
                LOGGER.info("Watching folder with polling observer: %s", root)
        else:
            LOGGER.info("Filesystem events disabled; using seed scans every %d second(s)", self.cfg.watch.rescan_interval_seconds)

        for root in watch_roots:
            self._start_seed_scan(root, work_queue, reason="initial", priority=False)
        for root in priority_roots:
            self._start_seed_scan(root, work_queue, reason="initial priority", priority=True)

        last_rescan = time.monotonic()
        last_runtime_heartbeat = time.monotonic()
        try:
            while not self._stop_event.is_set():
                now = time.monotonic()
                if (now - last_runtime_heartbeat) >= RUNTIME_HEARTBEAT_INTERVAL_SECONDS:
                    last_runtime_heartbeat = now
                    self._publish_runtime_state()
                interval = self.cfg.watch.rescan_interval_seconds
                if interval > 0 and (now - last_rescan) >= interval:
                    last_rescan = now
                    for root in watch_roots:
                        self._start_seed_scan(root, work_queue, reason="periodic", priority=False)
                    for root in priority_roots:
                        self._start_seed_scan(root, work_queue, reason="periodic priority", priority=True)
                time.sleep(1)
        except KeyboardInterrupt:
            LOGGER.info("Stopping watcher")
        finally:
            self._stop_event.set()
            for observer in observers:
                observer.stop()
            for observer in observers:
                observer.join(timeout=10)
            for worker in workers:
                worker.join(timeout=10)
            self.state_store.update_runtime_state(watch_running=False, queued_paths=0, active_workers=0)

    def stop(self) -> None:
        self._stop_event.set()

    def _worker(self, work_queue: queue.Queue[QueuedPath], process_fn) -> None:
        while not self._stop_event.is_set():
            if self.state_store.is_watch_paused():
                time.sleep(1)
                continue
            try:
                queued = work_queue.get(timeout=1)
            except queue.Empty:
                continue
            item = queued.item if isinstance(queued, _PrioritizedQueuedPath) else queued

            try:
                self._mark_started(item.path)
                if self._should_wait_for_stability(item):
                    stable = wait_for_stable_file(
                        path=item.path,
                        stable_checks=self.cfg.watch.stable_checks,
                        poll_interval_seconds=self.cfg.watch.poll_interval_seconds,
                        max_wait_seconds=self.cfg.watch.stable_wait_seconds,
                    )
                    if not stable:
                        LOGGER.warning("Timed out waiting for stable file: %s", item.path)
                        continue
                process_fn(item.path, item.source_root)
            except Exception:
                LOGGER.exception("Watch worker failed for %s", item.path)
            finally:
                self._mark_finished(item.path)
                work_queue.task_done()

    def _seed_existing_files(self, root: Path, work_queue: queue.Queue[QueuedPath], *, priority: bool = False) -> int:
        if not root.exists():
            return 0

        queued = 0
        extensions = self.cfg.watch.priority_extensions if priority else self.cfg.watch.allowed_extensions
        iterator = self._iter_seed_candidates(root, extensions=extensions, allow_managed_paths=priority)
        for path in iterator:
            if self.cfg.is_excluded_from_watch(path, allow_managed_paths=priority):
                continue
            if not path.is_file():
                continue
            if not is_media_file(path, extensions):
                continue
            if not self._should_enqueue_existing_file(path):
                continue
            if self._enqueue_once(work_queue, QueuedPath(path=path, source_root=root, seeded=True, priority=priority)):
                queued += 1
        return queued

    def _iter_seed_candidates(
        self,
        root: Path,
        *,
        extensions: set[str] | None = None,
        allow_managed_paths: bool = False,
    ):
        if not self.cfg.watch.recursive:
            yield from self._iter_directory_seed_candidates(root, extensions=extensions)
            return
        if allow_managed_paths and Path("/usr/bin/find").exists():
            yield from self._iter_find_seed_candidates(
                root,
                extensions=extensions,
                allow_managed_paths=allow_managed_paths,
            )
            return
        yield from self._iter_walk_seed_candidates(
            root,
            extensions=extensions,
            allow_managed_paths=allow_managed_paths,
        )

    def _iter_directory_seed_candidates(self, root: Path, *, extensions: set[str] | None = None):
        try:
            for path in root.iterdir():
                if self._stop_event.is_set():
                    return
                if extensions is not None and path.suffix.lower() not in extensions:
                    continue
                yield path
        except OSError:
            return

    def _iter_walk_seed_candidates(
        self,
        root: Path,
        *,
        extensions: set[str] | None = None,
        allow_managed_paths: bool = False,
    ):
        for current_root, dirnames, filenames in os.walk(root):
            if self._stop_event.is_set():
                return
            current_path = Path(current_root)
            dirnames[:] = [
                dirname
                for dirname in dirnames
                if not self.cfg.is_excluded_from_watch(
                    current_path / dirname,
                    allow_managed_paths=allow_managed_paths,
                )
            ]
            for filename in filenames:
                path = current_path / filename
                if extensions is not None and path.suffix.lower() not in extensions:
                    continue
                yield path

    def _iter_find_seed_candidates(
        self,
        root: Path,
        *,
        extensions: set[str] | None = None,
        allow_managed_paths: bool = False,
    ):
        direct_files = self._find_seed_candidates(root, max_depth=1, extensions=extensions)
        if direct_files is not None:
            yield from direct_files

        child_dirs = self._find_seed_directories(root)
        if child_dirs is None:
            return
        for child_dir in child_dirs:
            if self._stop_event.is_set():
                return
            if self.cfg.is_excluded_from_watch(child_dir, allow_managed_paths=allow_managed_paths):
                continue
            find_candidates = self._find_seed_candidates(child_dir, extensions=extensions)
            if find_candidates is not None:
                yield from find_candidates

    def _find_seed_directories(self, root: Path):
        find_bin = Path("/usr/bin/find")
        if not find_bin.exists():
            return None
        cmd = [
            str(find_bin),
            str(root),
            "-mindepth",
            "1",
            "-maxdepth",
            "1",
            "-type",
            "d",
            "-print0",
        ]
        try:
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError:
            return None
        return self._stream_find_candidates(process, root)

    def _find_seed_candidates(self, root: Path, *, max_depth: int | None = None, extensions: set[str] | None = None):
        find_bin = Path("/usr/bin/find")
        if not find_bin.exists():
            return None

        suffix_args: list[str] = []
        for suffix in sorted(extensions or self.cfg.watch.allowed_extensions):
            if suffix_args:
                suffix_args.append("-o")
            suffix_args.extend(["-iname", f"*{suffix}"])
        if suffix_args:
            suffix_args = ["("] + suffix_args + [")"]

        cmd = [
            str(find_bin),
            str(root),
        ]
        if max_depth is not None:
            cmd.extend(["-maxdepth", str(max_depth)])
        prune_args: list[str] = ["-iname", RUNTIME_TEMP_DIRNAME]
        for part in sorted(GENERATED_METADATA_PATH_PARTS):
            prune_args.extend(["-o", "-iname", part])
        cmd.extend(
            [
                "(",
                *prune_args,
                ")",
                "-prune",
                "-o",
                "-type",
                "f",
                *suffix_args,
                "-print0",
            ]
        )
        try:
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError:
            return None
        return self._stream_find_candidates(process, root)

    def _stream_find_candidates(self, process: subprocess.Popen[bytes], root: Path):
        if process.stdout is None:
            return
        stdout = process.stdout
        fd = stdout.fileno()
        pending = b""
        last_activity = time.monotonic()
        idle_timed_out = False
        try:
            while not self._stop_event.is_set():
                ready, _, _ = select.select([fd], [], [], 0.5)
                if not ready:
                    if process.poll() is not None:
                        break
                    if (time.monotonic() - last_activity) >= FIND_IDLE_TIMEOUT_SECONDS:
                        idle_timed_out = True
                        LOGGER.warning("find seed scan idle for %gs; terminating subtree scan for %s", FIND_IDLE_TIMEOUT_SECONDS, root)
                        break
                    continue
                try:
                    chunk = os.read(fd, 64 * 1024)
                except OSError:
                    break
                if not chunk:
                    break
                last_activity = time.monotonic()
                pending += chunk
                parts = pending.split(b"\0")
                for raw in parts[:-1]:
                    if raw:
                        yield Path(raw.decode(errors="surrogateescape"))
                pending = parts[-1]
            if pending:
                yield Path(pending.decode(errors="surrogateescape"))
            if idle_timed_out:
                return_code = -1
            else:
                try:
                    return_code = process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    return_code = -1
            if return_code != 0 and not self._stop_event.is_set():
                LOGGER.warning("find seed scan returned %s for %s", return_code, root)
        finally:
            try:
                stdout.close()
            except OSError:
                pass
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)

    def _start_seed_scan(
        self,
        root: Path,
        work_queue: queue.Queue[QueuedPath],
        *,
        reason: str,
        priority: bool = False,
    ) -> bool:
        resolved_root = root.expanduser().resolve()
        with self._active_seed_roots_lock:
            if resolved_root in self._active_seed_roots:
                return False
            self._active_seed_roots.add(resolved_root)

        thread = threading.Thread(
            target=self._seed_existing_files_in_background,
            args=(root, work_queue, resolved_root, reason, priority),
            daemon=True,
            name=f"reeltranscode-seed-{root.name or 'root'}",
        )
        thread.start()
        return True

    def _seed_existing_files_in_background(
        self,
        root: Path,
        work_queue: queue.Queue[QueuedPath],
        resolved_root: Path,
        reason: str,
        priority: bool,
    ) -> None:
        try:
            LOGGER.info("Starting %s watch seed scan for: %s", reason, root)
            with self._seed_scan_lock:
                queued = self._seed_existing_files(root, work_queue, priority=priority)
            LOGGER.info("Completed %s watch seed scan for %s: queued=%d", reason, root, queued)
        except Exception:
            LOGGER.exception("Watch seed scan failed for %s", root)
        finally:
            with self._active_seed_roots_lock:
                self._active_seed_roots.discard(resolved_root)

    def _should_enqueue_existing_file(self, path: Path) -> bool:
        try:
            stat = path.stat()
        except OSError:
            return False
        row = self.state_store.get_file_record(path)
        if row is None:
            return True
        unchanged = row.size == stat.st_size and row.mtime_ns == stat.st_mtime_ns
        if unchanged and row.last_status in {"failed", "skipped"}:
            if row.stream_fp and row.stream_fp.startswith("analysis-failed:"):
                return False
            return row.processing_policy_fp != self._processing_policy_fp
        return True

    def _enqueue_once(self, work_queue: queue.Queue[QueuedPath], item: QueuedPath) -> bool:
        resolved = item.path.resolve()
        with self._queued_paths_lock:
            if resolved in self._queued_paths:
                return False
            self._queued_paths.add(resolved)
        self._publish_runtime_state()
        if isinstance(work_queue, queue.PriorityQueue):
            priority_rank = 0 if item.priority else 10
            work_queue.put(_PrioritizedQueuedPath(priority_rank, next(self._queue_sequence), item))
        else:
            work_queue.put(item)
        return True

    def _mark_started(self, path: Path) -> None:
        resolved = path.resolve()
        with self._queued_paths_lock:
            self._inflight_paths.add(resolved)
        self._publish_runtime_state()

    def _mark_finished(self, path: Path) -> None:
        resolved = path.resolve()
        with self._queued_paths_lock:
            self._queued_paths.discard(resolved)
            self._inflight_paths.discard(resolved)
        self._publish_runtime_state()

    def _publish_runtime_state(self) -> None:
        with self._queued_paths_lock:
            active_workers = len(self._inflight_paths)
            queued_paths = max(0, len(self._queued_paths) - active_workers)
        self.state_store.update_runtime_state(
            queued_paths=queued_paths,
            active_workers=active_workers,
            max_workers=self.cfg.concurrency.max_workers,
        )

    def _should_wait_for_stability(self, item: QueuedPath) -> bool:
        if not item.seeded:
            return True
        try:
            age_seconds = time.time() - item.path.stat().st_mtime
        except OSError:
            return True
        return age_seconds < self.cfg.watch.stable_wait_seconds
