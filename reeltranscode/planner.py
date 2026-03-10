from __future__ import annotations

import math
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from reeltranscode.config import AppConfig
from reeltranscode.analyzer import FFprobeAnalyzer
from reeltranscode.models import (
    CommandStep,
    CompatibilityDetails,
    Decision,
    ExecutionPlan,
    MediaInfo,
    OcrSubtitleTask,
    Strategy,
)
from reeltranscode.tooling import ToolchainResolver
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
        self.tooling = ToolchainResolver(config)

    def preview_target_path(self, source: Path, source_root: Path | None) -> Path:
        return self._build_target_path(source, source_root)

    def build(
        self,
        media: MediaInfo,
        decision: Decision,
        compatibility: CompatibilityDetails,
        source_root: Path | None,
    ) -> ExecutionPlan:
        target_path = self._build_target_path(media.path, source_root)
        temp_root = self._select_temp_root(media, decision)
        notes: list[str] = []
        steps: list[CommandStep] = []
        subtitle_sidecars: list[Path] = []
        dropped_subtitle_streams: list[int] = []
        cleanup_paths: list[Path] = []
        cleanup_dirs: list[Path] = []

        if decision.strategy == Strategy.NO_OP:
            return ExecutionPlan(
                source_path=media.path,
                target_path=None,
                temp_path=None,
                workspace_dir=None,
                strategy=decision.strategy,
                case_label=decision.case_label,
                steps=[],
                notes=["No-op path selected"],
            )

        self._ensure_apple_native_mp4_subtitles(media)

        if decision.use_dovi_muxer:
            return self._build_dovi_muxer_plan(media, decision, source_root, temp_root=temp_root)

        if temp_root != self._configured_temp_root():
            notes.append(f"Using alternate temporary workspace volume: {temp_root}")

        workspace_dir = (
            self._build_workspace_dir(media.path, "subtitle-ocr", root=temp_root)
            if self._needs_subtitle_ocr(media)
            else None
        )
        if workspace_dir is not None:
            cleanup_dirs.append(workspace_dir)
            temp_path = (workspace_dir / f"{media.path.stem}.tmp{target_path.suffix}").resolve()
            step_cwd = workspace_dir
        else:
            temp_path = self._build_temp_path(media.path, target_path, root=temp_root)
            step_cwd = target_path.parent

        subtitle_args, subtitle_exports, subtitle_notes, dropped_subtitle_streams, ocr_subtitle_tasks = self._subtitle_args(
            media,
            decision,
            target_path,
            workspace_dir=workspace_dir,
            include_maps=workspace_dir is not None,
            ocr_input_start_index=1,
        )

        ffmpeg = self.config.tooling.ffmpeg_bin
        cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(media.path)]
        for task in ocr_subtitle_tasks:
            cmd.extend(["-i", str(task.output_path)])

        if workspace_dir is not None:
            cmd.extend(["-map", "0:v", "-map", "0:a?"])
        else:
            cmd.extend(["-map", "0"])

        if not self.config.remux.keep_attachments:
            cmd.extend(["-map", "-0:t"])
        if not self.config.remux.keep_chapters:
            cmd.extend(["-map_chapters", "-1"])

        cmd.extend(self._video_args(media, decision, compatibility))
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
        steps.append(CommandStep(name="main_ffmpeg", command=cmd, expected_outputs=[temp_path], cwd=step_cwd))

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
                    cwd=step_cwd,
                )
            )

        return ExecutionPlan(
            source_path=media.path,
            target_path=target_path,
            temp_path=temp_path,
            workspace_dir=workspace_dir,
            strategy=decision.strategy,
            case_label=decision.case_label,
            steps=steps,
            ocr_subtitle_tasks=ocr_subtitle_tasks,
            external_subtitle_outputs=subtitle_sidecars,
            dropped_subtitle_streams=dropped_subtitle_streams,
            cleanup_paths=cleanup_paths,
            cleanup_dirs=cleanup_dirs,
            notes=notes,
        )

    def _build_dovi_muxer_plan(
        self,
        media: MediaInfo,
        decision: Decision,
        source_root: Path | None,
        *,
        temp_root: Path | None = None,
    ) -> ExecutionPlan:
        caps = self.tooling.resolve_dolby_vision_mux_capabilities()
        if not caps.available or not caps.mp4muxer_bin:
            missing = ", ".join(sorted(caps.missing_tools)) or "unknown"
            raise RuntimeError(f"DoViMuxer toolchain unavailable: {missing}")

        target_path = self._build_target_path(media.path, source_root)
        selected_temp_root = temp_root or self._select_temp_root(media, decision)
        workspace_dir = self._build_workspace_dir(media.path, "dovi", root=selected_temp_root)
        step_cwd = workspace_dir
        ocr_subtitle_tasks = self._build_ocr_subtitle_tasks(media, workspace_dir)
        temp_path = (workspace_dir / f"{media.path.stem}.tmp{target_path.suffix}").resolve()
        base_temp_path = (
            (workspace_dir / f"{media.path.stem}.dovi-base{target_path.suffix}").resolve()
            if ocr_subtitle_tasks
            else temp_path
        )
        cmd = [caps.dovi_muxer_bin, str(base_temp_path), "-i", str(media.path), "-ffmpeg", caps.ffmpeg_bin]
        notes = ["DoViMuxer Dolby Vision safe remux path selected"]
        if selected_temp_root != self._configured_temp_root():
            notes.append(f"Using alternate temporary workspace volume: {selected_temp_root}")
        subtitle_sidecars: list[Path] = []
        dropped_subtitle_streams: list[int] = []
        mp4muxer_wrapper = self._build_mp4muxer_wrapper(media, caps.mp4muxer_bin, workspace_dir)
        cleanup_paths = [mp4muxer_wrapper]
        cleanup_dirs = [workspace_dir]
        steps: list[CommandStep] = []
        if caps.mp4box_bin:
            mp4box_wrapper = self._build_mp4box_wrapper(media, caps.mp4box_bin, workspace_dir)
            if mp4box_wrapper is not None:
                cmd.extend(["-mp4box", str(mp4box_wrapper)])
                cleanup_paths.append(mp4box_wrapper)
                notes.append("Trimmed overlong audio track(s) to source video duration on the DV-safe remux path")
            else:
                cmd.extend(["-mp4box", caps.mp4box_bin])
        if caps.mediainfo_bin:
            cmd.extend(["-mediainfo", caps.mediainfo_bin])
        cmd.extend(["-mp4muxer", str(mp4muxer_wrapper)])
        if not self.config.remux.keep_chapters:
            cmd.append("--nochap")

        cmd.extend(["-map", "0:v:0"])

        output_audio_index = 0
        for source_audio_index, stream in enumerate(media.audio_streams):
            cmd.extend(["-map", f"0:a:{source_audio_index}"])
            if meta := self._dovi_meta_arg("a", output_audio_index, stream.language, stream.title):
                cmd.extend(["-meta", meta])
            if stream.disposition.default:
                cmd.extend(["-default", f"a:{output_audio_index}"])
            output_audio_index += 1

        if not ocr_subtitle_tasks:
            output_sub_index = 0
            for source_sub_index, stream in enumerate(media.subtitle_streams):
                if stream.is_image_subtitle:
                    if self.config.subtitles.drop_incompatible_image_subtitles:
                        dropped_subtitle_streams.append(source_sub_index)
                        notes.append(
                            "Dropped incompatible image subtitle track(s) for Apple-native MP4 output"
                        )
                        continue
                    raise RuntimeError(
                        "Image subtitles require OCR for Apple-native MP4 output; refusing to externalize "
                        f"stream {source_sub_index} ({stream.codec_name or 'unknown'})"
                    )
                lang = (stream.language or "und").lower()
                cmd.extend(["-map", f"0:s:{source_sub_index}"])
                subtitle_title = self._subtitle_title_for_dovi(
                    stream.title,
                    stream.disposition.hearing_impaired,
                    stream.disposition.captions,
                )
                if meta := self._dovi_meta_arg("s", output_sub_index, lang, subtitle_title):
                    cmd.extend(["-meta", meta])
                if stream.disposition.default:
                    cmd.extend(["-default", f"s:{output_sub_index}"])
                if stream.disposition.forced:
                    cmd.extend(["-forced", f"s:{output_sub_index}"])
                output_sub_index += 1

        cmd.append("-y")
        steps.append(CommandStep(name="dovi_muxer", command=cmd, expected_outputs=[base_temp_path], cwd=step_cwd))

        if ocr_subtitle_tasks:
            notes.append("OCR image subtitle track(s) to mov_text for Apple-native MP4 output")
            subtitle_merge_cmd = [self.config.tooling.ffmpeg_bin, "-hide_banner", "-nostdin", "-y", "-i", str(base_temp_path), "-i", str(media.path)]
            for task in ocr_subtitle_tasks:
                subtitle_merge_cmd.extend(["-i", str(task.output_path)])
            subtitle_merge_cmd.extend(["-map", "0:v:0", "-map", "0:a?"])
            subtitle_args, _, subtitle_notes, dropped_subtitle_streams, _ = self._subtitle_args(
                media,
                decision,
                target_path,
                workspace_dir=workspace_dir,
                input_index=1,
                include_maps=True,
                ocr_input_start_index=2,
            )
            subtitle_merge_cmd.extend(["-c:v", "copy", "-tag:v", self.config.video.hevc_tag, "-c:a", "copy"])
            subtitle_merge_cmd.extend(subtitle_args)
            notes.extend(subtitle_notes)
            subtitle_merge_cmd.extend(["-map_metadata", "1"])
            if self.config.remux.keep_chapters:
                subtitle_merge_cmd.extend(["-map_chapters", "1"])
            else:
                subtitle_merge_cmd.extend(["-map_chapters", "-1"])
            movflags = "+write_colr"
            if self.config.remux.faststart:
                movflags = f"{movflags}+faststart"
            subtitle_merge_cmd.extend(["-movflags", movflags, str(temp_path)])
            steps.append(
                CommandStep(
                    name="dovi_subtitle_merge",
                    command=subtitle_merge_cmd,
                    expected_outputs=[temp_path],
                    cwd=step_cwd,
                )
            )
            if dv_profile_arg := self._dovi_mp4box_profile_arg(media):
                steps.append(
                    CommandStep(
                        name="dovi_metadata_patch",
                        command=[
                            caps.mp4box_bin,
                            "-tmp",
                            str(workspace_dir),
                            "-add",
                            f"self#video:dvp={dv_profile_arg}",
                            str(temp_path),
                        ],
                        expected_outputs=[temp_path],
                        cwd=step_cwd,
                    )
                )
                notes.append(f"Reapplied Dolby Vision signaling after subtitle merge via MP4Box ({dv_profile_arg})")
        else:
            temp_path = base_temp_path

        return ExecutionPlan(
            source_path=media.path,
            target_path=target_path,
            temp_path=temp_path,
            workspace_dir=workspace_dir,
            strategy=decision.strategy,
            case_label=decision.case_label,
            steps=steps,
            ocr_subtitle_tasks=ocr_subtitle_tasks,
            external_subtitle_outputs=subtitle_sidecars,
            dropped_subtitle_streams=dropped_subtitle_streams,
            cleanup_paths=cleanup_paths,
            cleanup_dirs=cleanup_dirs,
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
            # For Apple HDR playback, always keep explicit PQ + BT.2020 signaling.
            if compatibility.hdr10_present or compatibility.dv_present:
                args.extend(["-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc"])
            else:
                if source.color_primaries:
                    args.extend(["-color_primaries", source.color_primaries])
                if source.color_transfer:
                    args.extend(["-color_trc", source.color_transfer])
                if source.color_space:
                    args.extend(["-colorspace", source.color_space])

        return args

    def _audio_args(
        self,
        media: MediaInfo,
        decision: Decision,
        *,
        input_index: int = 0,
        include_default_maps: bool = False,
    ) -> list[str]:
        if not media.audio_streams:
            return []

        args: list[str] = []
        has_aac_stereo = False
        fallback_source_audio_index = 0
        for source_audio_index, stream in enumerate(media.audio_streams):
            if stream.disposition.default:
                fallback_source_audio_index = source_audio_index
            codec = (stream.codec_name or "").lower()
            channels = stream.channels or 2
            if codec == "aac" and channels <= 2:
                has_aac_stereo = True
            if include_default_maps:
                args.extend(["-map", f"{input_index}:a:{source_audio_index}"])

        args.extend(["-c:a", "copy"])

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

        target_is_mp4 = self._target_suffix() == ".mp4"
        if (
            target_is_mp4
            and self.config.audio.ensure_aac_fallback_stereo_when_missing
            and not has_aac_stereo
            and media.audio_streams
        ):
            fallback_out_audio_index = len(media.audio_streams)
            args.extend(["-map", f"{input_index}:a:{fallback_source_audio_index}"])
            args.extend(
                [
                    f"-c:a:{fallback_out_audio_index}",
                    "aac",
                    f"-ac:a:{fallback_out_audio_index}",
                    "2",
                    f"-b:a:{fallback_out_audio_index}",
                    "192k",
                    f"-metadata:s:a:{fallback_out_audio_index}",
                    "title=AAC Stereo Fallback",
                    f"-disposition:a:{fallback_out_audio_index}",
                    "0",
                ]
            )
        return args

    def _subtitle_args(
        self,
        media: MediaInfo,
        decision: Decision,
        target_path: Path,
        *,
        workspace_dir: Path | None = None,
        input_index: int = 0,
        include_maps: bool = False,
        ocr_input_start_index: int = 1,
    ) -> tuple[list[str], list[SubtitleExport], list[str], list[int], list[OcrSubtitleTask]]:
        if not media.subtitle_streams:
            return ["-c:s", "copy"], [], [], [], []

        target_suffix = self._target_suffix()
        args: list[str] = ["-c:s", "copy"]
        exports: list[SubtitleExport] = []
        notes: list[str] = []
        dropped_subtitle_streams: list[int] = []
        ocr_subtitle_tasks: list[OcrSubtitleTask] = []

        if target_suffix != ".mp4":
            return args, exports, notes, dropped_subtitle_streams, ocr_subtitle_tasks

        output_sub_index = 0
        next_ocr_input_index = ocr_input_start_index
        for source_sub_index, stream in enumerate(media.subtitle_streams):
            lang = (stream.language or "und").lower()
            if stream.is_image_subtitle:
                if self.config.subtitles.ocr_image_subtitles:
                    if workspace_dir is None:
                        raise RuntimeError("Image subtitle OCR requires a workspace directory")
                    task = self._build_ocr_subtitle_task(stream, source_sub_index, workspace_dir)
                    ocr_subtitle_tasks.append(task)
                    args.extend(["-map", f"{next_ocr_input_index}:0"])
                    args.extend([f"-c:s:{output_sub_index}", "mov_text"])
                    args.extend([f"-metadata:s:s:{output_sub_index}", f"language={lang}"])
                    subtitle_title = self._subtitle_title_for_dovi(
                        stream.title,
                        stream.disposition.hearing_impaired,
                        stream.disposition.captions,
                    )
                    if subtitle_title:
                        args.extend([f"-metadata:s:s:{output_sub_index}", f"title={subtitle_title}"])
                    args.extend([f"-disposition:s:{output_sub_index}", self._subtitle_disposition_value(stream)])
                    notes.append(f"OCR subtitle stream {source_sub_index} ({stream.codec_name or 'unknown'}) to mov_text")
                    output_sub_index += 1
                    next_ocr_input_index += 1
                    continue
                if self.config.subtitles.drop_incompatible_image_subtitles:
                    dropped_subtitle_streams.append(source_sub_index)
                    notes.append(
                        "Dropped incompatible image subtitle track(s) for Apple-native MP4 output"
                    )
                    continue
                raise RuntimeError(
                    "Image subtitles require OCR for Apple-native MP4 output; refusing to externalize "
                    f"stream {source_sub_index} ({stream.codec_name or 'unknown'})"
                )

            if include_maps:
                args.extend(["-map", f"{input_index}:s:{source_sub_index}"])
            args.extend([f"-c:s:{output_sub_index}", "mov_text"])
            args.extend([f"-metadata:s:s:{output_sub_index}", f"language={lang}"])
            if stream.title:
                args.extend([f"-metadata:s:s:{output_sub_index}", f"title={stream.title}"])
            args.extend(
                [
                    f"-disposition:s:{output_sub_index}",
                    self._subtitle_disposition_value(stream),
                ]
            )
            output_sub_index += 1

        return args, exports, notes, dropped_subtitle_streams, ocr_subtitle_tasks

    def _ensure_apple_native_mp4_subtitles(self, media: MediaInfo) -> None:
        if self._target_suffix() != ".mp4":
            return
        for source_sub_index, stream in enumerate(media.subtitle_streams):
            if stream.is_image_subtitle:
                if self.config.subtitles.ocr_image_subtitles:
                    continue
                if self.config.subtitles.drop_incompatible_image_subtitles:
                    continue
                raise RuntimeError(
                    "Image subtitles require OCR for Apple-native MP4 output; refusing to externalize "
                    f"stream {source_sub_index} ({stream.codec_name or 'unknown'})"
                )

    def _needs_subtitle_ocr(self, media: MediaInfo) -> bool:
        if self._target_suffix() != ".mp4" or not self.config.subtitles.ocr_image_subtitles:
            return False
        return any(stream.is_image_subtitle for stream in media.subtitle_streams)

    def _build_ocr_subtitle_tasks(self, media: MediaInfo, workspace_dir: Path) -> list[OcrSubtitleTask]:
        tasks: list[OcrSubtitleTask] = []
        if not self.config.subtitles.ocr_image_subtitles:
            return tasks
        for source_sub_index, stream in enumerate(media.subtitle_streams):
            if stream.is_image_subtitle:
                tasks.append(self._build_ocr_subtitle_task(stream, source_sub_index, workspace_dir))
        return tasks

    def _build_ocr_subtitle_task(
        self,
        stream,
        source_sub_index: int,
        workspace_dir: Path,
    ) -> OcrSubtitleTask:
        lang = (stream.language or "und").lower()
        title = self._subtitle_title_for_dovi(stream.title, stream.disposition.hearing_impaired, stream.disposition.captions)
        # Keep the extracted PGS source filename language-neutral because pgsrip
        # reparses dotted suffixes as language markers and may rewrite the path.
        sup_path = (workspace_dir / f"subtitle_{source_sub_index}.sup").resolve()
        output_path = (workspace_dir / f"subtitle_{source_sub_index}.{lang}.srt").resolve()
        return OcrSubtitleTask(
            source_subtitle_index=source_sub_index,
            source_codec=stream.codec_name,
            language=lang,
            title=title,
            default=stream.disposition.default,
            forced=stream.disposition.forced,
            hearing_impaired=stream.disposition.hearing_impaired,
            captions=stream.disposition.captions,
            sup_path=sup_path,
            output_path=output_path,
        )

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

    def _build_temp_path(self, source: Path, target_path: Path, *, hidden: bool = True, root: Path | None = None) -> Path:
        token = uuid.uuid4().hex[:10]
        prefix = "." if hidden else ""
        temp_name = f"{prefix}{source.stem}.{token}.tmp{target_path.suffix}"
        temp_root = self._normalize_temp_root(root)
        ensure_dir(temp_root)
        return (temp_root / temp_name).resolve()

    def _build_intermediate_path(self, source: Path, label: str, suffix: str) -> Path:
        token = uuid.uuid4().hex[:10]
        file_name = f".{source.stem}.{token}.{label}{suffix}"
        ensure_dir(self.config.paths.temp_dir)
        return (self.config.paths.temp_dir / file_name).resolve()

    def _build_workspace_dir(self, source: Path, label: str, *, root: Path | None = None) -> Path:
        token = uuid.uuid4().hex[:10]
        temp_root = self._normalize_temp_root(root)
        workspace_dir = (temp_root / f".{source.stem}.{token}.{label}").resolve()
        ensure_dir(workspace_dir)
        return workspace_dir

    def _select_temp_root(self, media: MediaInfo, decision: Decision) -> Path:
        required_bytes = self._estimated_temp_requirement(
            media.size,
            use_dovi_muxer=decision.use_dovi_muxer,
            needs_subtitle_ocr=self._needs_subtitle_ocr(media),
        )
        fallback_root = self._configured_temp_root()
        for candidate in self._temp_root_candidates():
            ensure_dir(candidate)
            try:
                free_bytes = shutil.disk_usage(candidate).free
            except OSError:
                continue
            if free_bytes >= required_bytes:
                return candidate
        ensure_dir(fallback_root)
        return fallback_root

    def _temp_root_candidates(self) -> list[Path]:
        candidates = [self._configured_temp_root()]
        if self.config.output.mode != "replace_original":
            output_temp_root = (self.config.output.output_root / ".reeltranscode-tmp").expanduser().resolve()
            if output_temp_root not in candidates:
                candidates.append(output_temp_root)
        return candidates

    def _configured_temp_root(self) -> Path:
        return self.config.paths.temp_dir.expanduser().resolve()

    @staticmethod
    def _estimated_temp_requirement(
        source_size: int,
        *,
        use_dovi_muxer: bool,
        needs_subtitle_ocr: bool,
    ) -> int:
        required_bytes = source_size
        if use_dovi_muxer:
            required_bytes += source_size
        if needs_subtitle_ocr:
            required_bytes += max(512 * 1024 * 1024, source_size // 20)
        return required_bytes

    def _normalize_temp_root(self, root: Path | None) -> Path:
        return (root or self.config.paths.temp_dir).expanduser().resolve()

    def _dovi_mp4box_profile_arg(self, media: MediaInfo) -> str | None:
        dv = FFprobeAnalyzer.inspect_dolby_vision(media)
        if not dv.present or not dv.profile:
            return None
        normalized = dv.profile.strip().lower()
        if not normalized:
            return None
        return normalized

    def _build_mp4muxer_wrapper(self, media: MediaInfo, mp4muxer_bin: str, workspace_dir: Path) -> Path:
        wrapper_path = (workspace_dir / "mp4muxer-fps-wrapper.sh").resolve()
        fps_value = self._source_video_frame_rate(media)
        script = "\n".join(
            [
                "#!/bin/bash",
                "args=()",
                "inject_next=0",
                "injected=0",
                'for arg in "$@"; do',
                '  args+=("$arg")',
                "  if [[ $inject_next -eq 1 && $injected -eq 0 ]]; then",
                f'    args+=("--input-video-frame-rate" "{fps_value}")',
                "    inject_next=0",
                "    injected=1",
                "    continue",
                "  fi",
                '  if [[ "$arg" == "-i" || "$arg" == "--input-file" ]]; then',
                "    inject_next=1",
                "  fi",
                "done",
                f'exec "{mp4muxer_bin}" "${{args[@]}}"',
                "",
            ]
        )
        wrapper_path.write_text(script, encoding="utf-8")
        wrapper_path.chmod(0o755)
        return wrapper_path

    def _build_mp4box_wrapper(self, media: MediaInfo, mp4box_bin: str, workspace_dir: Path) -> Path | None:
        trim_specs = self._audio_trim_specs(media)
        if not trim_specs:
            return None

        wrapper_path = (workspace_dir / "mp4box-audio-trim-wrapper.sh").resolve()
        script_lines = [
            "#!/bin/bash",
            "args=()",
            "inject_next_add=0",
            'for arg in "$@"; do',
            "  if [[ $inject_next_add -eq 1 ]]; then",
            '    rewritten="$arg"',
        ]
        for output_audio_index, duration in trim_specs.items():
            script_lines.extend(
                [
                    f'    if [[ "$arg" == *_Audio{output_audio_index}.* ]] && [[ "$arg" != *:dur=* ]]; then',
                    f'      rewritten="${{arg}}:dur={duration:.3f}"',
                    "    fi",
                ]
            )
        script_lines.extend(
            [
                '    args+=("$rewritten")',
                "    inject_next_add=0",
                "    continue",
                "  fi",
                '  args+=("$arg")',
                '  if [[ "$arg" == "-add" ]]; then',
                "    inject_next_add=1",
                "  fi",
                "done",
                f'exec "{mp4box_bin}" "${{args[@]}}"',
                "",
            ]
        )
        wrapper_path.write_text("\n".join(script_lines), encoding="utf-8")
        wrapper_path.chmod(0o755)
        return wrapper_path

    def _target_suffix(self) -> str:
        preferred = self.config.remux.preferred_container.lower()
        if preferred == "mp4":
            return ".mp4"
        if preferred in {"mov", "m4v"}:
            return f".{preferred}"
        return ".mkv"

    @staticmethod
    def _dovi_meta_arg(track_type: str, track_index: int, language: str | None, title: str | None) -> str | None:
        parts = [f"{track_type}:{track_index}"]
        if language:
            parts.append(f"lang={language.lower()}")
        if title:
            sanitized = title.replace(":", " - ").replace("\n", " ").replace('"', "'").strip()
            if sanitized:
                parts.append(f"name={sanitized}")
        return ":".join(parts) if len(parts) > 1 else None

    @staticmethod
    def _image_subtitle_export(codec_name: str | None) -> tuple[str, str]:
        codec = (codec_name or "").lower()
        if codec == "hdmv_pgs_subtitle":
            return "sup", "copy"
        return "mks", "copy"

    def _subtitle_export_path(self, target_path: Path, subtitle_index: int, lang: str, ext: str) -> Path:
        return target_path.with_name(f"{target_path.stem}__stream_{subtitle_index}.{lang}.{ext}")

    @staticmethod
    def _subtitle_disposition_value(stream) -> str:
        values: list[str] = []
        if stream.disposition.default:
            values.append("default")
        if stream.disposition.forced:
            values.append("forced")
        if stream.disposition.hearing_impaired:
            values.append("hearing_impaired")
        if stream.disposition.captions:
            values.append("captions")
        if not values and stream.title and "sdh" in stream.title.lower():
            values.extend(["hearing_impaired", "captions"])
        return "+".join(values) if values else "0"

    @staticmethod
    def _subtitle_title_for_dovi(title: str | None, hearing_impaired: bool, captions: bool) -> str | None:
        if not (hearing_impaired or captions):
            return title
        if title and _subtitle_title_implies_hi(title):
            return title
        if title:
            return f"{title} SDH"
        return "SDH"

    @staticmethod
    def _source_video_frame_rate(media: MediaInfo) -> str:
        video = media.primary_video
        if video is None:
            raise RuntimeError("Dolby Vision remux requires a video stream")

        for value in [video.avg_frame_rate, video.r_frame_rate]:
            if value and value not in {"0/0", "N/A"}:
                return value
        raise RuntimeError("Dolby Vision remux requires a known source frame rate")

    def _audio_trim_specs(self, media: MediaInfo) -> dict[int, float]:
        video = media.primary_video
        if video is None or video.duration is None:
            return {}

        trim_specs: dict[int, float] = {}
        tolerance = self.config.validation.verify_duration_tolerance_seconds
        for output_audio_index, stream in enumerate(media.audio_streams):
            if stream.duration is None:
                continue
            if stream.duration - video.duration > tolerance:
                trim_specs[output_audio_index] = video.duration
        return trim_specs



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


def _subtitle_title_implies_hi(value: str | None) -> bool:
    if not value:
        return False
    text = value.casefold()
    return any(token in text for token in ["sdh", "hearing impaired", "closed captions", "cc"])
