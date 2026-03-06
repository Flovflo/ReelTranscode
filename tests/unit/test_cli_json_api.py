from __future__ import annotations

import json
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
        assert payload["summary"]["success"] == 1
        assert len(payload["latest_jobs"]) == 1
        assert payload["latest_jobs"][0]["job_id"] == job_id
        assert payload["paths"]["state_db"] == str(cfg.paths.state_db)
        assert "capabilities" in payload
    finally:
        state.close()
