from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml

from reeltranscode.utils import (
    is_generated_metadata_path,
    is_runtime_temp_path,
    is_transient_media_path,
    path_contains,
    paths_overlap,
)


@dataclass(slots=True)
class WatchConfig:
    folders: list[Path] = field(default_factory=list)
    priority_folders: list[Path] = field(default_factory=list)
    recursive: bool = True
    use_filesystem_events: bool = False
    allowed_extensions: set[str] = field(
        default_factory=lambda: {".mkv", ".mp4", ".mov", ".m4v", ".ts", ".m2ts", ".avi"}
    )
    priority_extensions: set[str] = field(
        default_factory=lambda: {".mkv", ".mov", ".m4v", ".ts", ".m2ts", ".avi"}
    )
    stable_wait_seconds: int = 30
    stable_checks: int = 3
    poll_interval_seconds: int = 5
    rescan_interval_seconds: int = 300


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
    ensure_aac_fallback_stereo_when_missing: bool = True


@dataclass(slots=True)
class SubtitlePolicy:
    mode: str = "convert_or_externalize"
    convert_text_to_mov_text: bool = True
    external_subtitle_format: str = "srt"
    preserve_forced_only_when_needed: bool = False
    ocr_image_subtitles: bool = False
    drop_incompatible_image_subtitles: bool = True


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
    hardware_encoder: str = "auto"
    encoder_threads: int = 0
    videotoolbox_bitrate_multiplier: float = 1.0
    videotoolbox_min_bitrate_kbps: int = 2500
    videotoolbox_max_bitrate_kbps: int = 80000
    keyframe_interval_seconds: int = 2
    hevc_tag: str = "hvc1"
    max_4k_fps: int = 60


@dataclass(slots=True)
class OutputPolicy:
    mode: str = "keep_original"
    output_root: Path = Path("./optimized")
    output_root_overrides: dict[Path, Path] = field(default_factory=dict)
    archive_root: Path = Path("./archive")
    overwrite: bool = False
    delete_original_after_success: bool = False
    delete_original_after_success_roots: list[Path] = field(default_factory=list)


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
    temp_dir_strategy: str = "source_first"
    temp_dir_overrides: dict[Path, Path] = field(default_factory=dict)


@dataclass(slots=True)
class ToolingConfig:
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    dovi_muxer_bin: str | None = None
    mp4box_bin: str | None = None
    mediainfo_bin: str | None = None
    mp4muxer_bin: str | None = None


@dataclass(slots=True)
class ValidationConfig:
    verify_duration_tolerance_seconds: float = 2.0
    verify_stream_count_delta_max: int = 3
    run_post_ffprobe: bool = True
    require_dv_preservation: bool = True


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
        def _optional_str(value: Any) -> str | None:
            if value is None:
                return None
            text = str(value).strip()
            return text or None

        def _path(v: str | None, default: Path) -> Path:
            if not v:
                return default
            return Path(v).expanduser()

        watch_raw = raw.get("watch", {})
        watch = WatchConfig(
            folders=[Path(p).expanduser() for p in watch_raw.get("folders", [])],
            priority_folders=[Path(p).expanduser() for p in watch_raw.get("priority_folders", [])],
            recursive=bool(watch_raw.get("recursive", True)),
            use_filesystem_events=bool(watch_raw.get("use_filesystem_events", False)),
            allowed_extensions={e.lower() for e in watch_raw.get("allowed_extensions", WatchConfig().allowed_extensions)},
            priority_extensions={
                e.lower() for e in watch_raw.get("priority_extensions", WatchConfig().priority_extensions)
            },
            stable_wait_seconds=int(watch_raw.get("stable_wait_seconds", 30)),
            stable_checks=int(watch_raw.get("stable_checks", 3)),
            poll_interval_seconds=int(watch_raw.get("poll_interval_seconds", 5)),
            rescan_interval_seconds=int(watch_raw.get("rescan_interval_seconds", 300)),
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
            ensure_aac_fallback_stereo_when_missing=bool(
                audio_raw.get("ensure_aac_fallback_stereo_when_missing", True)
            ),
        )

        sub_raw = raw.get("subtitles", {})
        subtitles = SubtitlePolicy(
            mode=str(sub_raw.get("mode", "convert_or_externalize")),
            convert_text_to_mov_text=bool(sub_raw.get("convert_text_to_mov_text", True)),
            external_subtitle_format=str(sub_raw.get("external_subtitle_format", "srt")),
            preserve_forced_only_when_needed=bool(sub_raw.get("preserve_forced_only_when_needed", False)),
            ocr_image_subtitles=bool(sub_raw.get("ocr_image_subtitles", False)),
            drop_incompatible_image_subtitles=bool(sub_raw.get("drop_incompatible_image_subtitles", True)),
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
            hardware_encoder=str(video_raw.get("hardware_encoder", "auto")).strip().lower(),
            encoder_threads=int(video_raw.get("encoder_threads", 0)),
            videotoolbox_bitrate_multiplier=float(video_raw.get("videotoolbox_bitrate_multiplier", 1.0)),
            videotoolbox_min_bitrate_kbps=int(video_raw.get("videotoolbox_min_bitrate_kbps", 2500)),
            videotoolbox_max_bitrate_kbps=int(video_raw.get("videotoolbox_max_bitrate_kbps", 80000)),
            keyframe_interval_seconds=int(video_raw.get("keyframe_interval_seconds", 2)),
            hevc_tag=str(video_raw.get("hevc_tag", "hvc1")),
            max_4k_fps=int(video_raw.get("max_4k_fps", 60)),
        )

        output_raw = raw.get("output", {})
        output = OutputPolicy(
            mode=str(output_raw.get("mode", "keep_original")),
            output_root=_path(output_raw.get("output_root"), Path("./optimized")),
            output_root_overrides={
                Path(source_root).expanduser(): Path(dest_root).expanduser()
                for source_root, dest_root in dict(output_raw.get("output_root_overrides", {})).items()
            },
            archive_root=_path(output_raw.get("archive_root"), Path("./archive")),
            overwrite=bool(output_raw.get("overwrite", False)),
            delete_original_after_success=bool(output_raw.get("delete_original_after_success", False)),
            delete_original_after_success_roots=[
                Path(path).expanduser() for path in output_raw.get("delete_original_after_success_roots", [])
            ],
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
            temp_dir_strategy=str(paths_raw.get("temp_dir_strategy", "source_first")),
            temp_dir_overrides={
                Path(source_root).expanduser(): Path(temp_root).expanduser()
                for source_root, temp_root in dict(paths_raw.get("temp_dir_overrides", {})).items()
            },
        )

        tooling_raw = raw.get("tooling", {})
        tooling = ToolingConfig(
            ffmpeg_bin=str(tooling_raw.get("ffmpeg_bin", "ffmpeg")),
            ffprobe_bin=str(tooling_raw.get("ffprobe_bin", "ffprobe")),
            dovi_muxer_bin=_optional_str(tooling_raw.get("dovi_muxer_bin")),
            mp4box_bin=_optional_str(tooling_raw.get("mp4box_bin")),
            mediainfo_bin=_optional_str(tooling_raw.get("mediainfo_bin")),
            mp4muxer_bin=_optional_str(tooling_raw.get("mp4muxer_bin")),
        )

        validation_raw = raw.get("validation", {})
        validation = ValidationConfig(
            verify_duration_tolerance_seconds=float(validation_raw.get("verify_duration_tolerance_seconds", 2.0)),
            verify_stream_count_delta_max=int(validation_raw.get("verify_stream_count_delta_max", 3)),
            run_post_ffprobe=bool(validation_raw.get("run_post_ffprobe", True)),
            require_dv_preservation=bool(validation_raw.get("require_dv_preservation", True)),
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

    def to_dict(self) -> dict[str, Any]:
        """Serialize config with normalized JSON/YAML-friendly values."""
        return _serialize_value(asdict(self))

    def processing_policy_fingerprint(self) -> str:
        """Fingerprint settings and tool identities that can change processing outcomes."""
        normalized = self.to_dict()
        payload = {
            # Bump when planner/validator semantics change so unchanged failed files
            # are eligible for a safe retry on the next watcher seed scan.
            "revision": 3,
            "remux": normalized["remux"],
            "audio": normalized["audio"],
            "subtitles": normalized["subtitles"],
            "dolby_vision": normalized["dolby_vision"],
            "video": normalized["video"],
            "output": normalized["output"],
            "validation": normalized["validation"],
            "tooling": normalized["tooling"],
            "tool_identities": {
                name: _tool_identity(value)
                for name, value in normalized["tooling"].items()
                if value
            },
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "policy:" + hashlib.sha256(encoded).hexdigest()[:16]

    def watch_roots(self) -> list[Path]:
        return [path.expanduser().resolve() for path in self.watch.folders]

    def priority_watch_roots(self) -> list[Path]:
        return [path.expanduser().resolve() for path in self.watch.priority_folders]

    def resolved_watch_root_for(self, path: Path, source_root: Path | None = None) -> Path | None:
        if source_root is not None:
            return source_root.expanduser().resolve()

        candidate = path.expanduser().resolve()
        best = None
        for root in [*self.watch_roots(), *self.priority_watch_roots()]:
            if path_contains(root, candidate):
                if best is None or len(root.parts) > len(best.parts):
                    best = root
        return best

    def output_root_for(self, source: Path, source_root: Path | None = None) -> Path:
        resolved_source_root = self.resolved_watch_root_for(source, source_root)
        resolved_overrides = {
            watch_root.expanduser().resolve(): target_root.expanduser().resolve()
            for watch_root, target_root in self.output.output_root_overrides.items()
        }
        if resolved_source_root is not None and resolved_source_root in resolved_overrides:
            return resolved_overrides[resolved_source_root]
        return self.output.output_root.expanduser().resolve()

    def delete_original_after_success_for(self, source: Path, source_root: Path | None = None) -> bool:
        if self.output.delete_original_after_success:
            return True

        resolved_source_root = self.resolved_watch_root_for(source, source_root)
        if resolved_source_root is None:
            return False

        delete_roots = {path.expanduser().resolve() for path in self.output.delete_original_after_success_roots}
        return resolved_source_root in delete_roots

    def managed_paths(self) -> dict[str, Path]:
        managed = {
            "output.output_root": self.output.output_root.expanduser().resolve(),
            "output.archive_root": self.output.archive_root.expanduser().resolve(),
            "paths.temp_dir": self.paths.temp_dir.expanduser().resolve(),
        }
        for source_root, temp_root in self.paths.temp_dir_overrides.items():
            managed[f"paths.temp_dir_overrides[{source_root.expanduser().resolve()}]"] = temp_root.expanduser().resolve()
        for source_root, target_root in self.output.output_root_overrides.items():
            managed[f"output.output_root_overrides[{source_root.expanduser().resolve()}]"] = (
                target_root.expanduser().resolve()
            )
        return managed

    def temp_dir_for(self, source: Path, source_root: Path | None = None) -> Path:
        resolved_source_root = self.resolved_watch_root_for(source, source_root)
        resolved_overrides = {
            watch_root.expanduser().resolve(): temp_root.expanduser().resolve()
            for watch_root, temp_root in self.paths.temp_dir_overrides.items()
        }
        if resolved_source_root is not None and resolved_source_root in resolved_overrides:
            return resolved_overrides[resolved_source_root]
        return self.paths.temp_dir.expanduser().resolve()

    def is_excluded_from_watch(self, path: Path, *, allow_managed_paths: bool = False) -> bool:
        candidate = path.expanduser().resolve()
        if (
            is_runtime_temp_path(candidate)
            or is_transient_media_path(candidate)
            or is_generated_metadata_path(candidate)
        ):
            return True
        if allow_managed_paths:
            return False
        for protected_path in self.managed_paths().values():
            if path_contains(protected_path, candidate):
                return True
        return False

    def validate(self) -> list[dict[str, str]]:
        errors: list[dict[str, str]] = []

        def _error(field: str, message: str) -> None:
            errors.append({"field": field, "message": message})

        if self.concurrency.max_workers < 1:
            _error("concurrency.max_workers", "must be >= 1")
        if self.retry.max_attempts < 1:
            _error("retry.max_attempts", "must be >= 1")
        if self.watch.stable_checks < 1:
            _error("watch.stable_checks", "must be >= 1")
        if self.watch.poll_interval_seconds < 1:
            _error("watch.poll_interval_seconds", "must be >= 1")
        if self.watch.stable_wait_seconds < 1:
            _error("watch.stable_wait_seconds", "must be >= 1")
        if self.watch.rescan_interval_seconds < 0:
            _error("watch.rescan_interval_seconds", "must be >= 0")

        allowed_modes = {"keep_original", "archive_original", "replace_original"}
        if self.output.mode not in allowed_modes:
            _error("output.mode", f"must be one of {sorted(allowed_modes)}")

        allowed_containers = {"mp4", "mov", "m4v", "mkv"}
        if self.remux.preferred_container.lower() not in allowed_containers:
            _error("remux.preferred_container", f"must be one of {sorted(allowed_containers)}")

        if self.audio.max_channels < 1:
            _error("audio.max_channels", "must be >= 1")
        if self.video.max_4k_fps < 1:
            _error("video.max_4k_fps", "must be >= 1")
        if self.video.hardware_encoder not in {"auto", "software", "videotoolbox"}:
            _error("video.hardware_encoder", "must be one of ['auto', 'software', 'videotoolbox']")
        if self.video.encoder_threads < 0:
            _error("video.encoder_threads", "must be >= 0")
        if self.video.videotoolbox_bitrate_multiplier <= 0:
            _error("video.videotoolbox_bitrate_multiplier", "must be > 0")
        if self.video.videotoolbox_min_bitrate_kbps < 1:
            _error("video.videotoolbox_min_bitrate_kbps", "must be >= 1")
        if self.video.videotoolbox_max_bitrate_kbps < self.video.videotoolbox_min_bitrate_kbps:
            _error("video.videotoolbox_max_bitrate_kbps", "must be >= video.videotoolbox_min_bitrate_kbps")
        if self.video.keyframe_interval_seconds < 1:
            _error("video.keyframe_interval_seconds", "must be >= 1")
        if self.video.hevc_tag.lower() not in {"hev1", "hvc1"}:
            _error("video.hevc_tag", "must be one of ['hvc1', 'hev1']")

        if self.validation.verify_duration_tolerance_seconds < 0:
            _error("validation.verify_duration_tolerance_seconds", "must be >= 0")
        if self.validation.verify_stream_count_delta_max < 0:
            _error("validation.verify_stream_count_delta_max", "must be >= 0")
        if self.paths.temp_dir_strategy not in {"source_first", "configured_first"}:
            _error("paths.temp_dir_strategy", "must be one of ['configured_first', 'source_first']")

        if not self.tooling.ffmpeg_bin.strip():
            _error("tooling.ffmpeg_bin", "must not be empty")
        if not self.tooling.ffprobe_bin.strip():
            _error("tooling.ffprobe_bin", "must not be empty")

        ffmpeg_path = Path(self.tooling.ffmpeg_bin).expanduser()
        if ffmpeg_path.is_absolute():
            if not ffmpeg_path.exists():
                _error("tooling.ffmpeg_bin", f"binary not found: {ffmpeg_path}")
            elif not os.access(ffmpeg_path, os.X_OK):
                _error("tooling.ffmpeg_bin", f"not executable: {ffmpeg_path}")

        ffprobe_path = Path(self.tooling.ffprobe_bin).expanduser()
        if ffprobe_path.is_absolute():
            if not ffprobe_path.exists():
                _error("tooling.ffprobe_bin", f"binary not found: {ffprobe_path}")
            elif not os.access(ffprobe_path, os.X_OK):
                _error("tooling.ffprobe_bin", f"not executable: {ffprobe_path}")

        for field, value in [
            ("tooling.dovi_muxer_bin", self.tooling.dovi_muxer_bin),
            ("tooling.mp4box_bin", self.tooling.mp4box_bin),
            ("tooling.mediainfo_bin", self.tooling.mediainfo_bin),
            ("tooling.mp4muxer_bin", self.tooling.mp4muxer_bin),
        ]:
            if not value:
                continue
            bin_path = Path(value).expanduser()
            if not bin_path.is_absolute():
                continue
            if not bin_path.exists():
                _error(field, f"binary not found: {bin_path}")
            elif not os.access(bin_path, os.X_OK):
                _error(field, f"not executable: {bin_path}")

        if not self.watch.allowed_extensions:
            _error("watch.allowed_extensions", "must contain at least one extension")
        else:
            for ext in sorted(self.watch.allowed_extensions):
                if not ext.startswith("."):
                    _error("watch.allowed_extensions", f"extension must start with '.': {ext}")
        if not self.watch.priority_extensions:
            _error("watch.priority_extensions", "must contain at least one extension")
        else:
            for ext in sorted(self.watch.priority_extensions):
                if not ext.startswith("."):
                    _error("watch.priority_extensions", f"extension must start with '.': {ext}")

        for watch_root in self.watch_roots():
            for field, managed_path in self.managed_paths().items():
                if paths_overlap(watch_root, managed_path):
                    _error(
                        field,
                        f"must not overlap any watch folder: {managed_path}",
                    )
                    _error(
                        "watch.folders",
                        f"must not overlap managed path {field}: {watch_root}",
                    )

        for priority_root in self.priority_watch_roots():
            for field, managed_path in self.managed_paths().items():
                if field.startswith("output.output_root"):
                    continue
                if paths_overlap(priority_root, managed_path):
                    _error(
                        "watch.priority_folders",
                        f"must not overlap managed path {field}: {priority_root}",
                    )

        process_roots = {*self.watch_roots(), *self.priority_watch_roots()}
        for source_root in sorted(
            (path.expanduser().resolve() for path in self.output.output_root_overrides),
            key=str,
        ):
            if source_root not in process_roots:
                _error(
                    "output.output_root_overrides",
                    f"override source root must exactly match a configured watch or priority folder: {source_root}",
                )

        for delete_root in sorted(
            (path.expanduser().resolve() for path in self.output.delete_original_after_success_roots),
            key=str,
        ):
            if delete_root not in process_roots:
                _error(
                    "output.delete_original_after_success_roots",
                    f"delete root must exactly match a configured watch or priority folder: {delete_root}",
                )

        for source_root in sorted(
            (path.expanduser().resolve() for path in self.paths.temp_dir_overrides),
            key=str,
        ):
            if source_root not in process_roots:
                _error(
                    "paths.temp_dir_overrides",
                    f"override source root must exactly match a configured watch or priority folder: {source_root}",
                )

        return errors


def _serialize_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return sorted(_serialize_value(v) for v in value)
    if isinstance(value, list):
        return [_serialize_value(v) for v in value]
    if isinstance(value, tuple):
        return [_serialize_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _serialize_value(v) for k, v in value.items()}
    return value


def _tool_identity(value: Any) -> dict[str, Any]:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        return {"path": str(value), "absolute": False}
    try:
        stat = path.stat()
    except OSError:
        return {"path": str(path), "exists": False}
    return {
        "path": str(path),
        "exists": True,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }
