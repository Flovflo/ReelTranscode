from __future__ import annotations

import sys
from pathlib import Path

from reeltranscode.cli import build_parser
from reeltranscode.models import OcrSubtitleTask
from reeltranscode.subtitle_ocr import _build_ocr_worker_command


def _task(tmp_path: Path) -> OcrSubtitleTask:
    return OcrSubtitleTask(
        source_subtitle_index=0,
        source_codec="hdmv_pgs_subtitle",
        language="fre",
        title="Forced",
        default=True,
        forced=True,
        hearing_impaired=False,
        captions=False,
        sup_path=tmp_path / "subtitle_0.sup",
        output_path=tmp_path / "subtitle_0.fre.srt",
    )


def test_build_ocr_worker_command_uses_module_launch_in_python_mode(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(sys, "executable", "/usr/bin/python3")
    monkeypatch.delattr(sys, "frozen", raising=False)

    command = _build_ocr_worker_command(_task(tmp_path), max_workers=3)

    assert command[:4] == ["/usr/bin/python3", "-m", "reeltranscode.cli", "ocr-worker"]
    assert command[-2:] == ["--max-workers", "3"]


def test_build_ocr_worker_command_uses_internal_subcommand_in_frozen_mode(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(sys, "executable", "/Applications/ReelTranscodeApp.app/Contents/Resources/runtime/ReelTranscodeCore/ReelTranscodeCore")
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    command = _build_ocr_worker_command(_task(tmp_path))

    assert command[:2] == [
        "/Applications/ReelTranscodeApp.app/Contents/Resources/runtime/ReelTranscodeCore/ReelTranscodeCore",
        "ocr-worker",
    ]
    assert "--task-json" in command


def test_cli_parser_accepts_internal_ocr_worker_command():
    args = build_parser().parse_args(["ocr-worker", "--task-json", "{\"source_subtitle_index\":0}"])

    assert args.command == "ocr-worker"
