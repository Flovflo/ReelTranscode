from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from pathlib import Path

from reeltranscode.config import AppConfig
from reeltranscode.models import (
    CommandStep,
    CompatibilityDetails,
    Decision,
    ExecutionPlan,
    MediaInfo,
    Strategy,
)
from reeltranscode.utils import ensure_dir

SUPPORTED_AUDIO = {"eac3", "ac3", "aac"}


@dataclass(slots=True)
class SubtitleExport:
    map_spec: str
    output_path: Path
    codec: str


class CommandPlanner:
    def __init__(self, config: AppConfig):
        self.config = config

    def build(
        self,
        media: MediaInfo,
        decision: Decision,
        compatibility: CompatibilityDetails,
        source_root: Path | None,
    ) -> ExecutionPlan:
        target_path = self._build_target_path(media.path, source_root)
        temp_path = self._build_temp_path(media.path, target_path)
        notes: list[str] = []
        steps: list[CommandStep] = []
        subtitle_sidecars: list[Path] = []

        if decision.strategy == Strategy.NO_OP:
            return ExecutionPlan(
                source_path=media.path,
                target_path=None,
                temp_path=None,
                strategy=decision.strategy,
                case_label=decision.case_label,
                steps=[],
                notes=["No-op path selected"],
            )

        ffmpeg = self.config.tooling.ffmpeg_bin
        cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(media.path), "-map", "0"]

        if not self.config.remux.keep_attachments:
            cmd.extend(["-map", "-0:t"])
        if not self.config.remux.keep_chapters:
            cmd.extend(["-map_chapters", "-1"])

        cmd.extend(self._video_args(media, decision, compatibility))
        subtitle_args, subtitle_exports, subtitle_notes = self._subtitle_args(media, decision)
        cmd.extend(subtitle_args)
        notes.extend(subtitle_notes)
        cmd.extend(self._audio_args(media, decision))

        cmd.extend(["-map_metadata", "0"])
        if target_path.suffix.lower() == ".mp4":
            movflags = "+write_colr"
            if self.config.remux.faststart:
                movflags = f"{movflags}+faststart"
            cmd.extend(["-movflags", movflags])

        cmd.append(str(temp_path))
        steps.append(CommandStep(name="main_ffmpeg", command=cmd, expected_outputs=[temp_path]))

        for export in subtitle_exports:
            subtitle_sidecars.append(export.output_path)
            export_cmd = [
                ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-y",
                "-i",
                str(media.path),
                "-map",
                export.map_spec,
                "-c:s",
                export.codec,
                str(export.output_path),
            ]
            steps.append(
                CommandStep(
                    name="subtitle_export",
                    command=export_cmd,
                    expected_outputs=[export.output_path],
                )
            )

        return ExecutionPlan(
            source_path=media.path,
            target_path=target_path,
            temp_path=temp_path,
            strategy=decision.strategy,
            case_label=decision.case_label,
            steps=steps,
            external_subtitle_outputs=subtitle_sidecars,
            notes=notes,
        )

    def _video_args(
        self,
        media: MediaInfo,
        decision: Decision,
        compatibility: CompatibilityDetails,
    ) -> list[str]:
        if decision.strategy not in {Strategy.VIDEO_ONLY, Strategy.FULL_PIPELINE} and not compatibility.requires_video_transcode:
            args = ["-c:v", "copy"]
            target_is_mp4 = self._target_suffix() == ".mp4"
            source = media.primary_video
            if target_is_mp4 and source and (source.codec_name or "").lower() == "hevc":
                args.extend(["-tag:v", self.config.video.hevc_tag])
            return args

        source = media.primary_video
        if source is None:
            return ["-c:v", "copy"]

        use_hevc = self.config.video.preferred_codec == "hevc"
        fps = _fps(source.avg_frame_rate or source.r_frame_rate) or 24.0
        gop = max(24, int(math.ceil(fps * self.config.video.keyframe_interval_seconds)))

        args: list[str] = []
        if use_hevc:
            args.extend(["-c:v", "hevc_videotoolbox", "-tag:v", self.config.video.hevc_tag])
            if decision.force_sdr:
                args.extend(["-profile:v", "main", "-pix_fmt", "yuv420p"])
            else:
                # Keep HDR/Dolby Vision transcodes in Main10 to avoid narrowing dynamic range.
                preserve_hdr_pipeline = compatibility.hdr10_present or compatibility.dv_present
                ten_bit_source = (source.pix_fmt or "") in {"yuv420p10le", "p010le"}
                if ten_bit_source or preserve_hdr_pipeline:
                    args.extend(["-profile:v", "main10", "-pix_fmt", "p010le"])
                else:
                    args.extend(["-profile:v", "main", "-pix_fmt", "yuv420p"])
        else:
            args.extend(["-c:v", "h264_videotoolbox", "-profile:v", "high", "-pix_fmt", "yuv420p"])

        target_bitrate = _video_target_bitrate(source.bit_rate)
        args.extend(["-b:v", f"{target_bitrate}"])
        args.extend(["-g", str(gop), "-keyint_min", str(gop)])

        if self.config.video.force_cfr:
            args.extend(["-vsync", "cfr"])

        if decision.force_sdr:
            args.extend(["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"])
        else:
            if source.color_primaries:
                args.extend(["-color_primaries", source.color_primaries])
            if source.color_transfer:
                args.extend(["-color_trc", source.color_transfer])
            if source.color_space:
                args.extend(["-colorspace", source.color_space])

        return args

    def _audio_args(self, media: MediaInfo, decision: Decision) -> list[str]:
        if not media.audio_streams:
            return []

        args: list[str] = ["-c:a", "copy"]
        for out_audio_index, stream in enumerate(media.audio_streams):
            codec = (stream.codec_name or "").lower()
            if decision.strategy in {Strategy.AUDIO_ONLY, Strategy.FULL_PIPELINE} and codec not in SUPPORTED_AUDIO:
                if (stream.channels or 2) > 2:
                    target_codec = self.config.audio.preferred_codec_multichannel
                    bitrate = "640k" if (stream.channels or 6) <= 6 else "768k"
                else:
                    target_codec = self.config.audio.preferred_codec_stereo
                    bitrate = "192k"
                args.extend([f"-c:a:{out_audio_index}", target_codec, f"-b:a:{out_audio_index}", bitrate])

            language = stream.language or "und"
            args.extend([f"-metadata:s:a:{out_audio_index}", f"language={language}"])
            if stream.title:
                args.extend([f"-metadata:s:a:{out_audio_index}", f"title={stream.title}"])
            if stream.disposition.default:
                args.extend([f"-disposition:a:{out_audio_index}", "default"])
            else:
                args.extend([f"-disposition:a:{out_audio_index}", "0"])
        return args

    def _subtitle_args(self, media: MediaInfo, decision: Decision) -> tuple[list[str], list[SubtitleExport], list[str]]:
        if not media.subtitle_streams:
            return ["-c:s", "copy"], [], []

        target_suffix = self._target_suffix()
        args: list[str] = ["-c:s", "copy"]
        exports: list[SubtitleExport] = []
        notes: list[str] = []

        if target_suffix != ".mp4":
            return args, exports, notes

        output_sub_index = 0
        for source_sub_index, stream in enumerate(media.subtitle_streams):
            lang = (stream.language or "und").lower()
            map_spec = f"0:s:{source_sub_index}"
            if stream.is_image_subtitle:
                args.extend(["-map", f"-0:s:{source_sub_index}"])
                ext = (
                    "sup"
                    if (stream.codec_name or "") == "hdmv_pgs_subtitle"
                    else self.config.subtitles.external_subtitle_format
                )
                export = self._subtitle_export_path(media.path, source_sub_index, lang, ext)
                codec = "copy" if ext == "sup" else self.config.subtitles.external_subtitle_format
                exports.append(SubtitleExport(map_spec=map_spec, output_path=export, codec=codec))
                notes.append(f"Externalized image subtitle stream {source_sub_index} -> {export.name}")
                continue

            if self.config.subtitles.convert_text_to_mov_text:
                args.extend([f"-c:s:{output_sub_index}", "mov_text"])
            else:
                args.extend(["-map", f"-0:s:{source_sub_index}"])
                export = self._subtitle_export_path(
                    media.path,
                    source_sub_index,
                    lang,
                    self.config.subtitles.external_subtitle_format,
                )
                exports.append(
                    SubtitleExport(
                        map_spec=map_spec,
                        output_path=export,
                        codec=self.config.subtitles.external_subtitle_format,
                    )
                )
                notes.append(f"Externalized text subtitle stream {source_sub_index} -> {export.name}")
                continue

            args.extend([f"-metadata:s:s:{output_sub_index}", f"language={lang}"])
            if stream.disposition.forced:
                args.extend([f"-disposition:s:{output_sub_index}", "forced"])
            output_sub_index += 1

        return args, exports, notes

    def _build_target_path(self, source: Path, source_root: Path | None) -> Path:
        suffix = self._target_suffix()
        if self.config.output.mode == "replace_original":
            return source.with_suffix(suffix)

        relative = source.name
        if source_root:
            try:
                relative = str(source.relative_to(source_root))
            except ValueError:
                relative = source.name
        rel_path = Path(relative).with_suffix(suffix)
        return (self.config.output.output_root / rel_path).resolve()

    def _build_temp_path(self, source: Path, target_path: Path) -> Path:
        token = uuid.uuid4().hex[:10]
        temp_name = f".{source.stem}.{token}.tmp{target_path.suffix}"
        # Keep temp file on target filesystem to avoid cross-device moves on commit.
        try:
            ensure_dir(target_path.parent)
            return (target_path.parent / temp_name).resolve()
        except OSError:
            ensure_dir(self.config.paths.temp_dir)
            return (self.config.paths.temp_dir / temp_name).resolve()

    def _target_suffix(self) -> str:
        preferred = self.config.remux.preferred_container.lower()
        if preferred == "mp4":
            return ".mp4"
        if preferred in {"mov", "m4v"}:
            return f".{preferred}"
        return ".mkv"

    def _subtitle_export_path(self, source: Path, subtitle_index: int, lang: str, ext: str) -> Path:
        return source.with_name(f"{source.stem}__stream_{subtitle_index}.{lang}.{ext}")


def _fps(value: str | None) -> float | None:
    if not value or value in {"0/0", "N/A"}:
        return None
    if "/" in value:
        left, right = value.split("/", 1)
        try:
            denominator = float(right)
            if denominator == 0:
                return None
            return float(left) / denominator
        except ValueError:
            return None
    try:
        return float(value)
    except ValueError:
        return None


def _video_target_bitrate(source_bit_rate: int | None) -> str:
    if source_bit_rate is None:
        return "12000000"
    estimated = int(source_bit_rate * 0.88)
    estimated = min(max(estimated, 4_000_000), 35_000_000)
    return str(estimated)
