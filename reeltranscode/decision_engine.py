from __future__ import annotations

from reeltranscode.analyzer import FFprobeAnalyzer
from reeltranscode.config import AppConfig
from reeltranscode.models import CaseLabel, CompatibilityDetails, Decision, MediaInfo, Strategy
from reeltranscode.tooling import DolbyVisionMuxCapabilities, ToolchainResolver


class DecisionEngine:
    def __init__(self, config: AppConfig):
        self.config = config
        self.tooling = ToolchainResolver(config)

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

    @staticmethod
    def _has_aac_stereo(media: MediaInfo) -> bool:
        for stream in media.audio_streams:
            if (stream.codec_name or "").lower() == "aac" and (stream.channels or 2) <= 2:
                return True
        return False

    def _subtitles_supported_by_dovi_muxer(self, media: MediaInfo) -> bool:
        for stream in media.subtitle_streams:
            codec = (stream.codec_name or "").lower()
            if stream.is_text_subtitle:
                continue
            if codec in {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt"}:
                continue
            if stream.is_image_subtitle:
                return False
            if not codec:
                return False
            return False
        return True

    def _can_use_dovi_muxer(
        self,
        media: MediaInfo,
        comp: CompatibilityDetails,
        caps: DolbyVisionMuxCapabilities,
    ) -> bool:
        return (
            self.config.remux.preferred_container.lower() == "mp4"
            and caps.available
            and not comp.requires_video_transcode
            and not comp.requires_audio_fix
            and self._subtitles_supported_by_dovi_muxer(media)
        )

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
                dv_caps = self.tooling.resolve_dolby_vision_mux_capabilities()
                if self._can_use_dovi_muxer(media, comp, dv_caps):
                    reasons.append("Using DoViMuxer DV-safe remux path for Apple-compatible MP4")
                    if self.config.audio.ensure_aac_fallback_stereo_when_missing and not self._has_aac_stereo(media):
                        reasons.append("AAC stereo fallback is skipped on the DV-safe remux path")
                    return (
                        Decision(
                            strategy=Strategy.REMUX_ONLY,
                            case_label=CaseLabel.F,
                            reasons=reasons,
                            expected_container=expected_container,
                            expected_direct_play_safe=True,
                            preserve_hdr10=True,
                            use_dovi_muxer=True,
                        ),
                        comp,
                    )

                if dv_caps.missing_tools:
                    reasons.append(
                        "DV-safe MP4 mux toolchain unavailable: missing "
                        + ", ".join(sorted(dv_caps.missing_tools))
                    )
                elif comp.requires_video_transcode:
                    reasons.append("DV-safe remux unavailable because video transcoding would be required")
                elif comp.requires_audio_fix:
                    reasons.append("DV-safe remux unavailable because audio transcoding would be required")
                elif not self._subtitles_supported_by_dovi_muxer(media):
                    reasons.append("DV-safe remux unavailable because subtitles are not DoViMuxer-compatible")

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
                        use_dovi_muxer=False,
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
                    use_dovi_muxer=False,
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
