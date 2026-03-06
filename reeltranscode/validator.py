from __future__ import annotations

import re

from reeltranscode.analyzer import FFprobeAnalyzer
from reeltranscode.config import AppConfig
from reeltranscode.models import Decision, ExecutionPlan, MediaInfo, ValidationResult


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
            subtitle_reasons, subtitle_notes = self._validate_mp4_subtitles(source, output)
            reasons.extend(subtitle_reasons)
            notes.extend(subtitle_notes)

        if output.duration is not None and source.duration is not None:
            delta = abs(output.duration - source.duration)
            if delta > self.config.validation.verify_duration_tolerance_seconds:
                reasons.append(f"Duration delta too high: {delta:.2f}s")

        externalized_subtitles = len(plan.external_subtitle_outputs) if plan else 0
        expected_output_stream_count = max(0, len(source.streams) - externalized_subtitles)
        stream_delta = abs(expected_output_stream_count - len(output.streams))
        if stream_delta > self.config.validation.verify_stream_count_delta_max:
            reasons.append(f"Unexpected stream count delta: {stream_delta}")

        return ValidationResult(ok=not reasons, reasons=reasons, notes=notes)

    def _validate_mp4_subtitles(self, source: MediaInfo, output: MediaInfo) -> tuple[list[str], list[str]]:
        source_tracks = FFprobeAnalyzer.subtitle_track_states(source)
        output_tracks = FFprobeAnalyzer.subtitle_track_states(output)
        if not source_tracks:
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
            if source_track.language != output_track.language:
                reasons.append(
                    f"Subtitle track {index} language changed: "
                    f"source={source_track.language or 'und'}, output={output_track.language or 'und'}"
                )

            source_title = _normalize_title(source_track.title)
            output_title = _normalize_title(output_track.title)
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

            source_hi = source_track.hearing_impaired or source_track.captions or _title_implies_hi(source_track.title)
            output_hi = output_track.hearing_impaired or output_track.captions or _title_implies_hi(output_track.title)
            if source_hi != output_hi:
                reasons.append(
                    f"Subtitle track {index} hearing-impaired/captions marker changed: "
                    f"source={source_hi}, output={output_hi}"
                )

        if reasons:
            return reasons, []
        return [], [f"Subtitle validation passed: {len(output_tracks)} mov_text tracks preserved"]


def _dv_description(info) -> str:
    if info.profile:
        return f"{info.source or 'unknown'} (profile {info.profile})"
    return info.source or "unknown"


def _normalize_title(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"\s+", " ", value.strip()).casefold()
    return normalized or None


def _title_implies_hi(value: str | None) -> bool:
    if not value:
        return False
    text = value.casefold()
    return any(token in text for token in ["sdh", "hearing impaired", "closed captions", "cc"])
