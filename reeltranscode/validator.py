from __future__ import annotations

from reeltranscode.analyzer import FFprobeAnalyzer
from reeltranscode.config import AppConfig
from reeltranscode.models import Decision, MediaInfo, ValidationResult


class OutputValidator:
    def __init__(self, config: AppConfig):
        self.config = config

    def validate(self, source: MediaInfo, output: MediaInfo, decision: Decision) -> ValidationResult:
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

        if output.duration is not None and source.duration is not None:
            delta = abs(output.duration - source.duration)
            if delta > self.config.validation.verify_duration_tolerance_seconds:
                reasons.append(f"Duration delta too high: {delta:.2f}s")

        stream_delta = abs(len(source.streams) - len(output.streams))
        if stream_delta > self.config.validation.verify_stream_count_delta_max:
            reasons.append(f"Unexpected stream count delta: {stream_delta}")

        return ValidationResult(ok=not reasons, reasons=reasons)
