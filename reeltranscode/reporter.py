from __future__ import annotations

import csv
import json
from pathlib import Path

from reeltranscode.config import AppConfig
from reeltranscode.models import JobReport
from reeltranscode.utils import ensure_dir, ensure_parent


class Reporter:
    def __init__(self, config: AppConfig):
        self.config = config
        ensure_dir(self.config.paths.reports_dir)
        ensure_parent(self.config.paths.csv_summary)

    def write_job_report(self, report: JobReport) -> Path:
        report_path = self.config.paths.reports_dir / f"{report.job_id}.json"
        with report_path.open("w", encoding="utf-8") as handle:
            json.dump(report.__dict__, handle, indent=2, ensure_ascii=True)
        self._append_csv(report)
        return report_path

    def _append_csv(self, report: JobReport) -> None:
        file_exists = self.config.paths.csv_summary.exists()
        with self.config.paths.csv_summary.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            if not file_exists:
                writer.writerow(
                    [
                        "job_id",
                        "status",
                        "case_label",
                        "strategy",
                        "source_path",
                        "target_path",
                        "duration_seconds",
                        "expected_direct_play_safe",
                        "error_class",
                    ]
                )
            writer.writerow(
                [
                    report.job_id,
                    report.status,
                    report.case_label,
                    report.strategy,
                    report.source_path,
                    report.target_path,
                    f"{report.duration_seconds:.2f}",
                    report.expected_direct_play_safe,
                    report.error_class,
                ]
            )
