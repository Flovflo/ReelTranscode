from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True)
class WatchConfig:
    folders: list[Path] = field(default_factory=list)
    recursive: bool = True
    allowed_extensions: set[str] = field(
        default_factory=lambda: {".mkv", ".mp4", ".mov", ".m4v", ".ts", ".m2ts"}
    )
    stable_wait_seconds: int = 30
    stable_checks: int = 3
    poll_interval_seconds: int = 5


@dataclass(slots=True)
class RemuxPolicy:
    preferred_container: str = "mp4"
    faststart: bool = True
    keep_chapters: bool = True
    keep_attachments: bool = False


@dataclass(slots=True)
class AudioPolicy:
    preferred_codec_multichannel: str = "eac3"
    preferred_codec_stereo: str = "aac"
    fallback_codec: str = "ac3"
    max_channels: int = 8
    preferred_languages: list[str] = field(default_factory=lambda: ["eng", "fra", "jpn"])
    keep_original_compatible_tracks: bool = True


@dataclass(slots=True)
class SubtitlePolicy:
    mode: str = "convert_or_externalize"
    convert_text_to_mov_text: bool = True
    external_subtitle_format: str = "srt"
    preserve_forced_only_when_needed: bool = False
    ocr_image_subtitles: bool = False


@dataclass(slots=True)
class DolbyVisionPolicy:
    preserve_when_safe: bool = True
    safe_profiles: set[str] = field(default_factory=lambda: {"8.1"})
    remux_dv_from_mkv_to_mp4_is_safe: bool = False
    fragile_fallback: str = "preserve_hdr10"


@dataclass(slots=True)
class VideoPolicy:
    preferred_codec: str = "hevc"
    fallback_codec: str = "h264"
    force_cfr: bool = False
    keyframe_interval_seconds: int = 2
    hevc_tag: str = "hvc1"
    max_4k_fps: int = 60


@dataclass(slots=True)
class OutputPolicy:
    mode: str = "keep_original"
    output_root: Path = Path("./optimized")
    archive_root: Path = Path("./archive")
    overwrite: bool = False
    delete_original_after_success: bool = False


@dataclass(slots=True)
class ConcurrencyConfig:
    max_workers: int = 2
    io_nice_sleep_seconds: float = 0.0


@dataclass(slots=True)
class RetryConfig:
    max_attempts: int = 3
    backoff_initial_seconds: float = 3.0
    backoff_max_seconds: float = 60.0


@dataclass(slots=True)
class PathsConfig:
    state_db: Path = Path("./state/reeltranscode.db")
    reports_dir: Path = Path("./reports")
    csv_summary: Path = Path("./reports/summary.csv")
    temp_dir: Path = Path("./tmp")


@dataclass(slots=True)
class ToolingConfig:
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"


@dataclass(slots=True)
class ValidationConfig:
    verify_duration_tolerance_seconds: float = 2.0
    verify_stream_count_delta_max: int = 3
    run_post_ffprobe: bool = True


@dataclass(slots=True)
class LoggingConfig:
    level: str = "INFO"
    json_logs: bool = False


@dataclass(slots=True)
class AppConfig:
    watch: WatchConfig = field(default_factory=WatchConfig)
    remux: RemuxPolicy = field(default_factory=RemuxPolicy)
    audio: AudioPolicy = field(default_factory=AudioPolicy)
    subtitles: SubtitlePolicy = field(default_factory=SubtitlePolicy)
    dolby_vision: DolbyVisionPolicy = field(default_factory=DolbyVisionPolicy)
    video: VideoPolicy = field(default_factory=VideoPolicy)
    output: OutputPolicy = field(default_factory=OutputPolicy)
    concurrency: ConcurrencyConfig = field(default_factory=ConcurrencyConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    tooling: ToolingConfig = field(default_factory=ToolingConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    dry_run: bool = False

    @classmethod
    def load(cls, path: str | Path) -> "AppConfig":
        with Path(path).expanduser().open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AppConfig":
        def _path(v: str | None, default: Path) -> Path:
            if not v:
                return default
            return Path(v).expanduser()

        watch_raw = raw.get("watch", {})
        watch = WatchConfig(
            folders=[Path(p).expanduser() for p in watch_raw.get("folders", [])],
            recursive=bool(watch_raw.get("recursive", True)),
            allowed_extensions={e.lower() for e in watch_raw.get("allowed_extensions", WatchConfig().allowed_extensions)},
            stable_wait_seconds=int(watch_raw.get("stable_wait_seconds", 30)),
            stable_checks=int(watch_raw.get("stable_checks", 3)),
            poll_interval_seconds=int(watch_raw.get("poll_interval_seconds", 5)),
        )

        remux_raw = raw.get("remux", {})
        remux = RemuxPolicy(
            preferred_container=str(remux_raw.get("preferred_container", "mp4")),
            faststart=bool(remux_raw.get("faststart", True)),
            keep_chapters=bool(remux_raw.get("keep_chapters", True)),
            keep_attachments=bool(remux_raw.get("keep_attachments", False)),
        )

        audio_raw = raw.get("audio", {})
        audio = AudioPolicy(
            preferred_codec_multichannel=str(audio_raw.get("preferred_codec_multichannel", "eac3")),
            preferred_codec_stereo=str(audio_raw.get("preferred_codec_stereo", "aac")),
            fallback_codec=str(audio_raw.get("fallback_codec", "ac3")),
            max_channels=int(audio_raw.get("max_channels", 8)),
            preferred_languages=list(audio_raw.get("preferred_languages", ["eng", "fra", "jpn"])),
            keep_original_compatible_tracks=bool(audio_raw.get("keep_original_compatible_tracks", True)),
        )

        sub_raw = raw.get("subtitles", {})
        subtitles = SubtitlePolicy(
            mode=str(sub_raw.get("mode", "convert_or_externalize")),
            convert_text_to_mov_text=bool(sub_raw.get("convert_text_to_mov_text", True)),
            external_subtitle_format=str(sub_raw.get("external_subtitle_format", "srt")),
            preserve_forced_only_when_needed=bool(sub_raw.get("preserve_forced_only_when_needed", False)),
            ocr_image_subtitles=bool(sub_raw.get("ocr_image_subtitles", False)),
        )

        dv_raw = raw.get("dolby_vision", {})
        dv = DolbyVisionPolicy(
            preserve_when_safe=bool(dv_raw.get("preserve_when_safe", True)),
            safe_profiles={str(p) for p in dv_raw.get("safe_profiles", ["8.1"])},
            remux_dv_from_mkv_to_mp4_is_safe=bool(dv_raw.get("remux_dv_from_mkv_to_mp4_is_safe", False)),
            fragile_fallback=str(dv_raw.get("fragile_fallback", "preserve_hdr10")),
        )

        video_raw = raw.get("video", {})
        video = VideoPolicy(
            preferred_codec=str(video_raw.get("preferred_codec", "hevc")),
            fallback_codec=str(video_raw.get("fallback_codec", "h264")),
            force_cfr=bool(video_raw.get("force_cfr", False)),
            keyframe_interval_seconds=int(video_raw.get("keyframe_interval_seconds", 2)),
            hevc_tag=str(video_raw.get("hevc_tag", "hvc1")),
            max_4k_fps=int(video_raw.get("max_4k_fps", 60)),
        )

        output_raw = raw.get("output", {})
        output = OutputPolicy(
            mode=str(output_raw.get("mode", "keep_original")),
            output_root=_path(output_raw.get("output_root"), Path("./optimized")),
            archive_root=_path(output_raw.get("archive_root"), Path("./archive")),
            overwrite=bool(output_raw.get("overwrite", False)),
            delete_original_after_success=bool(output_raw.get("delete_original_after_success", False)),
        )

        conc_raw = raw.get("concurrency", {})
        concurrency = ConcurrencyConfig(
            max_workers=int(conc_raw.get("max_workers", 2)),
            io_nice_sleep_seconds=float(conc_raw.get("io_nice_sleep_seconds", 0.0)),
        )

        retry_raw = raw.get("retry", {})
        retry = RetryConfig(
            max_attempts=int(retry_raw.get("max_attempts", 3)),
            backoff_initial_seconds=float(retry_raw.get("backoff_initial_seconds", 3.0)),
            backoff_max_seconds=float(retry_raw.get("backoff_max_seconds", 60.0)),
        )

        paths_raw = raw.get("paths", {})
        paths = PathsConfig(
            state_db=_path(paths_raw.get("state_db"), Path("./state/reeltranscode.db")),
            reports_dir=_path(paths_raw.get("reports_dir"), Path("./reports")),
            csv_summary=_path(paths_raw.get("csv_summary"), Path("./reports/summary.csv")),
            temp_dir=_path(paths_raw.get("temp_dir"), Path("./tmp")),
        )

        tooling_raw = raw.get("tooling", {})
        tooling = ToolingConfig(
            ffmpeg_bin=str(tooling_raw.get("ffmpeg_bin", "ffmpeg")),
            ffprobe_bin=str(tooling_raw.get("ffprobe_bin", "ffprobe")),
        )

        validation_raw = raw.get("validation", {})
        validation = ValidationConfig(
            verify_duration_tolerance_seconds=float(validation_raw.get("verify_duration_tolerance_seconds", 2.0)),
            verify_stream_count_delta_max=int(validation_raw.get("verify_stream_count_delta_max", 3)),
            run_post_ffprobe=bool(validation_raw.get("run_post_ffprobe", True)),
        )

        logging_raw = raw.get("logging", {})
        logging = LoggingConfig(
            level=str(logging_raw.get("level", "INFO")),
            json_logs=bool(logging_raw.get("json_logs", False)),
        )

        return cls(
            watch=watch,
            remux=remux,
            audio=audio,
            subtitles=subtitles,
            dolby_vision=dv,
            video=video,
            output=output,
            concurrency=concurrency,
            retry=retry,
            paths=paths,
            tooling=tooling,
            validation=validation,
            logging=logging,
            dry_run=bool(raw.get("dry_run", False)),
        )
