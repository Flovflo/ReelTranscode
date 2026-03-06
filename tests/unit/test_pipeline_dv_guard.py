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
from reeltranscode.reporter import Reporter
from reeltranscode.state_store import StateStore


def _media(path: Path, format_name: str, *, has_dv: bool, codec_tag: str | None) -> MediaInfo:
    side_data = []
    if has_dv:
        side_data.append({"side_data_type": "DOVI configuration record", "dv_profile": "8.1"})
    streams = [
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
                "color_primaries": "bt2020",
                "color_transfer": "smpte2084",
                "color_space": "bt2020nc",
                "disposition": {"default": 1},
                "side_data_list": side_data,
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
    ]
    return MediaInfo(
        path=path,
        format_name=format_name,
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
        self.calls: list[Path] = []

    def analyze(self, path: Path):
        self.calls.append(path)
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
            steps=[CommandStep(name="main_ffmpeg", command=["mock-ffmpeg", str(self.temp_path)], expected_outputs=[self.temp_path])],
            notes=[],
        )


class _FakeRunner:
    def run(self, command: list[str], cwd: Path | None = None):  # noqa: ARG002
        Path(command[-1]).write_bytes(b"fake-mp4")
        return CommandResult(command=command, return_code=0, stdout="", stderr="")


class _FakeRunnerWithoutOutputs:
    def run(self, command: list[str], cwd: Path | None = None):  # noqa: ARG002
        return CommandResult(command=command, return_code=0, stdout="", stderr="")


def test_pipeline_skips_fragile_dv_case_when_strict_preservation_is_enabled(tmp_path: Path):
    source = tmp_path / "watch" / "movie.mkv"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    target = source.with_suffix(".mp4")
    temp = target.parent / ".movie.dv.tmp.mp4"

    cfg = AppConfig.from_dict(
        {
            "output": {"mode": "replace_original", "overwrite": True},
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

    source_media = _media(source, "matroska,webm", has_dv=True, codec_tag=None)
    output_media = _media(temp, "mov,mp4,m4a,3gp,3g2,mj2", has_dv=False, codec_tag="hvc1")
    analyzer = _FakeAnalyzer(source, temp, source_media, output_media)
    processor.analyzer = analyzer

    decision = Decision(
        strategy=Strategy.REMUX_ONLY,
        case_label=CaseLabel.F,
        reasons=["DV source must be preserved"],
        expected_container="mp4",
        expected_direct_play_safe=True,
        dv_fallback_applied=True,
        dv_fallback_reason="strict DV guard",
        preserve_hdr10=True,
    )
    compatibility = CompatibilityDetails(
        container_ok=False,
        video_ok=True,
        audio_ok=True,
        subtitle_ok=True,
        dv_present=True,
        dv_profile="8.1",
        hdr10_present=True,
        requires_container_change=True,
        requires_audio_fix=False,
        requires_subtitle_fix=False,
        requires_video_transcode=False,
        reasons=[],
    )
    processor.engine = _FakeEngine(decision, compatibility)
    processor.planner = _FakePlanner(source, target, temp)
    processor.runner = _FakeRunner()

    try:
        report = processor.process_path(source, source.parent, dry_run_override=False)
        snapshot = state.status_snapshot(limit=10)
    finally:
        state.close()

    assert report.status == "skipped"
    assert report.case_label == "DV_STRICT_SKIP"
    assert snapshot["summary"]["skipped"] == 1

    assert source.exists()
    assert not target.exists()
    assert not temp.exists()
    assert temp not in analyzer.calls


def test_pipeline_quarantines_stale_non_dv_target_when_strict_skip_hits(tmp_path: Path):
    source = tmp_path / "watch" / "movie.mkv"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    target = tmp_path / "optimized" / "movie.mp4"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old-non-dv-output")
    temp = target.parent / ".movie.dv.tmp.mp4"

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

    source_media = _media(source, "matroska,webm", has_dv=True, codec_tag=None)
    stale_output_media = _media(target, "mov,mp4,m4a,3gp,3g2,mj2", has_dv=False, codec_tag="hvc1")
    analyzer = _FakeAnalyzer(source, target, source_media, stale_output_media)
    processor.analyzer = analyzer

    decision = Decision(
        strategy=Strategy.REMUX_ONLY,
        case_label=CaseLabel.F,
        reasons=["DV source must be preserved"],
        expected_container="mp4",
        expected_direct_play_safe=True,
        dv_fallback_applied=True,
        dv_fallback_reason="strict DV guard",
        preserve_hdr10=True,
    )
    compatibility = CompatibilityDetails(
        container_ok=False,
        video_ok=True,
        audio_ok=True,
        subtitle_ok=True,
        dv_present=True,
        dv_profile="8.1",
        hdr10_present=True,
        requires_container_change=True,
        requires_audio_fix=False,
        requires_subtitle_fix=False,
        requires_video_transcode=False,
        reasons=[],
    )
    processor.engine = _FakeEngine(decision, compatibility)
    processor.planner = _FakePlanner(source, target, temp)
    processor.runner = _FakeRunner()

    try:
        report = processor.process_path(source, source.parent, dry_run_override=False)
    finally:
        state.close()

    assert report.status == "skipped"
    assert report.target_path == str(target)
    assert any("quarantined" in reason.lower() for reason in report.reasons)
    assert not target.exists()
    quarantine_dir = target.parent / "_quarantine"
    quarantined = list(quarantine_dir.glob("movie.dv-invalid-*.mp4"))
    assert len(quarantined) == 1


def test_pipeline_fails_when_step_does_not_create_expected_output(tmp_path: Path):
    source = tmp_path / "watch" / "movie.mkv"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    target = tmp_path / "optimized" / "movie.mp4"
    temp = target.parent / ".movie.dv.tmp.mp4"

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

    source_media = _media(source, "matroska,webm", has_dv=False, codec_tag=None)
    output_media = _media(temp, "mov,mp4,m4a,3gp,3g2,mj2", has_dv=False, codec_tag="hvc1")
    analyzer = _FakeAnalyzer(source, temp, source_media, output_media)
    processor.analyzer = analyzer

    decision = Decision(
        strategy=Strategy.REMUX_ONLY,
        case_label=CaseLabel.B,
        reasons=["remux"],
        expected_container="mp4",
        expected_direct_play_safe=True,
        preserve_hdr10=True,
    )
    compatibility = CompatibilityDetails(
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
    )
    processor.engine = _FakeEngine(decision, compatibility)
    processor.planner = _FakePlanner(source, target, temp)
    processor.runner = _FakeRunnerWithoutOutputs()

    try:
        report = processor.process_path(source, source.parent, dry_run_override=False)
    finally:
        state.close()

    assert report.status == "failed"
    assert report.error_class == "RuntimeError"
    assert "expected outputs" in (report.error_message or "")
    assert not target.exists()
