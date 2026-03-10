from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class Strategy(str, Enum):
    NO_OP = "no_op"
    REMUX_ONLY = "remux_only"
    SUBTITLE_ONLY = "subtitle_only"
    AUDIO_ONLY = "audio_only"
    VIDEO_ONLY = "video_only"
    FULL_PIPELINE = "full_pipeline"


class CaseLabel(str, Enum):
    A = "A_ALREADY_COMPATIBLE"
    B = "B_CONTAINER_ONLY"
    C = "C_AUDIO_ONLY"
    D = "D_SUBTITLE_ONLY"
    E = "E_VIDEO_INCOMPATIBLE"
    F = "F_DOLBY_VISION_FRAGILE"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(slots=True)
class StreamDisposition:
    default: bool = False
    forced: bool = False
    hearing_impaired: bool = False
    captions: bool = False

    @classmethod
    def from_probe(cls, raw: dict[str, Any] | None) -> "StreamDisposition":
        raw = raw or {}
        return cls(
            default=bool(raw.get("default", 0)),
            forced=bool(raw.get("forced", 0)),
            hearing_impaired=bool(raw.get("hearing_impaired", 0)),
            captions=bool(raw.get("captions", 0)),
        )


@dataclass(slots=True)
class StreamInfo:
    index: int
    codec_type: str
    codec_name: str | None = None
    codec_tag_string: str | None = None
    profile: str | None = None
    level: int | None = None
    pix_fmt: str | None = None
    width: int | None = None
    height: int | None = None
    avg_frame_rate: str | None = None
    r_frame_rate: str | None = None
    bit_rate: int | None = None
    channels: int | None = None
    channel_layout: str | None = None
    language: str | None = None
    title: str | None = None
    duration: float | None = None
    start_time: float | None = None
    disposition: StreamDisposition = field(default_factory=StreamDisposition)
    color_primaries: str | None = None
    color_transfer: str | None = None
    color_space: str | None = None
    field_order: str | None = None
    side_data_list: list[dict[str, Any]] = field(default_factory=list)
    dv_profile: str | None = None
    dv_level: str | None = None

    @classmethod
    def from_probe(cls, raw: dict[str, Any]) -> "StreamInfo":
        tags = raw.get("tags", {}) or {}
        bit_rate = raw.get("bit_rate")
        parsed_bit_rate = int(bit_rate) if isinstance(bit_rate, str) and bit_rate.isdigit() else None
        if isinstance(bit_rate, int):
            parsed_bit_rate = bit_rate
        parsed_duration = _probe_duration_seconds(raw.get("duration"))
        if parsed_duration is None:
            parsed_duration = _duration_tag_seconds(tags.get("DURATION"))
        return cls(
            index=int(raw["index"]),
            codec_type=raw.get("codec_type", "unknown"),
            codec_name=raw.get("codec_name"),
            codec_tag_string=raw.get("codec_tag_string"),
            profile=raw.get("profile"),
            level=raw.get("level"),
            pix_fmt=raw.get("pix_fmt"),
            width=raw.get("width"),
            height=raw.get("height"),
            avg_frame_rate=raw.get("avg_frame_rate"),
            r_frame_rate=raw.get("r_frame_rate"),
            bit_rate=parsed_bit_rate,
            channels=raw.get("channels"),
            channel_layout=raw.get("channel_layout"),
            language=tags.get("language"),
            title=tags.get("title"),
            duration=parsed_duration,
            start_time=_probe_duration_seconds(raw.get("start_time")),
            disposition=StreamDisposition.from_probe(raw.get("disposition")),
            color_primaries=raw.get("color_primaries"),
            color_transfer=raw.get("color_transfer"),
            color_space=raw.get("color_space"),
            field_order=raw.get("field_order"),
            side_data_list=list(raw.get("side_data_list", []) or []),
            dv_profile=_dv_field(raw, "dv_profile", "dovi_profile", "dolby_vision_profile"),
            dv_level=_dv_field(raw, "dv_level", "dovi_level", "dolby_vision_level"),
        )

    @property
    def is_video(self) -> bool:
        return self.codec_type == "video"

    @property
    def is_audio(self) -> bool:
        return self.codec_type == "audio"

    @property
    def is_subtitle(self) -> bool:
        return self.codec_type == "subtitle"

    @property
    def is_text_subtitle(self) -> bool:
        return self.codec_name in {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text"}

    @property
    def is_image_subtitle(self) -> bool:
        return self.codec_name in {"hdmv_pgs_subtitle", "dvd_subtitle", "xsub"}


@dataclass(slots=True)
class MediaInfo:
    path: Path
    format_name: str
    duration: float | None
    bit_rate: int | None
    size: int | None
    streams: list[StreamInfo]
    raw_probe: dict[str, Any]
    raw_mediainfo: dict[str, Any] = field(default_factory=dict)

    @property
    def container_names(self) -> set[str]:
        return {item.strip() for item in self.format_name.split(",") if item.strip()}

    @property
    def video_streams(self) -> list[StreamInfo]:
        return [s for s in self.streams if s.is_video]

    @property
    def audio_streams(self) -> list[StreamInfo]:
        return [s for s in self.streams if s.is_audio]

    @property
    def subtitle_streams(self) -> list[StreamInfo]:
        return [s for s in self.streams if s.is_subtitle]

    @property
    def primary_video(self) -> StreamInfo | None:
        for stream in self.video_streams:
            if stream.disposition.default:
                return stream
        return self.video_streams[0] if self.video_streams else None


@dataclass(slots=True)
class CompatibilityDetails:
    container_ok: bool
    video_ok: bool
    audio_ok: bool
    subtitle_ok: bool
    dv_present: bool
    dv_profile: str | None
    hdr10_present: bool
    requires_container_change: bool
    requires_audio_fix: bool
    requires_subtitle_fix: bool
    requires_video_transcode: bool
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Decision:
    strategy: Strategy
    case_label: CaseLabel
    reasons: list[str]
    expected_container: str
    expected_direct_play_safe: bool
    dv_fallback_applied: bool = False
    dv_fallback_reason: str | None = None
    force_sdr: bool = False
    preserve_hdr10: bool = True
    use_dovi_muxer: bool = False


@dataclass(slots=True)
class CommandStep:
    name: str
    command: list[str]
    expected_outputs: list[Path]
    cwd: Path | None = None


@dataclass(slots=True)
class OcrSubtitleTask:
    source_subtitle_index: int
    source_codec: str | None
    language: str | None
    title: str | None
    default: bool
    forced: bool
    hearing_impaired: bool
    captions: bool
    sup_path: Path
    output_path: Path


@dataclass(slots=True)
class ExecutionPlan:
    source_path: Path
    target_path: Path | None
    temp_path: Path | None
    workspace_dir: Path | None
    strategy: Strategy
    case_label: CaseLabel
    steps: list[CommandStep]
    ocr_subtitle_tasks: list[OcrSubtitleTask] = field(default_factory=list)
    external_subtitle_outputs: list[Path] = field(default_factory=list)
    dropped_subtitle_streams: list[int] = field(default_factory=list)
    cleanup_paths: list[Path] = field(default_factory=list)
    cleanup_dirs: list[Path] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ValidationResult:
    ok: bool
    reasons: list[str]
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DolbyVisionEvidence:
    present: bool
    profile: str | None = None
    source: str | None = None
    brand_hint: bool = False


@dataclass(slots=True)
class SubtitleTrackState:
    codec_name: str | None
    language: str | None
    title: str | None
    default: bool
    forced: bool
    hearing_impaired: bool
    captions: bool


@dataclass(slots=True)
class JobReport:
    job_id: str
    source_path: str
    target_path: str | None
    strategy: str
    case_label: str
    status: str
    started_at: str
    finished_at: str
    duration_seconds: float
    reasons: list[str]
    ffprobe_command: list[str]
    ffmpeg_commands: list[list[str]]
    expected_direct_play_safe: bool
    validations: list[str]
    stream_fingerprint: str
    metadata_fingerprint: str
    dv_fallback_applied: bool
    dv_fallback_reason: str | None
    error_class: str | None = None
    error_message: str | None = None


def _dv_field(raw: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = raw.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _probe_duration_seconds(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _duration_tag_seconds(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) != 3:
        return None
    try:
        hours = float(parts[0])
        minutes = float(parts[1])
        seconds = float(parts[2])
    except ValueError:
        return None
    return (hours * 3600.0) + (minutes * 60.0) + seconds
