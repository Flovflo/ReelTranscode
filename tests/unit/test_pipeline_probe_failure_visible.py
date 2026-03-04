from __future__ import annotations

from pathlib import Path

from reeltranscode.analyzer import ProbeError
from reeltranscode.config import AppConfig
from reeltranscode.pipeline import PipelineProcessor
from reeltranscode.reporter import Reporter
from reeltranscode.state_store import StateStore


class _FailingAnalyzer:
    def analyze(self, path: Path):
        raise ProbeError("ffprobe missing dylib")


def test_probe_failure_is_persisted_in_status_snapshot(tmp_path):
    cfg = AppConfig.from_dict(
        {
            "watch": {"folders": [str(tmp_path / "watch")]},
            "paths": {
                "state_db": str(tmp_path / "state" / "reeltranscode.db"),
                "reports_dir": str(tmp_path / "reports"),
                "csv_summary": str(tmp_path / "reports" / "summary.csv"),
                "temp_dir": str(tmp_path / "tmp"),
            },
            "output": {
                "mode": "keep_original",
                "output_root": str(tmp_path / "out"),
                "archive_root": str(tmp_path / "archive"),
                "overwrite": True,
            },
        }
    )

    source = tmp_path / "watch" / "broken.mkv"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fake")

    state = StateStore(cfg.paths.state_db)
    reporter = Reporter(cfg)
    processor = PipelineProcessor(config=cfg, state_store=state, reporter=reporter)
    processor.analyzer = _FailingAnalyzer()

    try:
        report = processor.process_path(source, source.parent, dry_run_override=False)
        snapshot = state.status_snapshot(limit=20)
    finally:
        state.close()

    assert report.status == "failed"
    assert report.error_class == "ProbeError"
    assert snapshot["summary"]["total"] == 1
    assert snapshot["summary"]["failed"] == 1
    assert snapshot["latest_jobs"][0]["source_path"] == str(source)
