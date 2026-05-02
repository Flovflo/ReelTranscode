from __future__ import annotations

from functools import lru_cache
import math
import platform
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from reeltranscode.config import AppConfig
from reeltranscode.analyzer import FFprobeAnalyzer
from reeltranscode.languages import normalize_language_code
from reeltranscode.models import (
    CommandStep,
    CompatibilityDetails,
    Decision,
    ExecutionPlan,
    MediaInfo,
    OcrSubtitleTask,
    Strategy,
    StreamInfo,
)
from reeltranscode.tooling import ToolchainResolver
from reeltranscode.utils import RUNTIME_TEMP_DIRNAME, ensure_dir

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
        preferred_temp_root = self._preferred_temp_root(media.path)
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
                target_path=target_path,
                temp_path=None,
                workspace_dir=None,
                strategy=decision.strategy,
                case_label=decision.case_label,
                steps=[],
                notes=["No-op path selected"],
            )

        self._ensure_apple_native_mp4_subtitles(media)

        if decision.use_dovi_muxer:
            return self._build_dovi_muxer_plan(
                media,
                decision,
                source_root,
                temp_root=temp_root,
                preferred_temp_root=preferred_temp_root,
            )

        if temp_root != preferred_temp_root:
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
            cmd.extend(self._attachment_exclusion_args(media))
        if not self.config.remux.keep_chapters:
            cmd.extend(["-map_chapters", "-1"])

        cmd.extend(self._video_args(media, decision, compatibility))
        cmd.extend(subtitle_args)
        notes.extend(subtitle_notes)
        cmd.extend(self._audio_args(media, decision))

        cmd.extend(["-map_metadata", "0"])
        if target_path.suffix.lower() == ".mp4":
            cmd.extend(self._mp4_mux_args(media, compatibility))

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

    def build_mp4_cleanup_steps(
        self,
        media: MediaInfo,
        decision: Decision,
        plan: ExecutionPlan,
    ) -> tuple[list[CommandStep], list[Path], Path | None, list[str]]:
        if (
            plan.temp_path is None
            or plan.target_path is None
            or plan.target_path.suffix.lower() != ".mp4"
            or decision.use_dovi_muxer
        ):
            return [], [], None, []

        ffmpeg = self.config.tooling.ffmpeg_bin
        step_cwd = plan.temp_path.parent
        cleaned_path = plan.temp_path.with_name(f"{plan.temp_path.stem}.apple-clean{plan.temp_path.suffix}")

        cleanup_cmd = [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(plan.temp_path),
            "-map",
            "0:v",
            "-map",
            "0:a?",
            "-map",
            "0:s?",
        ]
        if self.config.remux.keep_chapters:
            cleanup_cmd.extend(["-map_chapters", "0"])
        else:
            cleanup_cmd.extend(["-map_chapters", "-1"])
        cleanup_cmd.extend(["-map_metadata", "0", "-c", "copy"])
        cleanup_cmd.extend(self._mp4_cleanup_stream_metadata_args(media))

        source_video = media.primary_video
        if source_video is not None and (source_video.codec_name or "").lower() == "hevc":
            cleanup_cmd.extend(["-tag:v", self.config.video.hevc_tag])

        cleanup_cmd.extend(self._mp4_mux_args(media))
        cleanup_cmd.append(str(cleaned_path))
        steps = [
            CommandStep(
                name="final_mp4_cleanup",
                command=cleanup_cmd,
                expected_outputs=[cleaned_path],
                cwd=step_cwd,
            )
        ]
        notes = ["Normalized final MP4 container via ffmpeg copy remux for Apple-native playback stability"]
        return steps, [plan.temp_path], cleaned_path, notes

    def _mp4_cleanup_stream_metadata_args(self, media: MediaInfo) -> list[str]:
        args: list[str] = []

        for out_audio_index, stream in enumerate(media.audio_streams):
            language = normalize_language_code(stream.language)
            args.extend([f"-metadata:s:a:{out_audio_index}", f"language={language}"])
            if stream.title:
                args.extend([f"-metadata:s:a:{out_audio_index}", f"title={stream.title}"])
            args.extend(
                [
                    f"-disposition:a:{out_audio_index}",
                    "default" if stream.disposition.default else "0",
                ]
            )

        if self._should_add_aac_fallback(media):
            fallback_out_audio_index = len(media.audio_streams)
            args.extend(
                [
                    f"-metadata:s:a:{fallback_out_audio_index}",
                    "title=AAC Stereo Fallback",
                    f"-disposition:a:{fallback_out_audio_index}",
                    "0",
                ]
            )

        subtitle_states = FFprobeAnalyzer.subtitle_track_states(media)
        output_sub_index = 0
        for source_sub_index, stream in enumerate(media.subtitle_streams):
            state = subtitle_states[source_sub_index] if source_sub_index < len(subtitle_states) else None
            if self._is_empty_image_subtitle_stream(media, stream):
                continue
            if stream.is_image_subtitle and self.config.subtitles.drop_incompatible_image_subtitles:
                continue

            language = normalize_language_code(state.language if state else stream.language)
            args.extend([f"-metadata:s:s:{output_sub_index}", f"language={language}"])
            subtitle_title = self._subtitle_title_for_dovi(
                (state.title if state else stream.title),
                (state.hearing_impaired if state else stream.disposition.hearing_impaired),
                (state.captions if state else stream.disposition.captions),
            )
            if subtitle_title:
                args.extend([f"-metadata:s:s:{output_sub_index}", f"title={subtitle_title}"])
            args.extend(
                [
                    f"-disposition:s:{output_sub_index}",
                    self._subtitle_disposition_value(stream, state=state),
                ]
            )
            output_sub_index += 1

        return args

    def build_hevc_mp4_stabilization_steps(
        self,
        media: MediaInfo,
        decision: Decision,
        compatibility: CompatibilityDetails,
        plan: ExecutionPlan,
    ) -> tuple[list[CommandStep], list[Path]]:
        source = media.primary_video
        if (
            plan.temp_path is None
            or plan.workspace_dir is not None
            or plan.ocr_subtitle_tasks
            or decision.use_dovi_muxer
            or self._target_suffix() != ".mp4"
            or source is None
            or (source.codec_name or "").lower() != "hevc"
        ):
            return [], []

        ffmpeg = self.config.tooling.ffmpeg_bin
        step_cwd = plan.temp_path.parent

        stabilized_ts_path = plan.temp_path.with_name(f"{plan.temp_path.stem}.video-stabilized.ts")
        stabilized_video_path = plan.temp_path.with_name(f"{plan.temp_path.stem}.video-stabilized.mp4")

        stabilize_video_ts_cmd = [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(media.path),
            "-map",
            "0:v:0",
            "-c:v",
            "copy",
            "-f",
            "mpegts",
            str(stabilized_ts_path),
        ]

        stabilize_video_mp4_cmd = [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(stabilized_ts_path),
            "-map",
            "0:v:0",
        ]
        stabilize_video_mp4_cmd.extend(self._video_args(media, decision, compatibility))
        stabilize_video_mp4_cmd.extend(self._mp4_mux_args(media, compatibility))
        stabilize_video_mp4_cmd.append(str(stabilized_video_path))

        subtitle_args, _, _, _, _ = self._subtitle_args(
            media,
            decision,
            plan.target_path or plan.temp_path,
            workspace_dir=None,
            input_index=1,
            include_maps=True,
            ocr_input_start_index=2,
        )
        rebuild_cmd = [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(stabilized_video_path),
            "-i",
            str(media.path),
            "-map",
            "0:v:0",
        ]
        if self.config.remux.keep_chapters:
            rebuild_cmd.extend(["-map_chapters", "1"])
        else:
            rebuild_cmd.extend(["-map_chapters", "-1"])
        rebuild_cmd.extend(self._video_args(media, decision, compatibility))
        rebuild_cmd.extend(subtitle_args)
        rebuild_cmd.extend(self._audio_args(media, decision, input_index=1, include_default_maps=True))
        rebuild_cmd.extend(["-map_metadata", "1"])
        rebuild_cmd.extend(self._mp4_mux_args(media, compatibility))
        rebuild_cmd.append(str(plan.temp_path))

        steps = [
            CommandStep(
                name="stabilize_hevc_video_ts",
                command=stabilize_video_ts_cmd,
                expected_outputs=[stabilized_ts_path],
                cwd=step_cwd,
            ),
            CommandStep(
                name="stabilize_hevc_video_mp4",
                command=stabilize_video_mp4_cmd,
                expected_outputs=[stabilized_video_path],
                cwd=step_cwd,
            ),
            CommandStep(
                name="rebuild_mp4_with_stabilized_video",
                command=rebuild_cmd,
                expected_outputs=[plan.temp_path],
                cwd=step_cwd,
            ),
        ]
        return steps, [stabilized_ts_path, stabilized_video_path]

    def build_dovi_subtitle_repair_steps(
        self,
        media: MediaInfo,
        decision: Decision,
        plan: ExecutionPlan,
    ) -> tuple[list[CommandStep], list[Path], Path | None, list[str]]:
        if (
            plan.temp_path is None
            or plan.workspace_dir is None
            or plan.ocr_subtitle_tasks
            or not decision.use_dovi_muxer
            or not media.subtitle_streams
        ):
            return [], [], None, []

        caps = self.tooling.resolve_dolby_vision_mux_capabilities()
        if not caps.available or not caps.mp4box_bin:
            return [], [], None, []

        repaired_path = plan.temp_path.with_name(f"{plan.temp_path.stem}.subtitle-repaired{plan.temp_path.suffix}")
        final_repaired_path = repaired_path
        subtitle_merge_cmd = [
            self.config.tooling.ffmpeg_bin,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(plan.temp_path),
            "-i",
            str(media.path),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
        ]
        subtitle_args, _, subtitle_notes, _, _ = self._subtitle_args(
            media,
            decision,
            plan.target_path or plan.temp_path,
            workspace_dir=None,
            input_index=1,
            include_maps=True,
            ocr_input_start_index=2,
        )
        subtitle_merge_cmd.extend(["-c:v", "copy", "-tag:v", self.config.video.hevc_tag, "-c:a", "copy"])
        subtitle_merge_cmd.extend(subtitle_args)
        subtitle_merge_cmd.extend(["-map_metadata", "1"])
        if self.config.remux.keep_chapters:
            subtitle_merge_cmd.extend(["-map_chapters", "1"])
        else:
            subtitle_merge_cmd.extend(["-map_chapters", "-1"])
        subtitle_merge_cmd.extend(self._mp4_mux_args(media))
        subtitle_merge_cmd.append(str(repaired_path))

        steps = [
            CommandStep(
                name="dovi_subtitle_repair",
                command=subtitle_merge_cmd,
                expected_outputs=[repaired_path],
                cwd=plan.workspace_dir,
            )
        ]
        notes = list(subtitle_notes)
        cleanup_paths = [plan.temp_path]
        if dv_profile_arg := self._dovi_mp4box_profile_arg(media):
            final_repaired_path = repaired_path.with_name(
                f"{repaired_path.stem}.dv-patched{repaired_path.suffix}"
            )
            steps.append(
                CommandStep(
                    name="dovi_subtitle_repair_metadata_patch",
                    command=[
                        caps.mp4box_bin,
                        "-tmp",
                        str(plan.workspace_dir),
                        "-add",
                        f"self#video:dvp={dv_profile_arg}",
                        str(repaired_path),
                        "-out",
                        str(final_repaired_path),
                    ],
                    expected_outputs=[final_repaired_path],
                    cwd=plan.workspace_dir,
                )
            )
            notes.append(f"Reapplied Dolby Vision signaling after subtitle repair via MP4Box ({dv_profile_arg})")
            cleanup_paths.append(repaired_path)

        return steps, cleanup_paths, final_repaired_path, notes

    def build_dovi_subtitle_import_recovery_steps(
        self,
        media: MediaInfo,
        decision: Decision,
        plan: ExecutionPlan,
    ) -> tuple[list[CommandStep], list[Path], Path | None, list[str]]:
        if (
            plan.temp_path is None
            or plan.workspace_dir is None
            or plan.ocr_subtitle_tasks
            or not decision.use_dovi_muxer
            or not media.subtitle_streams
        ):
            return [], [], None, []

        caps = self.tooling.resolve_dolby_vision_mux_capabilities()
        if not caps.available or not caps.mp4muxer_bin:
            return [], [], None, []

        workspace_dir = plan.workspace_dir
        base_temp_path = plan.temp_path.with_name(f"{plan.temp_path.stem}.subtitleless-base{plan.temp_path.suffix}")
        cmd = [caps.dovi_muxer_bin, str(base_temp_path), "-i", str(media.path), "-ffmpeg", caps.ffmpeg_bin]
        cleanup_paths: list[Path] = []
        notes = ["Retried DoViMuxer base remux without embedded subtitles after subtitle import failure"]

        mp4muxer_wrapper = self._build_mp4muxer_wrapper(media, caps.mp4muxer_bin, workspace_dir)
        cleanup_paths.append(mp4muxer_wrapper)
        if caps.mp4box_bin:
            mp4box_wrapper = self._build_mp4box_wrapper(media, caps.mp4box_bin, workspace_dir)
            if mp4box_wrapper is not None:
                cmd.extend(["-mp4box", str(mp4box_wrapper)])
                cleanup_paths.append(mp4box_wrapper)
                notes.append("Trimmed overlong audio track(s) to source video duration on the subtitleless DV fallback path")
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
        cmd.append("-y")

        steps = [
            CommandStep(
                name="dovi_muxer_subtitleless_base",
                command=cmd,
                expected_outputs=[base_temp_path],
                cwd=workspace_dir,
            )
        ]

        repair_plan = ExecutionPlan(
            source_path=plan.source_path,
            target_path=plan.target_path,
            temp_path=base_temp_path,
            workspace_dir=workspace_dir,
            strategy=plan.strategy,
            case_label=plan.case_label,
            steps=[],
        )
        repair_steps, repair_cleanup_paths, repaired_path, repair_notes = self.build_dovi_subtitle_repair_steps(
            media,
            decision,
            repair_plan,
        )
        steps.extend(repair_steps)
        cleanup_paths.extend(repair_cleanup_paths)
        notes.extend(repair_notes)
        return steps, cleanup_paths, repaired_path, notes

    def _build_dovi_muxer_plan(
        self,
        media: MediaInfo,
        decision: Decision,
        source_root: Path | None,
        *,
        temp_root: Path | None = None,
        preferred_temp_root: Path | None = None,
    ) -> ExecutionPlan:
        caps = self.tooling.resolve_dolby_vision_mux_capabilities()
        if not caps.available or not caps.mp4muxer_bin:
            missing = ", ".join(sorted(caps.missing_tools)) or "unknown"
            raise RuntimeError(f"DoViMuxer toolchain unavailable: {missing}")

        target_path = self._build_target_path(media.path, source_root)
        selected_temp_root = temp_root or self._select_temp_root(media, decision)
        preferred_root = preferred_temp_root or self._preferred_temp_root(media.path)
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
        if selected_temp_root != preferred_root:
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
            subtitle_states = FFprobeAnalyzer.subtitle_track_states(media)
            for source_sub_index, stream in enumerate(media.subtitle_streams):
                state = subtitle_states[source_sub_index] if source_sub_index < len(subtitle_states) else None
                if self._is_empty_image_subtitle_stream(media, stream):
                    dropped_subtitle_streams.append(source_sub_index)
                    notes.append(f"Dropped empty image subtitle stream {source_sub_index} (no frames/bytes)")
                    continue
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
                lang = normalize_language_code(state.language if state else stream.language)
                cmd.extend(["-map", f"0:s:{source_sub_index}"])
                subtitle_title = self._subtitle_title_for_dovi(
                    (state.title if state else stream.title),
                    (state.hearing_impaired if state else stream.disposition.hearing_impaired),
                    (state.captions if state else stream.disposition.captions),
                )
                if meta := self._dovi_meta_arg("s", output_sub_index, lang, subtitle_title):
                    cmd.extend(["-meta", meta])
                if state.default if state else stream.disposition.default:
                    cmd.extend(["-default", f"s:{output_sub_index}"])
                if state.forced if state else stream.disposition.forced:
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
            subtitle_merge_cmd.extend(self._mp4_mux_args(media))
            subtitle_merge_cmd.append(str(temp_path))
            steps.append(
                CommandStep(
                    name="dovi_subtitle_merge",
                    command=subtitle_merge_cmd,
                    expected_outputs=[temp_path],
                    cwd=step_cwd,
                )
            )
            if dv_profile_arg := self._dovi_mp4box_profile_arg(media):
                patched_temp_path = temp_path.with_name(f"{temp_path.stem}.dv-patched{temp_path.suffix}")
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
                            "-out",
                            str(patched_temp_path),
                        ],
                        expected_outputs=[patched_temp_path],
                        cwd=step_cwd,
                    )
                )
                notes.append(f"Reapplied Dolby Vision signaling after subtitle merge via MP4Box ({dv_profile_arg})")
                cleanup_paths.append(temp_path)
                temp_path = patched_temp_path
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
        if not compatibility.requires_video_transcode and not decision.force_sdr:
            args = ["-c:v", "copy"]
            target_is_mp4 = self._target_suffix() == ".mp4"
            source = media.primary_video
            if target_is_mp4 and source and (source.codec_name or "").lower() == "hevc":
                args.extend(["-tag:v", self.config.video.hevc_tag])
            return args

        source = media.primary_video
        if source is None:
            return ["-c:v", "copy"]

        use_hevc = self._should_target_hevc(compatibility)
        source_cfr = _source_constant_frame_rate(media, source)
        fps = _fps(source_cfr or source.avg_frame_rate or source.r_frame_rate) or 24.0
        gop = max(24, int(math.ceil(fps * self.config.video.keyframe_interval_seconds)))

        args: list[str] = []
        if use_hevc:
            codec = self._selected_video_encoder("hevc")
            if codec == "hevc_videotoolbox":
                args.extend(self._videotoolbox_base_args(codec=codec, media=media, source=source))
                args.extend(["-tag:v", self.config.video.hevc_tag])
            else:
                args.extend(["-c:v", "libx265", "-preset", "medium", "-crf", "18", "-tag:v", self.config.video.hevc_tag])
                args.extend(self._video_thread_limit_args(codec="libx265"))
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
            codec = self._selected_video_encoder("h264")
            if codec == "h264_videotoolbox":
                args.extend(self._videotoolbox_base_args(codec=codec, media=media, source=source))
                args.extend(["-profile:v", "high", "-pix_fmt", "yuv420p"])
            else:
                args.extend(["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-profile:v", "high", "-pix_fmt", "yuv420p"])
                args.extend(self._video_thread_limit_args(codec="libx264"))
        args.extend(self._video_filter_args(source))
        args.extend(["-g", str(gop), "-keyint_min", str(gop)])

        if self.config.video.force_cfr or source_cfr is not None:
            args.extend(["-fps_mode", "cfr"])
            if source_cfr is not None:
                args.extend(["-r:v", source_cfr])

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

    def _selected_video_encoder(self, target_codec: str) -> str:
        if self._should_use_videotoolbox(target_codec):
            return f"{target_codec}_videotoolbox"
        if target_codec == "hevc":
            return "libx265"
        return "libx264"

    def _should_target_hevc(self, compatibility: CompatibilityDetails) -> bool:
        if self.config.video.preferred_codec != "hevc":
            return False

        policy = self.config.video.hardware_encoder.strip().lower()
        if policy not in {"auto", "videotoolbox"}:
            return True

        if self._should_use_videotoolbox("hevc"):
            return True

        preserve_hdr_pipeline = compatibility.hdr10_present or compatibility.dv_present
        if preserve_hdr_pipeline:
            return True

        if self.config.video.fallback_codec == "h264" and self._should_use_videotoolbox("h264"):
            return False

        return True

    def _should_use_videotoolbox(self, target_codec: str) -> bool:
        policy = self.config.video.hardware_encoder.strip().lower()
        if policy == "software":
            return False
        if platform.system() != "Darwin":
            return False
        encoder = f"{target_codec}_videotoolbox"
        return _ffmpeg_encoder_available(
            self.config.tooling.ffmpeg_bin,
            encoder,
        ) and _ffmpeg_videotoolbox_session_available(
            self.config.tooling.ffmpeg_bin,
            encoder,
        )

    def _videotoolbox_base_args(self, *, codec: str, media: MediaInfo, source: StreamInfo) -> list[str]:
        args = ["-c:v", codec]
        for option, value in (
            ("-allow_sw", "0"),
            ("-power_efficient", "1"),
            ("-spatial_aq", "1"),
        ):
            if _ffmpeg_encoder_option_available(self.config.tooling.ffmpeg_bin, codec, option):
                args.extend([option, value])
        args.extend(self._videotoolbox_bitrate_args(media, source))
        return args

    def _videotoolbox_bitrate_args(self, media: MediaInfo, source: StreamInfo) -> list[str]:
        target_kbps = self._videotoolbox_target_bitrate_kbps(media, source)
        maxrate_kbps = max(target_kbps, int(target_kbps * 1.5))
        bufsize_kbps = max(maxrate_kbps, int(target_kbps * 2.0))
        return [
            "-b:v",
            f"{target_kbps}k",
            "-maxrate",
            f"{maxrate_kbps}k",
            "-bufsize",
            f"{bufsize_kbps}k",
        ]

    def _videotoolbox_target_bitrate_kbps(self, media: MediaInfo, source: StreamInfo) -> int:
        source_bitrate = source.bit_rate or _estimated_video_bitrate(media, source)
        scaled_kbps = int((source_bitrate / 1000) * self.config.video.videotoolbox_bitrate_multiplier)
        return min(
            self.config.video.videotoolbox_max_bitrate_kbps,
            max(self.config.video.videotoolbox_min_bitrate_kbps, scaled_kbps),
        )

    def _video_thread_limit_args(self, *, codec: str) -> list[str]:
        threads = self.config.video.encoder_threads
        if threads <= 0:
            return []
        args = ["-threads:v", str(threads)]
        if codec == "libx265":
            x265_params = [f"pools={threads}", "frame-threads=1"]
            if threads == 1:
                x265_params.append("wpp=0")
            args.extend(["-x265-params", ":".join(x265_params)])
        return args

    def _video_filter_args(self, source: StreamInfo) -> list[str]:
        if not _is_interlaced(source):
            return []
        return ["-vf", "bwdif=mode=send_frame:parity=auto:deint=all,setfield=mode=prog"]

    def _mp4_mux_args(
        self,
        media: MediaInfo,
        compatibility: CompatibilityDetails | None = None,
    ) -> list[str]:
        args: list[str] = []
        dv_present = compatibility.dv_present if compatibility is not None else FFprobeAnalyzer.detect_dolby_vision(media)[0]
        if dv_present:
            args.extend(["-strict", "unofficial"])

        movflags = "+write_colr"
        if self.config.remux.faststart:
            movflags = f"{movflags}+faststart"
        args.extend(["-movflags", movflags])
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
        target_is_mp4 = self._target_suffix() == ".mp4"
        compatible_audio_indices = [
            source_audio_index
            for source_audio_index, stream in enumerate(media.audio_streams)
            if (stream.codec_name or "").lower() in SUPPORTED_AUDIO
        ]
        default_compatible_audio_index = next(
            (
                source_audio_index
                for source_audio_index in compatible_audio_indices
                if media.audio_streams[source_audio_index].disposition.default
            ),
            None,
        )
        preferred_default_audio_index = (
            default_compatible_audio_index
            if default_compatible_audio_index is not None
            else (compatible_audio_indices[0] if target_is_mp4 and compatible_audio_indices else None)
        )
        for source_audio_index, stream in enumerate(media.audio_streams):
            if stream.disposition.default:
                fallback_source_audio_index = source_audio_index
            codec = (stream.codec_name or "").lower()
            channels = stream.channels or 2
            if codec == "aac" and channels <= 2:
                has_aac_stereo = True
            if include_default_maps:
                args.extend(["-map", f"{input_index}:a:{source_audio_index}"])

        if target_is_mp4 and compatible_audio_indices:
            fallback_source_audio_index = (
                default_compatible_audio_index
                if default_compatible_audio_index is not None
                else compatible_audio_indices[0]
            )

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

            language = normalize_language_code(stream.language)
            args.extend([f"-metadata:s:a:{out_audio_index}", f"language={language}"])
            if stream.title:
                args.extend([f"-metadata:s:a:{out_audio_index}", f"title={stream.title}"])
            should_be_default = stream.disposition.default
            if preferred_default_audio_index is not None:
                should_be_default = out_audio_index == preferred_default_audio_index
            if should_be_default:
                args.extend([f"-disposition:a:{out_audio_index}", "default"])
            else:
                args.extend([f"-disposition:a:{out_audio_index}", "0"])

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

    def _should_add_aac_fallback(self, media: MediaInfo) -> bool:
        if self._target_suffix() != ".mp4" or not self.config.audio.ensure_aac_fallback_stereo_when_missing:
            return False
        return bool(media.audio_streams) and not any(
            (stream.codec_name or "").lower() == "aac" and (stream.channels or 2) <= 2
            for stream in media.audio_streams
        )

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
        subtitle_states = FFprobeAnalyzer.subtitle_track_states(media)
        for source_sub_index, stream in enumerate(media.subtitle_streams):
            state = subtitle_states[source_sub_index] if source_sub_index < len(subtitle_states) else None
            lang = normalize_language_code(state.language if state else stream.language)
            if self._is_empty_image_subtitle_stream(media, stream):
                dropped_subtitle_streams.append(source_sub_index)
                notes.append(f"Dropped empty image subtitle stream {source_sub_index} (no frames/bytes)")
                continue
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
                        (state.title if state else stream.title),
                        (state.hearing_impaired if state else stream.disposition.hearing_impaired),
                        (state.captions if state else stream.disposition.captions),
                    )
                    if subtitle_title:
                        args.extend([f"-metadata:s:s:{output_sub_index}", f"title={subtitle_title}"])
                    args.extend(
                        [
                            f"-disposition:s:{output_sub_index}",
                            self._subtitle_disposition_value(stream, state=state),
                        ]
                    )
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
            subtitle_title = self._subtitle_title_for_dovi(
                (state.title if state else stream.title),
                (state.hearing_impaired if state else stream.disposition.hearing_impaired),
                (state.captions if state else stream.disposition.captions),
            )
            if subtitle_title:
                args.extend([f"-metadata:s:s:{output_sub_index}", f"title={subtitle_title}"])
            args.extend(
                [
                    f"-disposition:s:{output_sub_index}",
                    self._subtitle_disposition_value(stream, state=state),
                ]
            )
            output_sub_index += 1

        return args, exports, notes, dropped_subtitle_streams, ocr_subtitle_tasks

    def _attachment_exclusion_args(self, media: MediaInfo) -> list[str]:
        args = ["-map", "-0:t"]
        for stream in media.streams:
            if stream.is_attached_picture:
                args.extend(["-map", f"-0:{stream.index}"])
        return args

    def _ensure_apple_native_mp4_subtitles(self, media: MediaInfo) -> None:
        if self._target_suffix() != ".mp4":
            return
        for source_sub_index, stream in enumerate(media.subtitle_streams):
            if self._is_empty_image_subtitle_stream(media, stream):
                continue
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
        return any(
            stream.is_image_subtitle and not self._is_empty_image_subtitle_stream(media, stream)
            for stream in media.subtitle_streams
        )

    def _build_ocr_subtitle_tasks(self, media: MediaInfo, workspace_dir: Path) -> list[OcrSubtitleTask]:
        tasks: list[OcrSubtitleTask] = []
        if not self.config.subtitles.ocr_image_subtitles:
            return tasks
        for source_sub_index, stream in enumerate(media.subtitle_streams):
            if stream.is_image_subtitle and not self._is_empty_image_subtitle_stream(media, stream):
                tasks.append(self._build_ocr_subtitle_task(stream, source_sub_index, workspace_dir))
        return tasks

    def _is_empty_image_subtitle_stream(self, media: MediaInfo, stream) -> bool:
        if not stream.is_image_subtitle:
            return False
        tags = self._probe_stream_tags(media, stream)
        frame_count = self._probe_stat_int(tags, "NUMBER_OF_FRAMES")
        byte_count = self._probe_stat_int(tags, "NUMBER_OF_BYTES")
        duration_seconds = self._probe_stat_duration(tags, "DURATION")
        return (
            (frame_count == 0 and byte_count == 0)
            or (frame_count == 0 and duration_seconds == 0.0)
            or (byte_count == 0 and duration_seconds == 0.0)
        )

    @staticmethod
    def _probe_stream_tags(media: MediaInfo, stream) -> dict:
        for raw_stream in media.raw_probe.get("streams", []) or []:
            try:
                if int(raw_stream.get("index", -1)) == stream.index:
                    return raw_stream.get("tags", {}) or {}
            except (TypeError, ValueError):
                continue
        return {}

    @staticmethod
    def _probe_stat_int(tags: dict, key: str) -> int | None:
        value = tags.get(key)
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None

    @staticmethod
    def _probe_stat_duration(tags: dict, key: str) -> float | None:
        value = tags.get(key)
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        hms = text.split(".", 1)[0]
        parts = hms.split(":")
        if len(parts) != 3:
            return None
        try:
            hours, minutes, seconds = (float(part) for part in parts)
        except ValueError:
            return None
        return (hours * 3600.0) + (minutes * 60.0) + seconds

    def _build_ocr_subtitle_task(
        self,
        stream,
        source_sub_index: int,
        workspace_dir: Path,
    ) -> OcrSubtitleTask:
        lang = normalize_language_code(stream.language)
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

        output_root = self.config.output_root_for(source, source_root)
        relative = source.name
        if source_root:
            try:
                relative = str(source.relative_to(source_root))
            except ValueError:
                relative = source.name
        rel_path = Path(relative).with_suffix(suffix)
        return (output_root / rel_path).resolve()

    def _build_temp_path(self, source: Path, target_path: Path, *, hidden: bool = True, root: Path | None = None) -> Path:
        token = uuid.uuid4().hex[:10]
        prefix = "." if hidden else ""
        temp_name = f"{prefix}{source.stem}.{token}.tmp{target_path.suffix}"
        temp_root = self._normalize_temp_root(root, source=source)
        ensure_dir(temp_root)
        return (temp_root / temp_name).resolve()

    def _build_intermediate_path(self, source: Path, label: str, suffix: str) -> Path:
        token = uuid.uuid4().hex[:10]
        file_name = f".{source.stem}.{token}.{label}{suffix}"
        temp_root = self._normalize_temp_root(None, source=source)
        ensure_dir(temp_root)
        return (temp_root / file_name).resolve()

    def _build_workspace_dir(self, source: Path, label: str, *, root: Path | None = None) -> Path:
        token = uuid.uuid4().hex[:10]
        temp_root = self._normalize_temp_root(root, source=source)
        workspace_dir = (temp_root / f".{source.stem}.{token}.{label}").resolve()
        ensure_dir(workspace_dir)
        return workspace_dir

    def _select_temp_root(self, media: MediaInfo, decision: Decision) -> Path:
        required_bytes = self._estimated_temp_requirement(
            media.size,
            use_dovi_muxer=decision.use_dovi_muxer,
            needs_subtitle_ocr=self._needs_subtitle_ocr(media),
        )
        fallback_root: Path | None = None
        for candidate in self._temp_root_candidates(media.path):
            try:
                ensure_dir(candidate)
            except OSError:
                continue
            fallback_root = fallback_root or candidate
            try:
                free_bytes = shutil.disk_usage(candidate).free
            except OSError:
                continue
            if free_bytes >= required_bytes:
                return candidate
        if fallback_root is not None:
            return fallback_root
        preferred_root = self._preferred_temp_root(media.path)
        ensure_dir(preferred_root)
        return preferred_root

    def _preferred_temp_root(self, source: Path) -> Path:
        configured_temp_root = self._configured_temp_root(source)
        if self.config.paths.temp_dir_strategy == "configured_first":
            return configured_temp_root

        source_parent = source.expanduser().parent
        if source_parent.exists():
            return self._source_temp_root(source)
        return configured_temp_root

    def _source_temp_root(self, source: Path) -> Path:
        return (source.expanduser().parent / RUNTIME_TEMP_DIRNAME).resolve()

    def _temp_root_candidates(self, source: Path) -> list[Path]:
        candidates: list[Path] = []
        configured_temp_root = self._configured_temp_root(source)
        source_parent = source.expanduser().parent
        source_temp_root = self._source_temp_root(source) if source_parent.exists() else None

        def append(candidate: Path | None) -> None:
            if candidate is None:
                return
            if candidate not in candidates:
                candidates.append(candidate)

        if self.config.paths.temp_dir_strategy == "configured_first":
            append(configured_temp_root)
            append(source_temp_root)
        else:
            append(source_temp_root)
            append(configured_temp_root)

        if self.config.output.mode != "replace_original":
            output_temp_root = (self.config.output_root_for(source) / RUNTIME_TEMP_DIRNAME).expanduser().resolve()
            append(output_temp_root)
        return candidates

    def _configured_temp_root(self, source: Path) -> Path:
        return self.config.temp_dir_for(source)

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

    def _normalize_temp_root(self, root: Path | None, *, source: Path | None = None) -> Path:
        if root is not None:
            return root.expanduser().resolve()
        if source is not None:
            return self._preferred_temp_root(source)
        return self.config.paths.temp_dir.expanduser().resolve()

    def _dovi_mp4box_profile_arg(self, media: MediaInfo) -> str | None:
        dv = FFprobeAnalyzer.inspect_dolby_vision(media)
        if not dv.present or not dv.profile:
            return None
        normalized = dv.profile.strip().lower()
        if not normalized:
            return None
        if normalized.startswith("f"):
            return normalized
        return f"f{normalized}"

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
            parts.append(f"lang={normalize_language_code(language)}")
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
    def _subtitle_disposition_value(stream, *, state=None) -> str:
        values: list[str] = []
        default = state.default if state is not None else stream.disposition.default
        forced = state.forced if state is not None else stream.disposition.forced
        hearing_impaired = state.hearing_impaired if state is not None else stream.disposition.hearing_impaired
        captions = state.captions if state is not None else stream.disposition.captions
        title = state.title if state is not None else stream.title
        if default:
            values.append("default")
        if forced:
            values.append("forced")
        if hearing_impaired:
            values.append("hearing_impaired")
        if captions:
            values.append("captions")
        if not values and title and "sdh" in title.lower():
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


def _source_constant_frame_rate(media: MediaInfo, source: StreamInfo) -> str | None:
    if _is_interlaced(source):
        return None
    avg = _fps(source.avg_frame_rate)
    real = _fps(source.r_frame_rate)
    if avg is None:
        return None
    if real is not None and abs(avg - real) <= max(0.01, real * 0.001):
        return source.avg_frame_rate or source.r_frame_rate
    if _legacy_avi_timeline_matches_frame_rate(media, source, avg):
        return source.avg_frame_rate
    return None


def _legacy_avi_timeline_matches_frame_rate(media: MediaInfo, source: StreamInfo, avg_fps: float) -> bool:
    if "avi" not in media.container_names:
        return False
    codec = (source.codec_name or "").lower()
    if codec not in {"mpeg4", "msmpeg4v1", "msmpeg4v2", "msmpeg4v3", "mpeg2video", "h263", "dvvideo"}:
        return False
    if source.time_base and not _time_base_matches_fps(source.time_base, avg_fps):
        return False
    if source.nb_frames is None or source.duration is None or source.duration <= 0:
        return False
    observed_fps = source.nb_frames / source.duration
    return abs(observed_fps - avg_fps) <= max(0.05, avg_fps * 0.002)


def _time_base_matches_fps(value: str, fps: float) -> bool:
    if "/" not in value:
        return False
    left, right = value.split("/", 1)
    try:
        numerator = float(left)
        denominator = float(right)
    except ValueError:
        return False
    if numerator <= 0 or denominator <= 0:
        return False
    implied_fps = denominator / numerator
    if implied_fps < 1:
        return False
    return abs(implied_fps - fps) <= max(0.05, fps * 0.002)


def _estimated_video_bitrate(media: MediaInfo, source: StreamInfo) -> int:
    if media.bit_rate:
        audio_bitrate = sum(stream.bit_rate or 0 for stream in media.audio_streams)
        container_bitrate = max(0, media.bit_rate - audio_bitrate)
        if container_bitrate:
            return container_bitrate

    width = source.width or 1920
    height = source.height or 1080
    pixels = width * height
    if pixels >= 3840 * 1600:
        return 24_000_000
    if pixels >= 1920 * 1000:
        return 8_000_000
    if pixels >= 1280 * 700:
        return 5_000_000
    return 2_500_000


@lru_cache(maxsize=32)
def _ffmpeg_encoder_available(ffmpeg_bin: str, encoder: str) -> bool:
    try:
        completed = subprocess.run(
            [ffmpeg_bin, "-hide_banner", "-encoders"],
            check=False,
            text=True,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return encoder in completed.stdout


@lru_cache(maxsize=64)
def _ffmpeg_encoder_options(ffmpeg_bin: str, encoder: str) -> str:
    try:
        completed = subprocess.run(
            [ffmpeg_bin, "-hide_banner", "-h", f"encoder={encoder}"],
            check=False,
            text=True,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout


def _ffmpeg_encoder_option_available(ffmpeg_bin: str, encoder: str, option: str) -> bool:
    return option in _ffmpeg_encoder_options(ffmpeg_bin, encoder)


@lru_cache(maxsize=32)
def _ffmpeg_videotoolbox_session_available(ffmpeg_bin: str, encoder: str) -> bool:
    if not encoder.endswith("_videotoolbox"):
        return False
    try:
        completed = subprocess.run(
            [
                ffmpeg_bin,
                "-hide_banner",
                "-nostdin",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=640x360:rate=24:duration=0.25",
                "-frames:v",
                "1",
                "-c:v",
                encoder,
                "-allow_sw",
                "0",
                "-b:v",
                "1000k",
                "-f",
                "null",
                "-",
            ],
            check=False,
            text=True,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _is_interlaced(source: StreamInfo) -> bool:
    field_order = (source.field_order or "").lower()
    return bool(field_order and field_order not in {"unknown", "progressive"})


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
