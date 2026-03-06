from __future__ import annotations

from pathlib import Path

from reeltranscode.config import AppConfig
from reeltranscode.ffmpeg_runner import CommandResult
from reeltranscode.models import (
    CaseLabel,
    CommandStep,
    CompatibilityDetails,
    Decision,
    ExecutionPlan,
    MediaInfo,
    StreamInfo,
    Strategy,
)
from reeltranscode.pipeline import PipelineProcessor
from reeltranscode.state_store import StateStore


def _media(path: Path, format_name: str, codec_tag: str | None) -> MediaInfo:
    return MediaInfo(
        path=path,
        format_name=format_name,
        duration=120.0,
        bit_rate=20_000_000,
        size=1_000_000_000,
        streams=[
            StreamInfo.from_probe(
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "hevc",
                    "codec_tag_string": codec_tag,
                    "profile": "Main 10",
                    "pix_fmt": "yuv420p10le",
                    "width": 3840,
                    "height": 1606,
                    "avg_frame_rate": "24/1",
                    "disposition": {"default": 1},
                }
            ),
            StreamInfo.from_probe(
                {
                    "index": 1,
                    "codec_type": "audio",
                    "codec_name": "eac3",
                    "channels": 6,
                    "disposition": {"default": 1},
                    "tags": {"language": "fra"},
                }
            ),
        ],
        raw_probe={},
    )


class _FakeAnalyzer:
    def __init__(self, source_path: Path, temp_path: Path, source_media: MediaInfo, output_media: MediaInfo):
        self.source_path = source_path
        self.temp_path = temp_path
        self.source_media = source_media
        self.output_media = output_media

    def analyze(self, path: Path):
        if path == self.source_path:
            return self.source_media, ["ffprobe", str(path)]
        if path == self.temp_path:
            return self.output_media, ["ffprobe", str(path)]
        raise AssertionError(f"Unexpected analyze path: {path}")

    def stream_fingerprint(self, _media: MediaInfo) -> str:
        return "stream-fp"

    def metadata_fingerprint(self, _media: MediaInfo) -> str:
        return "meta-fp"


class _FakeEngine:
    def __init__(self, decision: Decision, compatibility: CompatibilityDetails):
        self.decision = decision
        self.compatibility = compatibility

    def decide(self, _media: MediaInfo):
        return self.decision, self.compatibility


class _FakePlanner:
    def __init__(self, source_path: Path, target_path: Path, temp_path: Path):
        self.source_path = source_path
        self.target_path = target_path
        self.temp_path = temp_path

    def preview_target_path(self, _source: Path, _source_root: Path | None) -> Path:
        return self.target_path

    def build(self, _media, decision, _compatibility, _source_root):
        return ExecutionPlan(
            source_path=self.source_path,
            target_path=self.target_path,
            temp_path=self.temp_path,
            strategy=decision.strategy,
            case_label=decision.case_label,
            steps=[
                CommandStep(
                    name="main_ffmpeg",
                    command=["mock-ffmpeg", str(self.temp_path)],
                    expected_outputs=[self.temp_path],
                    cwd=self.target_path.parent,
                )
            ],
        )


class _FakeRunner:
    def run(self, command: list[str], cwd: Path | None = None):  # noqa: ARG002
        Path(command[-1]).write_bytes(b"fake-mp4")
        return CommandResult(command=command, return_code=0, stdout="", stderr="")


class _BrokenReporter:
    def write_job_report(self, _report):
        raise OSError(28, "No space left on device")


def test_pipeline_marks_job_failed_when_report_write_fails(tmp_path: Path):
    source = tmp_path / "watch" / "movie.mkv"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    target = tmp_path / "optimized" / "movie.mp4"
    target.parent.mkdir(parents=True)
    temp = target.parent / ".movie.tmp.mp4"

    cfg = AppConfig.from_dict(
        {
            "output": {
                "mode": "keep_original",
                "output_root": str(tmp_path / "optimized"),
                "overwrite": True,
            },
            "paths": {
                "state_db": str(tmp_path / "state" / "reeltranscode.db"),
                "reports_dir": str(tmp_path / "reports"),
                "csv_summary": str(tmp_path / "reports" / "summary.csv"),
                "temp_dir": str(tmp_path / "tmp"),
            },
        }
    )
    state = StateStore(cfg.paths.state_db)
    processor = PipelineProcessor(config=cfg, state_store=state, reporter=_BrokenReporter())

    source_media = _media(source, "matroska,webm", codec_tag=None)
    output_media = _media(temp, "mov,mp4,m4a,3gp,3g2,mj2", codec_tag="hvc1")
    processor.analyzer = _FakeAnalyzer(source, temp, source_media, output_media)
    processor.engine = _FakeEngine(
        Decision(
            strategy=Strategy.REMUX_ONLY,
            case_label=CaseLabel.B,
            reasons=["remux"],
            expected_container="mp4",
            expected_direct_play_safe=True,
        ),
        CompatibilityDetails(
            container_ok=False,
            video_ok=True,
            audio_ok=True,
            subtitle_ok=True,
            dv_present=False,
            dv_profile=None,
            hdr10_present=False,
            requires_container_change=True,
            requires_audio_fix=False,
            requires_subtitle_fix=False,
            requires_video_transcode=False,
            reasons=[],
        ),
    )
    processor.planner = _FakePlanner(source, target, temp)
    processor.runner = _FakeRunner()

    try:
        report = processor.process_path(source, source.parent, dry_run_override=False)
        snapshot = state.status_snapshot(limit=10)
    finally:
        state.close()

    assert report.status == "failed"
    assert report.error_class == "OSError"
    assert snapshot["summary"]["failed"] == 1
