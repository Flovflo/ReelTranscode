from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from reeltranscode.config import AppConfig
from reeltranscode.utils import is_media_file, wait_for_stable_file

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class QueuedPath:
    path: Path
    source_root: Path
    seeded: bool = False


class _MediaEventHandler(FileSystemEventHandler):
    def __init__(self, cfg: AppConfig, root: Path, work_queue: queue.Queue[QueuedPath]):
        self.cfg = cfg
        self.root = root
        self.work_queue = work_queue
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
        if not is_media_file(path, self.cfg.watch.allowed_extensions):
            return

        key = str(path.resolve())
        now = time.time()
        previous = self._recent.get(key)
        if previous and (now - previous) < 5:
            return
        self._recent[key] = now
        self.work_queue.put(QueuedPath(path=path, source_root=self.root, seeded=False))


class LibraryWatcher:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self._stop_event = threading.Event()

    def run_forever(self, process_fn) -> None:
        if not self.cfg.watch.folders:
            raise RuntimeError("No watch folders configured")

        work_queue: queue.Queue[QueuedPath] = queue.Queue()
        observers: list[Observer] = []

        for root in self.cfg.watch.folders:
            handler = _MediaEventHandler(self.cfg, root, work_queue)
            observer = Observer()
            observer.schedule(handler, str(root), recursive=self.cfg.watch.recursive)
            observer.start()
            observers.append(observer)
            LOGGER.info("Watching folder: %s", root)
            queued = self._seed_existing_files(root, work_queue)
            if queued > 0:
                LOGGER.info("Queued %d existing media files from: %s", queued, root)

        workers = [
            threading.Thread(target=self._worker, args=(work_queue, process_fn), daemon=True)
            for _ in range(max(1, self.cfg.concurrency.max_workers))
        ]
        for worker in workers:
            worker.start()

        try:
            while not self._stop_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            LOGGER.info("Stopping watcher")
        finally:
            self._stop_event.set()
            for observer in observers:
                observer.stop()
            for observer in observers:
                observer.join(timeout=10)

    def stop(self) -> None:
        self._stop_event.set()

    def _worker(self, work_queue: queue.Queue[QueuedPath], process_fn) -> None:
        while not self._stop_event.is_set():
            try:
                item = work_queue.get(timeout=1)
            except queue.Empty:
                continue

            try:
                if not item.seeded:
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
                work_queue.task_done()

    def _seed_existing_files(self, root: Path, work_queue: queue.Queue[QueuedPath]) -> int:
        if not root.exists():
            return 0

        queued = 0
        iterator = root.rglob("*") if self.cfg.watch.recursive else root.glob("*")
        for path in iterator:
            if not path.is_file():
                continue
            if not is_media_file(path, self.cfg.watch.allowed_extensions):
                continue
            work_queue.put(QueuedPath(path=path, source_root=root, seeded=True))
            queued += 1
        return queued
