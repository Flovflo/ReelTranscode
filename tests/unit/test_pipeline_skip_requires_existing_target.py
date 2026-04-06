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
    JobStatus,
    MediaInfo,
    StreamInfo,
    Strategy,
)
from reeltranscode.pipeline import PipelineProcessor
from reeltranscode.reporter import Reporter
from reeltranscode.state_store import StateStore


def _media(path: Path) -> MediaInfo:
    return MediaInfo(
        path=path,
        format_name="matroska,webm",
        duration=120.0,
        bit_rate=20_000_000,
        size=1_000_000_000,
        streams=[
            StreamInfo.from_probe(
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "hevc",
                    "codec_tag_string": "hvc1",
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
                    "channels": 8,
                    "channel_layout": "7.1",
                    "disposition": {"default": 1},
                    "tags": {"language": "fra"},
                }
            ),
        ],
        raw_probe={},
    )


class _FakeAnalyzer:
    def __init__(self, media: MediaInfo):
        self.media = media

    def analyze(self, path: Path):
        assert path == self.media.path
        return self.media, ["ffprobe", str(path)]

    def stream_fingerprint(self, _media: MediaInfo) -> str:
        return "stream-fp"

    def metadata_fingerprint(self, _media: MediaInfo) -> str:
        return "meta-fp"


class _FakeEngine:
    def decide(self, _media: MediaInfo):
        return (
            Decision(
                strategy=Strategy.REMUX_ONLY,
                case_label=CaseLabel.B,
                reasons=["Container conversion required"],
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
            workspace_dir=None,
            strategy=decision.strategy,
            case_label=decision.case_label,
            steps=[
                CommandStep(
                    name="main_ffmpeg",
                    command=["mock-ffmpeg", str(self.temp_path)],
                    expected_outputs=[self.temp_path],
                )
            ],
            notes=[],
        )


class _FakeRunner:
    def run(self, command: list[str], cwd: Path | None = None):  # noqa: ARG002
        Path(command[-1]).write_bytes(b"optimized")
        return CommandResult(command=command, return_code=0, stdout="", stderr="")


def test_pipeline_reprocesses_when_state_would_skip_but_target_is_missing(tmp_path: Path):
    source_root = tmp_path / "watch"
    source_root.mkdir(parents=True)
    source = source_root / "movie.mkv"
    source.write_bytes(b"source")

    target = tmp_path / "optimized" / "movie.mp4"
    temp = tmp_path / "tmp" / ".movie.tmp.mp4"

    cfg = AppConfig.from_dict(
        {
            "watch": {"folders": [str(source_root)]},
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
            "validation": {"run_post_ffprobe": False},
        }
    )

    state = StateStore(cfg.paths.state_db)
    reporter = Reporter(cfg)
    processor = PipelineProcessor(config=cfg, state_store=state, reporter=reporter)
    processor.analyzer = _FakeAnalyzer(_media(source))
    processor.engine = _FakeEngine()
    processor.planner = _FakePlanner(source, target, temp)
    processor.runner = _FakeRunner()

    stat = source.stat()
    state.upsert_file_state(
        path=source,
        device=stat.st_dev,
        inode=stat.st_ino,
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        stream_fp="stream-fp",
        metadata_fp="meta-fp",
        status=JobStatus.SUCCESS,
        job_id="job-previous",
    )

    try:
        report = processor.process_path(source, source_root, dry_run_override=False)
    finally:
        state.close()

    assert report.status == "success"
    assert target.exists()
    assert not temp.exists()
