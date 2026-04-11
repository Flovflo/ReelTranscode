from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reeltranscode.models import JobStatus
from reeltranscode.utils import ensure_parent, now_utc_iso


@dataclass(slots=True)
class FileRecord:
    path: str
    stream_fp: str | None
    metadata_fp: str | None
    size: int | None
    mtime_ns: int | None
    last_status: str | None
    last_job_id: str | None


@dataclass(slots=True)
class RuntimeState:
    watch_running: bool
    watch_paused: bool
    queued_paths: int
    active_workers: int
    max_workers: int
    updated_at: str


class StateStore:
    def __init__(self, db_path: Path):
        ensure_parent(db_path)
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS files (
                    path TEXT PRIMARY KEY,
                    device INTEGER,
                    inode INTEGER,
                    size INTEGER,
                    mtime_ns INTEGER,
                    stream_fp TEXT,
                    metadata_fp TEXT,
                    last_status TEXT,
                    last_job_id TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    source_path TEXT NOT NULL,
                    target_path TEXT,
                    strategy TEXT NOT NULL,
                    case_label TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error_class TEXT,
                    error_message TEXT,
                    stream_fp TEXT,
                    metadata_fp TEXT,
                    commands_json TEXT,
                    report_path TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_fingerprints (
                    stream_fp TEXT PRIMARY KEY,
                    output_path TEXT,
                    last_job_id TEXT,
                    last_seen_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    watch_running INTEGER NOT NULL DEFAULT 0,
                    watch_paused INTEGER NOT NULL DEFAULT 0,
                    queued_paths INTEGER NOT NULL DEFAULT 0,
                    active_workers INTEGER NOT NULL DEFAULT 0,
                    max_workers INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                INSERT INTO runtime_state(singleton, watch_running, watch_paused, queued_paths, active_workers, max_workers, updated_at)
                VALUES (1, 0, 0, 0, 0, 0, ?)
                ON CONFLICT(singleton) DO NOTHING
                """,
                (now_utc_iso(),),
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")

    def get_file_record(self, path: Path) -> FileRecord | None:
        row = self._conn.execute(
            "SELECT path, stream_fp, metadata_fp, size, mtime_ns, last_status, last_job_id FROM files WHERE path=?",
            (str(path),),
        ).fetchone()
        if not row:
            return None
        return FileRecord(
            path=row["path"],
            stream_fp=row["stream_fp"],
            metadata_fp=row["metadata_fp"],
            size=row["size"],
            mtime_ns=row["mtime_ns"],
            last_status=row["last_status"],
            last_job_id=row["last_job_id"],
        )

    def should_skip(
        self,
        path: Path,
        stream_fp: str,
        metadata_fp: str,
        size: int | None,
        mtime_ns: int | None,
    ) -> tuple[bool, str | None]:
        row = self.get_file_record(path)
        if not row:
            return False, None
        if row.last_status == JobStatus.SUCCESS.value and row.stream_fp == stream_fp and row.size == size:
            if row.metadata_fp == metadata_fp:
                return True, "identical_stream_and_metadata"
            return True, "metadata_only_change"
        return False, None

    def mark_job_started(
        self,
        job_id: str,
        source_path: Path,
        target_path: Path | None,
        strategy: str,
        case_label: str,
        stream_fp: str,
        metadata_fp: str,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO jobs(
                    job_id, source_path, target_path, strategy, case_label, status, attempts,
                    stream_fp, metadata_fp, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    job_id,
                    str(source_path),
                    str(target_path) if target_path else None,
                    strategy,
                    case_label,
                    JobStatus.RUNNING.value,
                    stream_fp,
                    metadata_fp,
                    now_utc_iso(),
                ),
            )

    def mark_job_finished(
        self,
        job_id: str,
        status: JobStatus,
        error_class: str | None,
        error_message: str | None,
        report_path: Path | None,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE jobs
                SET status=?, error_class=?, error_message=?, report_path=?, finished_at=?
                WHERE job_id=?
                """,
                (
                    status.value,
                    error_class,
                    error_message,
                    str(report_path) if report_path else None,
                    now_utc_iso(),
                    job_id,
                ),
            )

    def get_runtime_state(self) -> RuntimeState:
        row = self._conn.execute(
            """
            SELECT watch_running, watch_paused, queued_paths, active_workers, max_workers, updated_at
            FROM runtime_state
            WHERE singleton=1
            """
        ).fetchone()
        if row is None:
            return RuntimeState(False, False, 0, 0, 0, now_utc_iso())
        return RuntimeState(
            watch_running=bool(row["watch_running"]),
            watch_paused=bool(row["watch_paused"]),
            queued_paths=int(row["queued_paths"]),
            active_workers=int(row["active_workers"]),
            max_workers=int(row["max_workers"]),
            updated_at=str(row["updated_at"]),
        )

    def set_watch_paused(self, paused: bool) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE runtime_state
                SET watch_paused=?, updated_at=?
                WHERE singleton=1
                """,
                (1 if paused else 0, now_utc_iso()),
            )

    def is_watch_paused(self) -> bool:
        return self.get_runtime_state().watch_paused

    def update_runtime_state(
        self,
        *,
        watch_running: bool | None = None,
        queued_paths: int | None = None,
        active_workers: int | None = None,
        max_workers: int | None = None,
    ) -> None:
        runtime = self.get_runtime_state()
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE runtime_state
                SET watch_running=?, watch_paused=?, queued_paths=?, active_workers=?, max_workers=?, updated_at=?
                WHERE singleton=1
                """,
                (
                    1 if (runtime.watch_running if watch_running is None else watch_running) else 0,
                    1 if runtime.watch_paused else 0,
                    runtime.queued_paths if queued_paths is None else max(0, int(queued_paths)),
                    runtime.active_workers if active_workers is None else max(0, int(active_workers)),
                    runtime.max_workers if max_workers is None else max(0, int(max_workers)),
                    now_utc_iso(),
                ),
            )

    def status_snapshot(self, limit: int = 50) -> dict[str, Any]:
        capped_limit = max(1, min(int(limit), 500))
        summary = {
            "pending": 0,
            "running": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "total": 0,
        }

        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS c FROM jobs GROUP BY status"
        ).fetchall()
        for row in rows:
            status = str(row["status"])
            count = int(row["c"])
            if status in summary:
                summary[status] = count
            summary["total"] += count

        latest_rows = self._conn.execute(
            """
            SELECT
                job_id,
                status,
                case_label,
                strategy,
                source_path,
                target_path,
                started_at,
                finished_at,
                error_class,
                error_message
            FROM jobs
            ORDER BY COALESCE(finished_at, started_at) DESC
            LIMIT ?
            """,
            (capped_limit,),
        ).fetchall()
        latest_jobs = [
            {
                "job_id": row["job_id"],
                "status": row["status"],
                "case_label": row["case_label"],
                "strategy": row["strategy"],
                "source_path": row["source_path"],
                "target_path": row["target_path"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "error_class": row["error_class"],
                "error_message": row["error_message"],
            }
            for row in latest_rows
        ]
        runtime = self.get_runtime_state()
        summary["pending"] = max(summary["pending"], runtime.queued_paths)
        summary["running"] = max(summary["running"], runtime.active_workers)
        return {
            "summary": summary,
            "latest_jobs": latest_jobs,
            "runtime": {
                "watch_running": runtime.watch_running,
                "watch_paused": runtime.watch_paused,
                "queued_paths": runtime.queued_paths,
                "active_workers": runtime.active_workers,
                "max_workers": runtime.max_workers,
                "updated_at": runtime.updated_at,
            },
        }

    def upsert_file_state(
        self,
        path: Path,
        device: int | None,
        inode: int | None,
        size: int | None,
        mtime_ns: int | None,
        stream_fp: str,
        metadata_fp: str,
        status: JobStatus,
        job_id: str,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO files(path, device, inode, size, mtime_ns, stream_fp, metadata_fp, last_status, last_job_id, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    device=excluded.device,
                    inode=excluded.inode,
                    size=excluded.size,
                    mtime_ns=excluded.mtime_ns,
                    stream_fp=excluded.stream_fp,
                    metadata_fp=excluded.metadata_fp,
                    last_status=excluded.last_status,
                    last_job_id=excluded.last_job_id,
                    updated_at=excluded.updated_at
                """,
                (
                    str(path),
                    device,
                    inode,
                    size,
                    mtime_ns,
                    stream_fp,
                    metadata_fp,
                    status.value,
                    job_id,
                    now_utc_iso(),
                ),
            )
            if status == JobStatus.SUCCESS:
                self._conn.execute(
                    """
                    INSERT INTO processed_fingerprints(stream_fp, output_path, last_job_id, last_seen_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(stream_fp) DO UPDATE SET
                        output_path=excluded.output_path,
                        last_job_id=excluded.last_job_id,
                        last_seen_at=excluded.last_seen_at
                    """,
                    (stream_fp, str(path), job_id, now_utc_iso()),
                )
