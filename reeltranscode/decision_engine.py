from __future__ import annotations

from reeltranscode.analyzer import FFprobeAnalyzer
from reeltranscode.config import AppConfig
from reeltranscode.models import CaseLabel, CompatibilityDetails, Decision, MediaInfo, Strategy


class DecisionEngine:
    def __init__(self, config: AppConfig):
        self.config = config

    def evaluate_compatibility(self, media: MediaInfo) -> CompatibilityDetails:
        container_ok = FFprobeAnalyzer.is_container_apple_compatible(media)
        video_ok, video_reasons = FFprobeAnalyzer.is_video_apple_compatible(media, self.config.video.max_4k_fps)
        audio_ok, audio_reasons = FFprobeAnalyzer.is_audio_apple_compatible(media)
        subtitle_ok, subtitle_reasons = FFprobeAnalyzer.mp4_subtitle_compatible(media)
        dv_present, dv_profile = FFprobeAnalyzer.detect_dolby_vision(media)
        hdr10_present = FFprobeAnalyzer.detect_hdr10(media)

        reasons: list[str] = []
        reasons.extend(video_reasons)
        reasons.extend(audio_reasons)
        reasons.extend(subtitle_reasons)

        requires_hevc_retag = self._requires_hevc_retag(media)
        if requires_hevc_retag:
            reasons.append(
                f"HEVC stream tag is not {self.config.video.hevc_tag}; MP4 retag/remux required for Apple clients"
            )

        requires_container_change = (
            (not container_ok and self.config.remux.preferred_container == "mp4")
            or requires_hevc_retag
        )
        requires_audio_fix = not audio_ok

        # Subtitle fix is relevant only if final target is MP4.
        target_is_mp4 = self.config.remux.preferred_container == "mp4"
        requires_subtitle_fix = target_is_mp4 and not subtitle_ok and bool(media.subtitle_streams)
        requires_video_transcode = not video_ok

        return CompatibilityDetails(
            container_ok=container_ok,
            video_ok=video_ok,
            audio_ok=audio_ok,
            subtitle_ok=subtitle_ok,
            dv_present=dv_present,
            dv_profile=dv_profile,
            hdr10_present=hdr10_present,
            requires_container_change=requires_container_change,
            requires_audio_fix=requires_audio_fix,
            requires_subtitle_fix=requires_subtitle_fix,
            requires_video_transcode=requires_video_transcode,
            reasons=reasons,
        )

    def _requires_hevc_retag(self, media: MediaInfo) -> bool:
        target_is_mp4 = self.config.remux.preferred_container == "mp4"
        video = media.primary_video
        if not target_is_mp4 or video is None:
            return False
        if (video.codec_name or "").lower() != "hevc":
            return False
        current_tag = (video.codec_tag_string or "").lower()
        desired_tag = self.config.video.hevc_tag.lower()
        return current_tag != desired_tag

    def decide(self, media: MediaInfo) -> tuple[Decision, CompatibilityDetails]:
        comp = self.evaluate_compatibility(media)
        reasons = list(comp.reasons)
        expected_container = self.config.remux.preferred_container

        dv_fragile = False
        if comp.dv_present:
            profile = comp.dv_profile or "unknown"
            safe_profile = profile in self.config.dolby_vision.safe_profiles
            container_switch = comp.requires_container_change
            if not safe_profile or (container_switch and not self.config.dolby_vision.remux_dv_from_mkv_to_mp4_is_safe):
                dv_fragile = True
                reasons.append(
                    "Dolby Vision stream detected but preservation path is fragile for target container"
                )

        # CASE F gets priority due to explicit fallback policy and risk of silent HDR loss.
        if dv_fragile:
            fallback = self.config.dolby_vision.fragile_fallback
            if fallback == "preserve_hdr10" and comp.hdr10_present:
                strategy = Strategy.REMUX_ONLY if not comp.requires_video_transcode else Strategy.VIDEO_ONLY
                return (
                    Decision(
                        strategy=strategy,
                        case_label=CaseLabel.F,
                        reasons=reasons,
                        expected_container=expected_container,
                        expected_direct_play_safe=True,
                        dv_fallback_applied=True,
                        dv_fallback_reason="DV not guaranteed; preserving HDR10-compatible path",
                        force_sdr=False,
                        preserve_hdr10=True,
                    ),
                    comp,
                )
            # fallback to SDR as last resort when no robust HDR path remains.
            return (
                Decision(
                    strategy=Strategy.VIDEO_ONLY,
                    case_label=CaseLabel.F,
                    reasons=reasons,
                    expected_container=expected_container,
                    expected_direct_play_safe=True,
                    dv_fallback_applied=True,
                    dv_fallback_reason="DV/HDR path fragile; forcing SDR video transcode fallback",
                    force_sdr=True,
                    preserve_hdr10=False,
                ),
                comp,
            )

        # CASE A
        if not any(
            [
                comp.requires_container_change,
                comp.requires_audio_fix,
                comp.requires_subtitle_fix,
                comp.requires_video_transcode,
            ]
        ):
            return (
                Decision(
                    strategy=Strategy.NO_OP,
                    case_label=CaseLabel.A,
                    reasons=["Already Apple Direct Play compatible"],
                    expected_container=media.format_name,
                    expected_direct_play_safe=True,
                ),
                comp,
            )

        # CASE E
        if comp.requires_video_transcode:
            strategy = Strategy.VIDEO_ONLY
            if comp.requires_audio_fix or comp.requires_subtitle_fix:
                strategy = Strategy.FULL_PIPELINE
            return (
                Decision(
                    strategy=strategy,
                    case_label=CaseLabel.E,
                    reasons=reasons,
                    expected_container=expected_container,
                    expected_direct_play_safe=True,
                ),
                comp,
            )

        # CASE B
        if comp.requires_container_change and not comp.requires_audio_fix and not comp.requires_subtitle_fix:
            return (
                Decision(
                    strategy=Strategy.REMUX_ONLY,
                    case_label=CaseLabel.B,
                    reasons=reasons or ["Container conversion required; all streams compatible"],
                    expected_container=expected_container,
                    expected_direct_play_safe=True,
                ),
                comp,
            )

        # CASE C (audio is prioritized over subtitle-only if both exist)
        if comp.requires_audio_fix:
            strategy = Strategy.AUDIO_ONLY
            if comp.requires_subtitle_fix:
                strategy = Strategy.FULL_PIPELINE
            return (
                Decision(
                    strategy=strategy,
                    case_label=CaseLabel.C,
                    reasons=reasons,
                    expected_container=expected_container,
                    expected_direct_play_safe=True,
                ),
                comp,
            )

        # CASE D
        return (
            Decision(
                strategy=Strategy.SUBTITLE_ONLY,
                case_label=CaseLabel.D,
                reasons=reasons or ["Subtitle adaptation required"],
                expected_container=expected_container,
                expected_direct_play_safe=True,
            ),
            comp,
        )
