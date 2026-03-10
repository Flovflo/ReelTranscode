from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from reeltranscode.analyzer import FFprobeAnalyzer, ProbeError
from reeltranscode.config import AppConfig


class _Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_analyzer_falls_back_when_primary_ffprobe_is_broken():
    cfg = AppConfig.from_dict(
        {
            "tooling": {
                "ffmpeg_bin": "/opt/homebrew/bin/ffmpeg",
                "ffprobe_bin": "/Applications/ReelTranscodeApp.app/Contents/Resources/bin/ffprobe",
            }
        }
    )
    analyzer = FFprobeAnalyzer(cfg)
    payload = json.dumps(
        {
            "format": {"format_name": "matroska,webm", "duration": "120.0"},
            "streams": [{"index": 0, "codec_type": "video", "codec_name": "hevc", "pix_fmt": "yuv420p10le"}],
        }
    )

    def fake_run(command, text, capture_output, check):  # noqa: ANN001
        binary = command[0]
        if binary == "/Applications/ReelTranscodeApp.app/Contents/Resources/bin/ffprobe":
            return _Result(
                5,
                stderr=(
                    "dyld: Library not loaded: "
                    "/opt/homebrew/Cellar/ffmpeg/8.0.1/lib/libavdevice.62.dylib"
                ),
            )
        if binary == "/opt/homebrew/bin/ffprobe":
            return _Result(0, stdout=payload)
        return _Result(1, stderr=f"unexpected binary {binary}")

    with patch("reeltranscode.analyzer.subprocess.run", side_effect=fake_run):
        media, used_command = analyzer.analyze(Path("/tmp/input.mkv"))

    assert used_command[0] == "/opt/homebrew/bin/ffprobe"
    assert media.format_name == "matroska,webm"
    assert cfg.tooling.ffprobe_bin == "/opt/homebrew/bin/ffprobe"


def test_analyzer_raises_probe_error_when_all_candidates_fail():
    cfg = AppConfig.from_dict({"tooling": {"ffprobe_bin": "/bad/ffprobe"}})
    analyzer = FFprobeAnalyzer(cfg)

    with patch.object(analyzer, "_ffprobe_candidates", return_value=["/bad/ffprobe", "/bad/ffprobe2"]):
        with patch(
            "reeltranscode.analyzer.subprocess.run",
            side_effect=lambda command, text, capture_output, check: _Result(
                5,
                stderr=f"failed: {command[0]}",
            ),
        ):
            try:
                analyzer.analyze(Path("/tmp/input.mkv"))
            except ProbeError as exc:
                message = str(exc)
            else:
                assert False, "ProbeError was expected"

    assert "/bad/ffprobe: failed: /bad/ffprobe" in message
    assert "/bad/ffprobe2: failed: /bad/ffprobe2" in message


def test_analyzer_skips_missing_candidate_and_uses_next_available_binary():
    cfg = AppConfig.from_dict(
        {
            "tooling": {
                "ffmpeg_bin": "/Applications/ReelTranscodeApp.app/Contents/Resources/bin/ffmpeg",
                "ffprobe_bin": "/usr/local/bin/ffprobe",
            }
        }
    )
    analyzer = FFprobeAnalyzer(cfg)
    payload = json.dumps(
        {
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "120.0"},
            "streams": [{"index": 0, "codec_type": "video", "codec_name": "hevc", "pix_fmt": "yuv420p10le"}],
        }
    )

    def fake_run(command, text, capture_output, check):  # noqa: ANN001
        binary = command[0]
        if binary == "/usr/local/bin/ffprobe":
            raise FileNotFoundError(2, "No such file or directory", binary)
        if binary == "/Applications/ReelTranscodeApp.app/Contents/Resources/bin/ffprobe":
            return _Result(0, stdout=payload)
        return _Result(1, stderr=f"unexpected binary {binary}")

    with patch("reeltranscode.analyzer.subprocess.run", side_effect=fake_run):
        media, used_command = analyzer.analyze(Path("/tmp/input.mp4"))

    assert used_command[0] == "/Applications/ReelTranscodeApp.app/Contents/Resources/bin/ffprobe"
    assert media.format_name == "mov,mp4,m4a,3gp,3g2,mj2"
    assert cfg.tooling.ffprobe_bin == "/Applications/ReelTranscodeApp.app/Contents/Resources/bin/ffprobe"
