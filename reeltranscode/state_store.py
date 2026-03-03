from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

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

    def was_stream_processed(self, stream_fp: str) -> bool:
        row = self._conn.execute(
            "SELECT stream_fp FROM processed_fingerprints WHERE stream_fp=?",
            (stream_fp,),
        ).fetchone()
        return row is not None

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
