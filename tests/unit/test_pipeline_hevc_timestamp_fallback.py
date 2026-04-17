from __future__ import annotations

from pathlib import Path

from reeltranscode.analyzer import ProbeError
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
from reeltranscode.reporter import Reporter
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
                    "width": 1920,
                    "height": 1080,
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
                    "tags": {"language": "eng"},
                }
            ),
            StreamInfo.from_probe(
                {
                    "index": 2,
                    "codec_type": "subtitle",
                    "codec_name": "subrip",
                    "disposition": {"default": 0},
                    "tags": {"language": "fre", "title": "Forced"},
                }
            ),
        ],
        raw_probe={},
    )


def _validated_output_media(path: Path) -> MediaInfo:
    media = _media(path, "mov,mp4,m4a,3gp,3g2,mj2", "hvc1")
    subtitle = media.streams[2]
    subtitle.codec_name = "mov_text"
    return media


def _dovi_source_media(path: Path) -> MediaInfo:
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
                    "codec_tag_string": None,
                    "profile": "Main 10",
                    "pix_fmt": "yuv420p10le",
                    "width": 1920,
                    "height": 1080,
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
                    "tags": {"language": "eng", "title": "English"},
                }
            ),
            StreamInfo.from_probe(
                {
                    "index": 2,
                    "codec_type": "subtitle",
                    "codec_name": "subrip",
                    "disposition": {"default": 1, "forced": 1},
                    "tags": {"language": "fre", "title": "French Forced"},
                }
            ),
            StreamInfo.from_probe(
                {
                    "index": 3,
                    "codec_type": "subtitle",
                    "codec_name": "subrip",
                    "disposition": {"default": 0},
                    "tags": {"language": "eng", "title": "English Full"},
                }
            ),
        ],
        raw_probe={},
    )


def _dovi_output_media(path: Path, *, subtitle_count: int) -> MediaInfo:
    streams = [
        StreamInfo.from_probe(
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "hevc",
                "codec_tag_string": "dvh1",
                "profile": "Main 10",
                "pix_fmt": "yuv420p10le",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "24/1",
                "disposition": {"default": 1},
                "side_data_list": [{"side_data_type": "DOVI configuration record", "dv_profile": "8.1"}],
            }
        ),
        StreamInfo.from_probe(
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "eac3",
                "channels": 6,
                "disposition": {"default": 1},
                "tags": {"language": "eng", "title": "English"},
            }
        ),
        StreamInfo.from_probe(
            {
                "index": 2,
                "codec_type": "subtitle",
                "codec_name": "mov_text",
                "disposition": {"default": 1, "forced": 1},
                "tags": {"language": "fre", "title": "French Forced"},
            }
        ),
    ]
    if subtitle_count > 1:
        streams.append(
            StreamInfo.from_probe(
                {
                    "index": 3,
                    "codec_type": "subtitle",
                    "codec_name": "mov_text",
                    "disposition": {"default": 0},
                    "tags": {"language": "eng", "title": "English Full"},
                }
            )
        )

    return MediaInfo(
        path=path,
        format_name="mov,mp4,m4a,3gp,3g2,mj2",
        duration=120.0,
        bit_rate=20_000_000,
        size=1_000_000_000,
        streams=streams,
        raw_probe={},
    )


class _FakeAnalyzer:
    def __init__(self, source_path: Path, temp_path: Path, source_media: MediaInfo, output_media: MediaInfo):
        self.source_path = source_path
        self.temp_path = temp_path
        self.source_media = source_media
        self.output_media = output_media
        self.temp_analyze_calls = 0

    def analyze(self, path: Path):
        if path == self.source_path:
            return self.source_media, ["ffprobe", str(path)]
        if path == self.temp_path:
            self.temp_analyze_calls += 1
            if self.temp_analyze_calls == 1:
                raise ProbeError("ffprobe failed: No start code is found. Invalid data found when processing input")
            return self.output_media, ["ffprobe", str(path)]
        raise AssertionError(f"Unexpected analyze path: {path}")

    def stream_fingerprint(self, _media: MediaInfo) -> str:
        return "stream-fp"

    def metadata_fingerprint(self, _media: MediaInfo) -> str:
        return "meta-fp"


class _FakeEngine:
    def decide(self, _media: MediaInfo):
        return (
            Decision(
                strategy=Strategy.SUBTITLE_ONLY,
                case_label=CaseLabel.D,
                reasons=["Subtitle codec subrip incompatible with MP4"],
                expected_container="mp4",
                expected_direct_play_safe=True,
            ),
            CompatibilityDetails(
                container_ok=False,
                video_ok=True,
                audio_ok=True,
                subtitle_ok=False,
                dv_present=False,
                dv_profile=None,
                hdr10_present=False,
                requires_container_change=True,
                requires_audio_fix=False,
                requires_subtitle_fix=True,
                requires_video_transcode=False,
                reasons=[],
            ),
        )


class _FakePlanner:
    def __init__(self, source_path: Path, target_path: Path, temp_path: Path):
        self.source_path = source_path
        self.target_path = target_path
        self.temp_path = temp_path
        self.fallback_requested = False

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
                    cwd=self.target_path.parent,
                )
            ],
        )

    def build_hevc_mp4_stabilization_steps(self, _media, _decision, _compatibility, plan):
        self.fallback_requested = True
        stabilized_ts = plan.temp_path.with_name(f"{plan.temp_path.stem}.video-stabilized.ts")
        stabilized_video = plan.temp_path.with_name(f"{plan.temp_path.stem}.video-stabilized.mp4")
        return (
            [
                CommandStep(
                    name="stabilize_hevc_video_ts",
                    command=["mock-ffmpeg", str(stabilized_ts)],
                    expected_outputs=[stabilized_ts],
                    cwd=plan.temp_path.parent,
                ),
                CommandStep(
                    name="stabilize_hevc_video_mp4",
                    command=["mock-ffmpeg", str(stabilized_video)],
                    expected_outputs=[stabilized_video],
                    cwd=plan.temp_path.parent,
                ),
                CommandStep(
                    name="rebuild_mp4_with_stabilized_video",
                    command=["mock-ffmpeg", str(plan.temp_path)],
                    expected_outputs=[plan.temp_path],
                    cwd=plan.temp_path.parent,
                ),
            ],
            [stabilized_ts, stabilized_video],
        )


class _RecordingRunner:
    def __init__(self):
        self.commands: list[list[str]] = []

    def run(self, command: list[str], cwd: Path | None = None):  # noqa: ARG002
        self.commands.append(command)
        Path(command[-1]).write_bytes(b"artifact")
        return CommandResult(command=command, return_code=0, stdout="", stderr="")


class _DoviImportFailureRunner(_RecordingRunner):
    def run(self, command: list[str], cwd: Path | None = None):  # noqa: ARG002
        self.commands.append(command)
        if command[0] == "mock-dovi":
            return CommandResult(
                command=command,
                return_code=0,
                stdout="",
                stderr="Error importing subtitle0.srt: subtitle track import failed",
            )
        Path(command[-1]).write_bytes(b"artifact")
        return CommandResult(command=command, return_code=0, stdout="", stderr="")


class _FakeDoviAnalyzer:
    def __init__(
        self,
        source_path: Path,
        temp_path: Path,
        repaired_path: Path,
        source_media: MediaInfo,
        initial_output_media: MediaInfo,
        repaired_output_media: MediaInfo,
    ):
        self.source_path = source_path
        self.temp_path = temp_path
        self.repaired_path = repaired_path
        self.source_media = source_media
        self.initial_output_media = initial_output_media
        self.repaired_output_media = repaired_output_media
        self.repair_analyze_calls = 0

    def analyze(self, path: Path):
        if path == self.source_path:
            return self.source_media, ["ffprobe", str(path)]
        if path == self.temp_path:
            return self.initial_output_media, ["ffprobe", str(path)]
        if path == self.repaired_path:
            self.repair_analyze_calls += 1
            return self.repaired_output_media, ["ffprobe", str(path)]
        raise AssertionError(f"Unexpected analyze path: {path}")

    def stream_fingerprint(self, _media: MediaInfo) -> str:
        return "stream-fp"

    def metadata_fingerprint(self, _media: MediaInfo) -> str:
        return "meta-fp"


class _FakeDoviEngine:
    def decide(self, _media: MediaInfo):
        return (
            Decision(
                strategy=Strategy.REMUX_ONLY,
                case_label=CaseLabel.F,
                reasons=["Using DoViMuxer DV-safe remux path for Apple-compatible MP4"],
                expected_container="mp4",
                expected_direct_play_safe=True,
                use_dovi_muxer=True,
            ),
            CompatibilityDetails(
                container_ok=False,
                video_ok=True,
                audio_ok=True,
                subtitle_ok=False,
                dv_present=True,
                dv_profile="8.1",
                hdr10_present=True,
                requires_container_change=True,
                requires_audio_fix=False,
                requires_subtitle_fix=True,
                requires_video_transcode=False,
                reasons=[],
            ),
        )


class _FakeDoviPlanner:
    def __init__(self, source_path: Path, target_path: Path, temp_path: Path, repaired_path: Path, workspace_dir: Path):
        self.source_path = source_path
        self.target_path = target_path
        self.temp_path = temp_path
        self.repaired_path = repaired_path
        self.workspace_dir = workspace_dir
        self.fallback_requested = False
        self.import_failure_fallback_requested = False

    def preview_target_path(self, _source: Path, _source_root: Path | None) -> Path:
        return self.target_path

    def build(self, _media, decision, _compatibility, _source_root):
        return ExecutionPlan(
            source_path=self.source_path,
            target_path=self.target_path,
            temp_path=self.temp_path,
            workspace_dir=self.workspace_dir,
            strategy=decision.strategy,
            case_label=decision.case_label,
            steps=[
                CommandStep(
                    name="dovi_muxer",
                    command=["mock-dovi", str(self.temp_path)],
                    expected_outputs=[self.temp_path],
                    cwd=self.workspace_dir,
                )
            ],
        )

    def build_hevc_mp4_stabilization_steps(self, _media, _decision, _compatibility, _plan):
        return [], []

    def build_dovi_subtitle_repair_steps(self, _media, _decision, _plan):
        self.fallback_requested = True
        return (
            [
                CommandStep(
                    name="dovi_subtitle_repair",
                    command=["mock-ffmpeg", str(self.repaired_path)],
                    expected_outputs=[self.repaired_path],
                    cwd=self.workspace_dir,
                )
            ],
            [self.temp_path],
            self.repaired_path,
            [],
        )

    def build_dovi_subtitle_import_recovery_steps(self, _media, _decision, _plan):
        self.import_failure_fallback_requested = True
        subtitleless = self.temp_path.with_name(f"{self.temp_path.stem}.subtitleless-base{self.temp_path.suffix}")
        return (
            [
                CommandStep(
                    name="dovi_muxer_subtitleless_base",
                    command=["mock-dovi-recovery", str(subtitleless)],
                    expected_outputs=[subtitleless],
                    cwd=self.workspace_dir,
                ),
                CommandStep(
                    name="dovi_subtitle_repair",
                    command=["mock-ffmpeg", str(self.repaired_path)],
                    expected_outputs=[self.repaired_path],
                    cwd=self.workspace_dir,
                ),
            ],
            [subtitleless],
            self.repaired_path,
            [],
        )


class _CleanupAwareAnalyzer:
    def __init__(
        self,
        source_path: Path,
        temp_path: Path,
        cleaned_path: Path,
        source_media: MediaInfo,
        temp_output_media: MediaInfo,
        cleaned_output_media: MediaInfo,
    ):
        self.source_path = source_path
        self.temp_path = temp_path
        self.cleaned_path = cleaned_path
        self.source_media = source_media
        self.temp_output_media = temp_output_media
        self.cleaned_output_media = cleaned_output_media

    def analyze(self, path: Path):
        if path == self.source_path:
            return self.source_media, ["ffprobe", str(path)]
        if path == self.temp_path:
            return self.temp_output_media, ["ffprobe", str(path)]
        if path == self.cleaned_path:
            return self.cleaned_output_media, ["ffprobe", str(path)]
        raise AssertionError(f"Unexpected analyze path: {path}")

    def stream_fingerprint(self, _media: MediaInfo) -> str:
        return "stream-fp"

    def metadata_fingerprint(self, _media: MediaInfo) -> str:
        return "meta-fp"


class _FakeRemuxEngine:
    def decide(self, _media: MediaInfo):
        return (
            Decision(
                strategy=Strategy.REMUX_ONLY,
                case_label=CaseLabel.B,
                reasons=["Container conversion required; all streams compatible"],
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


class _FakeCleanupPlanner(_FakePlanner):
    def __init__(self, source_path: Path, target_path: Path, temp_path: Path, cleaned_path: Path):
        super().__init__(source_path, target_path, temp_path)
        self.cleaned_path = cleaned_path
        self.cleanup_requested = False

    def build_mp4_cleanup_steps(self, _media, _decision, plan):
        self.cleanup_requested = True
        return (
            [
                CommandStep(
                    name="final_mp4_cleanup",
                    command=["mock-ffmpeg", str(self.cleaned_path)],
                    expected_outputs=[self.cleaned_path],
                    cwd=plan.temp_path.parent,
                )
            ],
            [plan.temp_path],
            self.cleaned_path,
            ["Normalized final MP4 container via cleanup remux"],
        )


def test_pipeline_rebuilds_unreadable_hevc_mp4_via_stabilized_video(tmp_path: Path):
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
    reporter = Reporter(cfg)
    processor = PipelineProcessor(config=cfg, state_store=state, reporter=reporter)

    source_media = _media(source, "matroska,webm", codec_tag=None)
    output_media = _validated_output_media(temp)
    analyzer = _FakeAnalyzer(source, temp, source_media, output_media)
    planner = _FakePlanner(source, target, temp)
    runner = _RecordingRunner()
    processor.analyzer = analyzer
    processor.engine = _FakeEngine()
    processor.planner = planner
    processor.runner = runner

    try:
        report = processor.process_path(source, source.parent, dry_run_override=False)
    finally:
        state.close()

    assert report.status == "success"
    assert target.exists()
    assert planner.fallback_requested is True
    assert analyzer.temp_analyze_calls == 2
    assert any(command[-1].endswith(".video-stabilized.ts") for command in runner.commands)
    assert any(command[-1].endswith(".video-stabilized.mp4") for command in runner.commands)


def test_pipeline_runs_final_mp4_cleanup_for_plain_remux_outputs(tmp_path: Path):
    source = tmp_path / "watch" / "movie.mkv"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    target = tmp_path / "optimized" / "movie.mp4"
    target.parent.mkdir(parents=True)
    temp = target.parent / ".movie.tmp.mp4"
    cleaned = target.parent / ".movie.tmp.apple-clean.mp4"

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
    reporter = Reporter(cfg)
    processor = PipelineProcessor(config=cfg, state_store=state, reporter=reporter)

    source_media = _media(source, "matroska,webm", codec_tag=None)
    temp_output_media = _validated_output_media(temp)
    cleaned_output_media = _validated_output_media(cleaned)
    analyzer = _CleanupAwareAnalyzer(source, temp, cleaned, source_media, temp_output_media, cleaned_output_media)
    planner = _FakeCleanupPlanner(source, target, temp, cleaned)
    runner = _RecordingRunner()
    processor.analyzer = analyzer
    processor.engine = _FakeRemuxEngine()
    processor.planner = planner
    processor.runner = runner

    try:
        report = processor.process_path(source, source.parent, dry_run_override=False)
    finally:
        state.close()

    assert report.status == "success"
    assert target.exists()
    assert planner.cleanup_requested is True
    assert any(command[-1].endswith(".apple-clean.mp4") for command in runner.commands)
    assert not temp.exists()
    assert not cleaned.exists()


def test_pipeline_repairs_dovi_subtitles_after_validation_mismatch(tmp_path: Path):
    source = tmp_path / "watch" / "movie-dv.mkv"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    target = tmp_path / "optimized" / "movie-dv.mp4"
    target.parent.mkdir(parents=True)
    workspace = tmp_path / "tmp" / "movie-dv.dovi"
    workspace.mkdir(parents=True)
    temp = workspace / "movie-dv.tmp.mp4"
    repaired = workspace / "movie-dv.tmp.subtitle-repaired.mp4"

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
    reporter = Reporter(cfg)
    processor = PipelineProcessor(config=cfg, state_store=state, reporter=reporter)

    source_media = _dovi_source_media(source)
    initial_output_media = _dovi_output_media(temp, subtitle_count=1)
    repaired_output_media = _dovi_output_media(repaired, subtitle_count=2)
    analyzer = _FakeDoviAnalyzer(source, temp, repaired, source_media, initial_output_media, repaired_output_media)
    planner = _FakeDoviPlanner(source, target, temp, repaired, workspace)
    runner = _RecordingRunner()
    processor.analyzer = analyzer
    processor.engine = _FakeDoviEngine()
    processor.planner = planner
    processor.runner = runner

    try:
        report = processor.process_path(source, source.parent, dry_run_override=False)
    finally:
        state.close()

    assert report.status == "success"
    assert target.exists()
    assert planner.fallback_requested is True
    assert analyzer.repair_analyze_calls == 1
    assert any(command[-1].endswith(".subtitle-repaired.mp4") for command in runner.commands)


def test_pipeline_recovers_when_dovi_muxer_cannot_import_text_subtitles(tmp_path: Path):
    source = tmp_path / "watch" / "movie-dv.mkv"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    target = tmp_path / "optimized" / "movie-dv.mp4"
    target.parent.mkdir(parents=True)
    workspace = tmp_path / "tmp" / "movie-dv.dovi"
    workspace.mkdir(parents=True)
    temp = workspace / "movie-dv.tmp.mp4"
    repaired = workspace / "movie-dv.tmp.subtitle-repaired.mp4"

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
    reporter = Reporter(cfg)
    processor = PipelineProcessor(config=cfg, state_store=state, reporter=reporter)

    source_media = _dovi_source_media(source)
    repaired_output_media = _dovi_output_media(repaired, subtitle_count=2)
    analyzer = _FakeDoviAnalyzer(source, temp, repaired, source_media, repaired_output_media, repaired_output_media)
    planner = _FakeDoviPlanner(source, target, temp, repaired, workspace)
    runner = _DoviImportFailureRunner()
    processor.analyzer = analyzer
    processor.engine = _FakeDoviEngine()
    processor.planner = planner
    processor.runner = runner

    try:
        report = processor.process_path(source, source.parent, dry_run_override=False)
    finally:
        state.close()

    assert report.status == "success"
    assert target.exists()
    assert planner.import_failure_fallback_requested is True
    assert any(command[0] == "mock-dovi-recovery" for command in runner.commands)
    assert any(command[-1].endswith(".subtitle-repaired.mp4") for command in runner.commands)
