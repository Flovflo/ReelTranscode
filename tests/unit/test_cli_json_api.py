from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from reeltranscode.cli import _run_config_export, _run_config_validate, _run_status
from reeltranscode.config import AppConfig
from reeltranscode.models import JobStatus
from reeltranscode.state_store import StateStore


def test_config_export_json_contract(capsys):
    cfg = AppConfig.from_dict({})
    _run_config_export(cfg, json_output=True)
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert payload["api_version"] == 1
    assert payload["config"]["remux"]["preferred_container"] == "mp4"
    assert isinstance(payload["config"]["watch"]["allowed_extensions"], list)
    assert payload["config"]["video"]["hevc_tag"] == "hvc1"
    assert payload["config"]["audio"]["ensure_aac_fallback_stereo_when_missing"] is True


def test_config_validate_reports_structured_errors(tmp_path: Path, capsys):
    config_path = tmp_path / "bad.yaml"
    config_path.write_text(
        """
output:
  mode: invalid_mode
concurrency:
  max_workers: 0
""".strip(),
        encoding="utf-8",
    )

    _run_config_validate(config_path, json_output=True)
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert payload["api_version"] == 1
    assert payload["valid"] is False
    assert {"field": "output.mode", "message": "must be one of ['archive_original', 'keep_original', 'replace_original']"} in payload["errors"]
    assert {"field": "concurrency.max_workers", "message": "must be >= 1"} in payload["errors"]


def test_config_validate_reports_missing_absolute_tooling_bins(tmp_path: Path, capsys):
    config_path = tmp_path / "bad_tools.yaml"
    config_path.write_text(
        f"""
tooling:
  ffmpeg_bin: {tmp_path / "missing-ffmpeg"}
  ffprobe_bin: {tmp_path / "missing-ffprobe"}
""".strip(),
        encoding="utf-8",
    )

    _run_config_validate(config_path, json_output=True)
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert payload["valid"] is False
    assert {"field": "tooling.ffmpeg_bin", "message": f"binary not found: {tmp_path / 'missing-ffmpeg'}"} in payload["errors"]
    assert {"field": "tooling.ffprobe_bin", "message": f"binary not found: {tmp_path / 'missing-ffprobe'}"} in payload["errors"]


def test_config_validate_rejects_invalid_hevc_tag(tmp_path: Path, capsys):
    config_path = tmp_path / "bad_hevc_tag.yaml"
    config_path.write_text(
        """
video:
  hevc_tag: badtag
""".strip(),
        encoding="utf-8",
    )

    _run_config_validate(config_path, json_output=True)
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert payload["valid"] is False
    assert {"field": "video.hevc_tag", "message": "must be one of ['hvc1', 'hev1']"} in payload["errors"]


def test_config_validate_rejects_watch_overlap_with_managed_paths(tmp_path: Path, capsys):
    watch_root = tmp_path / "media"
    config_path = tmp_path / "overlap.yaml"
    config_path.write_text(
        f"""
watch:
  folders:
    - {watch_root}
output:
  output_root: {watch_root / "optimized"}
paths:
  temp_dir: {tmp_path / "tmp"}
""".strip(),
        encoding="utf-8",
    )

    _run_config_validate(config_path, json_output=True)
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert payload["valid"] is False
    assert {
        "field": "output.output_root",
        "message": f"must not overlap any watch folder: {(watch_root / 'optimized').resolve()}",
    } in payload["errors"]


def test_status_json_contract(tmp_path: Path, capsys):
    cfg = AppConfig.from_dict(
        {
            "paths": {
                "state_db": str(tmp_path / "state.db"),
                "reports_dir": str(tmp_path / "reports"),
                "csv_summary": str(tmp_path / "reports" / "summary.csv"),
            }
        }
    )
    state = StateStore(cfg.paths.state_db)
    try:
        state.update_runtime_state(watch_running=True, queued_paths=3, active_workers=1, max_workers=4)
        job_id = "job-1"
        source = tmp_path / "movie.mkv"
        state.mark_job_started(
            job_id=job_id,
            source_path=source,
            target_path=None,
            strategy="remux_only",
            case_label="B_CONTAINER_ONLY",
            stream_fp="fp-stream",
            metadata_fp="fp-meta",
        )
        state.mark_job_finished(
            job_id=job_id,
            status=JobStatus.SUCCESS,
            error_class=None,
            error_message=None,
            report_path=None,
        )

        _run_status(cfg, state, limit=10, json_output=True)
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["api_version"] == 1
        assert payload["summary"]["total"] == 1
        assert payload["summary"]["pending"] == 3
        assert payload["summary"]["running"] == 1
        assert payload["summary"]["success"] == 1
        assert len(payload["latest_jobs"]) == 1
        assert payload["latest_jobs"][0]["job_id"] == job_id
        assert payload["paths"]["state_db"] == str(cfg.paths.state_db)
        assert payload["runtime"]["watch_running"] is True
        assert payload["runtime"]["queued_paths"] == 3
        assert payload["runtime"]["active_workers"] == 1
        assert payload["runtime"]["max_workers"] == 4
        assert "capabilities" in payload
    finally:
        state.close()


def test_runtime_pause_state_is_persisted(tmp_path: Path):
    cfg = AppConfig.from_dict({"paths": {"state_db": str(tmp_path / "state.db")}})
    state = StateStore(cfg.paths.state_db)
    try:
        assert state.is_watch_paused() is False
        state.set_watch_paused(True)
        assert state.is_watch_paused() is True
        state.set_watch_paused(False)
        assert state.is_watch_paused() is False
    finally:
        state.close()


def test_runtime_pause_state_tolerates_legacy_null_metrics(tmp_path: Path):
    db_path = tmp_path / "state.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE runtime_state (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                watch_running INTEGER,
                watch_paused INTEGER,
                queued_paths INTEGER,
                active_workers INTEGER,
                max_workers INTEGER,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO runtime_state(singleton, watch_running, watch_paused, queued_paths, active_workers, max_workers, updated_at)
            VALUES (1, 1, 0, NULL, NULL, NULL, NULL)
            """
        )

    state = StateStore(db_path)
    try:
        runtime = state.get_runtime_state()
        assert runtime.watch_running is True
        assert runtime.watch_paused is False
        assert runtime.queued_paths == 0
        assert runtime.active_workers == 0
        assert runtime.max_workers == 0
        assert runtime.updated_at
        assert state.is_watch_paused() is False
    finally:
        state.close()


def test_mark_incomplete_running_jobs_failed_reconciles_stale_entries(tmp_path: Path):
    cfg = AppConfig.from_dict({"paths": {"state_db": str(tmp_path / "state.db")}})
    state = StateStore(cfg.paths.state_db)
    try:
        state.mark_job_started(
            job_id="stale-job",
            source_path=tmp_path / "movie.mkv",
            target_path=tmp_path / "movie.mp4",
            strategy="subtitle_only",
            case_label="D_SUBTITLE_ONLY",
            stream_fp="stream-fp",
            metadata_fp="meta-fp",
        )

        reconciled = state.mark_incomplete_running_jobs_failed()
        snapshot = state.status_snapshot(limit=10)

        assert reconciled == 1
        assert snapshot["summary"]["running"] == 0
        assert snapshot["summary"]["failed"] == 1
        assert snapshot["latest_jobs"][0]["job_id"] == "stale-job"
        assert snapshot["latest_jobs"][0]["status"] == JobStatus.FAILED.value
        assert snapshot["latest_jobs"][0]["error_class"] == "InterruptedError"
        assert snapshot["latest_jobs"][0]["error_message"] == "Job was interrupted before completion by a watcher restart"
        assert snapshot["latest_jobs"][0]["finished_at"] is not None
    finally:
        state.close()


def test_status_snapshot_counts_only_latest_state_per_source(tmp_path: Path):
    cfg = AppConfig.from_dict({"paths": {"state_db": str(tmp_path / "state.db")}})
    state = StateStore(cfg.paths.state_db)
    source = tmp_path / "movie.mkv"
    try:
        state.mark_job_started(
            job_id="failed-job",
            source_path=source,
            target_path=tmp_path / "movie.mp4",
            strategy="subtitle_only",
            case_label="D_SUBTITLE_ONLY",
            stream_fp="stream-fp-1",
            metadata_fp="meta-fp-1",
        )
        state.mark_job_finished(
            job_id="failed-job",
            status=JobStatus.FAILED,
            error_class="RuntimeError",
            error_message="boom",
            report_path=None,
        )
        state.mark_job_started(
            job_id="success-job",
            source_path=source,
            target_path=tmp_path / "movie.mp4",
            strategy="subtitle_only",
            case_label="D_SUBTITLE_ONLY",
            stream_fp="stream-fp-2",
            metadata_fp="meta-fp-2",
        )
        state.mark_job_finished(
            job_id="success-job",
            status=JobStatus.SUCCESS,
            error_class=None,
            error_message=None,
            report_path=None,
        )

        snapshot = state.status_snapshot(limit=10)

        assert snapshot["summary"]["total"] == 1
        assert snapshot["summary"]["success"] == 1
        assert snapshot["summary"]["failed"] == 0
        assert len(snapshot["latest_jobs"]) == 1
        assert snapshot["latest_jobs"][0]["job_id"] == "success-job"
        assert snapshot["latest_jobs"][0]["status"] == JobStatus.SUCCESS.value
    finally:
        state.close()


def test_status_snapshot_ignores_transient_hidden_media_sources(tmp_path: Path):
    cfg = AppConfig.from_dict({"paths": {"state_db": str(tmp_path / "state.db")}})
    state = StateStore(cfg.paths.state_db)
    hidden_source = tmp_path / ".movie.tmp.mp4"
    try:
        state.mark_job_started(
            job_id="hidden-temp-job",
            source_path=hidden_source,
            target_path=tmp_path / "movie.mp4",
            strategy="subtitle_only",
            case_label="D_SUBTITLE_ONLY",
            stream_fp="stream-fp",
            metadata_fp="meta-fp",
        )
        state.mark_job_finished(
            job_id="hidden-temp-job",
            status=JobStatus.FAILED,
            error_class="RuntimeError",
            error_message="temporary file should be hidden from status",
            report_path=None,
        )

        snapshot = state.status_snapshot(limit=10)

        assert snapshot["summary"]["total"] == 0
        assert snapshot["summary"]["failed"] == 0
        assert snapshot["latest_jobs"] == []
    finally:
        state.close()
