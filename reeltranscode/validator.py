from __future__ import annotations

import re

from reeltranscode.analyzer import FFprobeAnalyzer
from reeltranscode.config import AppConfig
from reeltranscode.models import Decision, ExecutionPlan, MediaInfo, ValidationResult

START_TIME_TOLERANCE_SECONDS = 0.25


class OutputValidator:
    def __init__(self, config: AppConfig):
        self.config = config

    def validate(
        self,
        source: MediaInfo,
        output: MediaInfo,
        decision: Decision,
        plan: ExecutionPlan | None = None,
    ) -> ValidationResult:
        reasons: list[str] = []
        notes: list[str] = []

        if self.config.remux.preferred_container == "mp4":
            if FFprobeAnalyzer.is_container_apple_compatible(output) is False:
                reasons.append("Output container is not Apple-compatible")

        video_ok, video_reasons = FFprobeAnalyzer.is_video_apple_compatible(output, self.config.video.max_4k_fps)
        if not video_ok:
            reasons.extend(video_reasons)

        audio_ok, audio_reasons = FFprobeAnalyzer.is_audio_apple_compatible(output)
        if not audio_ok:
            reasons.extend(audio_reasons)

        if self.config.remux.preferred_container == "mp4":
            video = output.primary_video
            if video and (video.codec_name or "").lower() == "hevc":
                codec_tag = (video.codec_tag_string or "").lower()
                expected = self.config.video.hevc_tag.lower()
                if codec_tag != expected:
                    reasons.append(f"HEVC codec tag mismatch: expected {expected}, got {codec_tag or 'unknown'}")

        source_dv = FFprobeAnalyzer.inspect_dolby_vision(source)
        output_dv = FFprobeAnalyzer.inspect_dolby_vision(output)
        if self.config.validation.require_dv_preservation and source_dv.present:
            source_desc = _dv_description(source_dv)
            if not output_dv.present:
                brand_note = "; dby1 compatible brand ignored as non-proof" if output_dv.brand_hint else ""
                reasons.append(
                    "Dolby Vision lost: source proven "
                    f"via {source_desc} but output has no explicit Dolby Vision proof{brand_note}"
                )
            elif source_dv.profile and output_dv.profile and source_dv.profile != output_dv.profile:
                reasons.append(
                    "Dolby Vision profile changed: "
                    f"source={source_dv.profile}, output={output_dv.profile}"
                )
            else:
                notes.append(f"Dolby Vision preserved via {_dv_description(output_dv)}")

        if decision.preserve_hdr10 and FFprobeAnalyzer.detect_hdr10(source) and not FFprobeAnalyzer.detect_hdr10(output):
            reasons.append("HDR10 signaling lost while HDR preservation is required by decision policy")

        if self.config.remux.preferred_container == "mp4":
            dropped_subtitle_streams = list(plan.dropped_subtitle_streams) if plan else []
            subtitle_reasons, subtitle_notes = self._validate_mp4_subtitles(
                source,
                output,
                dropped_subtitle_streams=dropped_subtitle_streams,
            )
            reasons.extend(subtitle_reasons)
            notes.extend(subtitle_notes)

        expected_output_duration = _expected_output_duration(source)
        if output.duration is not None and expected_output_duration is not None:
            delta = abs(output.duration - expected_output_duration)
            if delta > self.config.validation.verify_duration_tolerance_seconds:
                reasons.append(f"Duration delta too high: {delta:.2f}s")

        reasons.extend(self._validate_video_timing(source, output))

        externalized_subtitles = len(plan.external_subtitle_outputs) if plan else 0
        dropped_subtitles = len(plan.dropped_subtitle_streams) if plan else 0
        expected_output_stream_count = max(0, len(source.streams) - externalized_subtitles - dropped_subtitles)
        stream_delta = abs(expected_output_stream_count - len(output.streams))
        if stream_delta > self.config.validation.verify_stream_count_delta_max:
            reasons.append(f"Unexpected stream count delta: {stream_delta}")

        return ValidationResult(ok=not reasons, reasons=reasons, notes=notes)

    def _validate_mp4_subtitles(
        self,
        source: MediaInfo,
        output: MediaInfo,
        *,
        dropped_subtitle_streams: list[int] | None = None,
    ) -> tuple[list[str], list[str]]:
        source_tracks = FFprobeAnalyzer.subtitle_track_states(source)
        output_tracks = FFprobeAnalyzer.subtitle_track_states(output)
        dropped = set(dropped_subtitle_streams or [])
        source_tracks = [track for index, track in enumerate(source_tracks) if index not in dropped]
        if not source_tracks:
            if dropped:
                return [], [f"Dropped {len(dropped)} incompatible image subtitle track(s) for Apple-native MP4 output"]
            return [], []

        reasons: list[str] = []

        if len(output_tracks) != len(source_tracks):
            reasons.append(
                "Subtitle track count mismatch after MP4 conversion: "
                f"source={len(source_tracks)}, output={len(output_tracks)}"
            )
            return reasons, []

        for index, output_track in enumerate(output_tracks):
            codec = (output_track.codec_name or "").lower()
            if codec not in {"mov_text"}:
                reasons.append(
                    f"Subtitle track {index} is not Apple-native mov_text/tx3g: {output_track.codec_name or 'unknown'}"
                )

        for index, (source_track, output_track) in enumerate(zip(source_tracks, output_tracks, strict=True)):
            source_hi = source_track.hearing_impaired or source_track.captions or _title_implies_hi(source_track.title)
            output_hi = output_track.hearing_impaired or output_track.captions or _title_implies_hi(output_track.title)

            if source_track.language != output_track.language:
                reasons.append(
                    f"Subtitle track {index} language changed: "
                    f"source={source_track.language or 'und'}, output={output_track.language or 'und'}"
                )

            source_title = _normalize_subtitle_title(source_track.title, source_hi)
            output_title = _normalize_subtitle_title(output_track.title, output_hi)
            if source_title != output_title:
                reasons.append(
                    f"Subtitle track {index} title changed: "
                    f"source={source_track.title or '-'}, output={output_track.title or '-'}"
                )

            if source_track.default != output_track.default:
                reasons.append(
                    f"Subtitle track {index} default flag changed: "
                    f"source={source_track.default}, output={output_track.default}"
                )
            if source_track.forced != output_track.forced:
                reasons.append(
                    f"Subtitle track {index} forced flag changed: "
                    f"source={source_track.forced}, output={output_track.forced}"
                )

            if source_hi != output_hi:
                reasons.append(
                    f"Subtitle track {index} hearing-impaired/captions marker changed: "
                    f"source={source_hi}, output={output_hi}"
                )

        if reasons:
            return reasons, []

        notes = [f"Subtitle validation passed: {len(output_tracks)} mov_text tracks preserved"]
        if dropped:
            notes.append(f"Dropped {len(dropped)} incompatible image subtitle track(s) for Apple-native MP4 output")
        return [], notes

    def _validate_video_timing(self, source: MediaInfo, output: MediaInfo) -> list[str]:
        source_video = source.primary_video
        output_video = output.primary_video
        if source_video is None or output_video is None:
            return []

        reasons: list[str] = []
        tolerance = self.config.validation.verify_duration_tolerance_seconds

        source_fps = _frame_rate_to_float(source_video.avg_frame_rate or source_video.r_frame_rate)
        output_fps = _frame_rate_to_float(output_video.avg_frame_rate or output_video.r_frame_rate)
        if source_fps is not None and output_fps is not None:
            fps_delta = abs(source_fps - output_fps)
            allowed_delta = max(0.05, source_fps * 0.005)
            if fps_delta > allowed_delta:
                reasons.append(
                    "Video frame rate changed unexpectedly: "
                    f"source={source_fps:.3f}fps, output={output_fps:.3f}fps"
                )

        source_video_duration = source_video.duration or source.duration
        output_video_duration = output_video.duration or output.duration
        if source_video_duration is not None and output_video_duration is not None:
            delta = abs(source_video_duration - output_video_duration)
            if delta > tolerance:
                reasons.append(
                    "Video duration changed unexpectedly: "
                    f"source={source_video_duration:.2f}s, output={output_video_duration:.2f}s"
                )

        if output.duration is not None and output_video_duration is not None:
            delta = abs(output.duration - output_video_duration)
            if delta > tolerance:
                reasons.append(
                    "Output video duration does not match container duration: "
                    f"video={output_video_duration:.2f}s, container={output.duration:.2f}s"
                )

        for audio_index, audio_stream in enumerate(output.audio_streams):
            if audio_stream.duration is None or output_video_duration is None:
                continue
            delta = abs(audio_stream.duration - output_video_duration)
            if delta > tolerance:
                reasons.append(
                    "Output audio/video duration mismatch: "
                    f"track={audio_index}, video={output_video_duration:.2f}s, audio={audio_stream.duration:.2f}s"
                )

        reasons.extend(self._validate_stream_sync(source, output))

        return reasons

    def _validate_stream_sync(self, source: MediaInfo, output: MediaInfo) -> list[str]:
        source_video = source.primary_video
        output_video = output.primary_video
        if source_video is None or output_video is None:
            return []

        reasons: list[str] = []
        source_video_start = source_video.start_time
        output_video_start = output_video.start_time

        if source_video_start is not None and output_video_start is not None:
            delta = abs(output_video_start - source_video_start)
            if delta > START_TIME_TOLERANCE_SECONDS:
                reasons.append(
                    "Video start time changed unexpectedly: "
                    f"source={source_video_start:.3f}s, output={output_video_start:.3f}s"
                )

        for audio_index, output_audio in enumerate(output.audio_streams):
            output_audio_start = output_audio.start_time
            if output_audio_start is None or output_video_start is None:
                continue

            source_audio = source.audio_streams[audio_index] if audio_index < len(source.audio_streams) else None
            source_audio_start = source_audio.start_time if source_audio else None

            if source_audio_start is not None and source_video_start is not None:
                source_offset = source_audio_start - source_video_start
                output_offset = output_audio_start - output_video_start
                delta = abs(output_offset - source_offset)
                if delta > START_TIME_TOLERANCE_SECONDS:
                    reasons.append(
                        "Output audio/video start offset changed unexpectedly: "
                        f"track={audio_index}, source={source_offset:.3f}s, output={output_offset:.3f}s"
                    )
                continue

            delta = abs(output_audio_start - output_video_start)
            if delta > START_TIME_TOLERANCE_SECONDS:
                reasons.append(
                    "Output audio/video start time mismatch: "
                    f"track={audio_index}, video={output_video_start:.3f}s, audio={output_audio_start:.3f}s"
                )

        return reasons


def _dv_description(info) -> str:
    if info.profile:
        return f"{info.source or 'unknown'} (profile {info.profile})"
    return info.source or "unknown"


def _normalize_title(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"\s+", " ", value.strip()).casefold()
    return normalized or None


def _normalize_subtitle_title(value: str | None, hi_marker: bool) -> str | None:
    normalized = _normalize_title(value)
    if normalized is None or not hi_marker:
        return normalized
    normalized = re.sub(r"\b(?:sdh|hearing impaired|closed captions|cc)\b", "", normalized)
    normalized = re.sub(r"[\(\)\[\]\-_:]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized or None


def _title_implies_hi(value: str | None) -> bool:
    if not value:
        return False
    text = value.casefold()
    return any(token in text for token in ["sdh", "hearing impaired", "closed captions", "cc"])


def _frame_rate_to_float(value: str | None) -> float | None:
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


def _expected_output_duration(source: MediaInfo) -> float | None:
    source_video = source.primary_video
    if source_video and source_video.duration is not None:
        return source_video.duration
    return source.duration
