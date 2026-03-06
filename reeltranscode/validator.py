from __future__ import annotations

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

        source_dv, source_dv_profile = FFprobeAnalyzer.detect_dolby_vision(source)
        output_dv, output_dv_profile = FFprobeAnalyzer.detect_dolby_vision(output)
        if self.config.validation.require_dv_preservation and source_dv:
            if not output_dv:
                reasons.append("Dolby Vision lost: source contains DV but output does not advertise DV metadata")
            elif source_dv_profile and output_dv_profile and source_dv_profile != output_dv_profile:
                reasons.append(
                    f"Dolby Vision profile changed: source={source_dv_profile}, output={output_dv_profile}"
                )

        if decision.preserve_hdr10 and FFprobeAnalyzer.detect_hdr10(source) and not FFprobeAnalyzer.detect_hdr10(output):
            reasons.append("HDR10 signaling lost while HDR preservation is required by decision policy")

        if output.duration is not None and source.duration is not None:
            delta = abs(output.duration - source.duration)
            if delta > self.config.validation.verify_duration_tolerance_seconds:
                reasons.append(f"Duration delta too high: {delta:.2f}s")

        externalized_subtitles = len(plan.external_subtitle_outputs) if plan else 0
        expected_output_stream_count = max(0, len(source.streams) - externalized_subtitles)
        stream_delta = abs(expected_output_stream_count - len(output.streams))
        if stream_delta > self.config.validation.verify_stream_count_delta_max:
            reasons.append(f"Unexpected stream count delta: {stream_delta}")

        return ValidationResult(ok=not reasons, reasons=reasons)
