from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from reeltranscode.config import AppConfig
from reeltranscode.models import DolbyVisionEvidence, MediaInfo, StreamInfo, SubtitleTrackState

LOGGER = logging.getLogger(__name__)

APPLE_CONTAINERS = {"mp4", "mov", "m4v"}
APPLE_VIDEO_CODECS = {"hevc", "h264"}
APPLE_AUDIO_CODECS = {"eac3", "ac3", "aac"}
MP4_SUBTITLE_CODECS = {"mov_text"}


class ProbeError(RuntimeError):
    pass


class FFprobeAnalyzer:
    def __init__(self, config: AppConfig):
        self.config = config

    def probe_command(self, path: Path) -> list[str]:
        return self._probe_command_for_binary(self.config.tooling.ffprobe_bin, path)

    @staticmethod
    def _probe_command_for_binary(ffprobe_bin: str, path: Path) -> list[str]:
        return [
            ffprobe_bin,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-show_chapters",
            str(path),
        ]

    def _ffprobe_candidates(self) -> list[str]:
        candidates: list[str] = []
        seen: set[str] = set()

        def append(candidate: str | None) -> None:
            if not candidate:
                return
            normalized = str(candidate).strip()
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            candidates.append(normalized)

        append(self.config.tooling.ffprobe_bin)

        ffmpeg_bin = str(self.config.tooling.ffmpeg_bin).strip()
        if "/" in ffmpeg_bin:
            sibling = str(Path(ffmpeg_bin).expanduser().with_name("ffprobe"))
            append(sibling)

        append("/opt/homebrew/bin/ffprobe")
        append("/usr/local/bin/ffprobe")
        append("/usr/bin/ffprobe")
        append(shutil.which("ffprobe"))
        return candidates

    def _mediainfo_candidates(self) -> list[str]:
        candidates: list[str] = []
        seen: set[str] = set()

        def append(candidate: str | None) -> None:
            if not candidate:
                return
            normalized = str(candidate).strip()
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            candidates.append(normalized)

        append(self.config.tooling.mediainfo_bin)

        ffmpeg_bin = str(self.config.tooling.ffmpeg_bin).strip()
        if "/" in ffmpeg_bin:
            sibling = str(Path(ffmpeg_bin).expanduser().with_name("mediainfo"))
            append(sibling)

        append("/opt/homebrew/bin/mediainfo")
        append("/usr/local/bin/mediainfo")
        append("/usr/bin/mediainfo")
        append(shutil.which("mediainfo"))
        return candidates

    @staticmethod
    def _probe_failure_message(path: Path, errors: list[str]) -> str:
        details = "; ".join(errors)
        return f"ffprobe failed for {path}. attempts: {details}"

    def analyze(self, path: Path) -> tuple[MediaInfo, list[str]]:
        errors: list[str] = []
        for ffprobe_bin in self._ffprobe_candidates():
            command = self._probe_command_for_binary(ffprobe_bin, path)
            LOGGER.debug("ffprobe command: %s", " ".join(command))
            try:
                process = subprocess.run(
                    command,
                    text=True,
                    capture_output=True,
                    check=False,
                )
            except OSError as exc:
                errors.append(f"{ffprobe_bin}: {exc}")
                continue
            if process.returncode != 0:
                stderr = process.stderr.strip() or process.stdout.strip() or f"exit code {process.returncode}"
                errors.append(f"{ffprobe_bin}: {stderr}")
                continue

            try:
                payload = json.loads(process.stdout)
            except json.JSONDecodeError as exc:
                errors.append(f"{ffprobe_bin}: invalid json output ({exc})")
                continue

            if ffprobe_bin != self.config.tooling.ffprobe_bin:
                LOGGER.warning(
                    "Primary ffprobe failed, using fallback binary '%s' for %s",
                    ffprobe_bin,
                    path,
                )
                self.config.tooling.ffprobe_bin = ffprobe_bin

            format_node = payload.get("format", {}) or {}
            duration = format_node.get("duration")
            duration_value = float(duration) if duration is not None else None
            bit_rate = format_node.get("bit_rate")
            bit_rate_value = int(bit_rate) if isinstance(bit_rate, str) and bit_rate.isdigit() else None
            if isinstance(bit_rate, int):
                bit_rate_value = bit_rate
            size = format_node.get("size")
            size_value = int(size) if isinstance(size, str) and size.isdigit() else None

            streams = [StreamInfo.from_probe(item) for item in payload.get("streams", [])]
            format_name = str(format_node.get("format_name", ""))
            media = MediaInfo(
                path=path,
                format_name=format_name,
                duration=duration_value,
                bit_rate=bit_rate_value,
                size=size_value,
                streams=streams,
                raw_probe=payload,
                raw_mediainfo=self._load_mediainfo(path, format_name),
            )
            return media, command

        raise ProbeError(self._probe_failure_message(path, errors))

    @staticmethod
    def detect_dolby_vision(media: MediaInfo) -> tuple[bool, str | None]:
        info = FFprobeAnalyzer.inspect_dolby_vision(media)
        return info.present, info.profile

    @staticmethod
    def inspect_dolby_vision(media: MediaInfo) -> DolbyVisionEvidence:
        ffprobe_info = FFprobeAnalyzer._inspect_dolby_vision_from_ffprobe(media)
        mediainfo_info = FFprobeAnalyzer._inspect_dolby_vision_from_mediainfo(media.raw_mediainfo)

        if FFprobeAnalyzer.is_container_apple_compatible(media) and mediainfo_info.present:
            return mediainfo_info
        if ffprobe_info.present:
            return ffprobe_info
        if mediainfo_info.present:
            return mediainfo_info
        return mediainfo_info

    @staticmethod
    def detect_hdr10(media: MediaInfo) -> bool:
        video = media.primary_video
        if video is None:
            return False
        return (
            (video.color_primaries or "").lower() == "bt2020"
            and (video.color_transfer or "").lower() in {"smpte2084", "arib-std-b67"}
        )

    @staticmethod
    def stream_fingerprint(media: MediaInfo) -> str:
        structure = {
            "format": sorted(media.container_names),
            "streams": [
                {
                    "codec_type": stream.codec_type,
                    "codec_name": stream.codec_name,
                    "codec_tag_string": stream.codec_tag_string,
                    "profile": stream.profile,
                    "level": stream.level,
                    "pix_fmt": stream.pix_fmt,
                    "width": stream.width,
                    "height": stream.height,
                    "fps": stream.avg_frame_rate,
                    "channels": stream.channels,
                    "channel_layout": stream.channel_layout,
                    "color_primaries": stream.color_primaries,
                    "color_transfer": stream.color_transfer,
                    "color_space": stream.color_space,
                    "dv_profile": stream.dv_profile,
                    "language": (stream.language or "und").lower(),
                    "default": stream.disposition.default,
                    "forced": stream.disposition.forced,
                }
                for stream in media.streams
            ],
        }
        payload = json.dumps(structure, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def metadata_fingerprint(media: MediaInfo) -> str:
        metadata = {
            "path_name": media.path.name,
            "streams": [
                {
                    "codec_type": stream.codec_type,
                    "language": stream.language,
                    "title": stream.title,
                    "default": stream.disposition.default,
                    "forced": stream.disposition.forced,
                }
                for stream in media.streams
            ],
        }
        payload = json.dumps(metadata, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def is_container_apple_compatible(media: MediaInfo) -> bool:
        return not media.container_names.isdisjoint(APPLE_CONTAINERS)

    @staticmethod
    def is_video_apple_compatible(media: MediaInfo, max_4k_fps: int) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        video = media.primary_video
        if video is None:
            return False, ["No video stream"]

        if (video.codec_name or "").lower() not in APPLE_VIDEO_CODECS:
            reasons.append(f"Unsupported video codec: {video.codec_name}")
        if (video.codec_name or "") == "hevc" and (video.pix_fmt or "") not in {"yuv420p", "yuv420p10le", "p010le"}:
            reasons.append(f"Unsupported HEVC pixel format: {video.pix_fmt}")
        if (video.codec_name or "") == "h264" and (video.pix_fmt or "") not in {"yuv420p"}:
            reasons.append(f"Unsupported H.264 pixel format: {video.pix_fmt}")
        if video.field_order and video.field_order not in {"progressive", "unknown"}:
            reasons.append(f"Interlaced video detected ({video.field_order}); transcode recommended")
        fps = _frame_rate_to_float(video.avg_frame_rate or video.r_frame_rate)
        if fps and video.width and video.height and video.width >= 3840 and fps > max_4k_fps:
            reasons.append(f"4K frame rate too high for policy: {fps:.2f}fps")
        return len(reasons) == 0, reasons

    @staticmethod
    def is_audio_apple_compatible(media: MediaInfo) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if not media.audio_streams:
            reasons.append("No audio streams")
            return False, reasons

        compatible = [s for s in media.audio_streams if (s.codec_name or "").lower() in APPLE_AUDIO_CODECS]
        if not compatible:
            reasons.append("No Apple-compatible audio track (need eac3/ac3/aac)")
        return len(reasons) == 0, reasons

    @staticmethod
    def mp4_subtitle_compatible(media: MediaInfo) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        for stream in media.subtitle_streams:
            if stream.is_image_subtitle:
                reasons.append(
                    f"Image subtitle codec {stream.codec_name} requires OCR for Apple-native MP4"
                )
                continue
            if (stream.codec_name or "") not in MP4_SUBTITLE_CODECS:
                reasons.append(f"Subtitle codec {stream.codec_name} incompatible with MP4")
        return len(reasons) == 0, reasons

    @staticmethod
    def subtitle_track_states(media: MediaInfo) -> list[SubtitleTrackState]:
        details: list[SubtitleTrackState] = []
        mediainfo_tracks = FFprobeAnalyzer._mediainfo_text_tracks(media.raw_mediainfo)

        for index, stream in enumerate(media.subtitle_streams):
            track = mediainfo_tracks[index] if index < len(mediainfo_tracks) else {}
            title = stream.title or _clean_mediainfo_text(track.get("Title"))
            language = _normalize_language(stream.language or track.get("Language"))
            codec_name = (stream.codec_name or "").lower() or _mediainfo_subtitle_codec(track)
            forced = stream.disposition.forced or _mediainfo_yes(track.get("Forced"))
            hearing_impaired = stream.disposition.hearing_impaired or str(track.get("ServiceKind", "")).upper() == "HI"
            captions = stream.disposition.captions or hearing_impaired
            details.append(
                SubtitleTrackState(
                    codec_name=codec_name or None,
                    language=language,
                    title=title,
                    default=stream.disposition.default or _mediainfo_yes(track.get("Default")),
                    forced=forced,
                    hearing_impaired=hearing_impaired,
                    captions=captions,
                )
            )
        return details

    def _load_mediainfo(self, path: Path, format_name: str) -> dict[str, Any]:
        container_names = {item.strip().lower() for item in format_name.split(",") if item.strip()}
        if APPLE_CONTAINERS.isdisjoint(container_names):
            return {}

        for mediainfo_bin in self._mediainfo_candidates():
            command = [mediainfo_bin, "--Output=JSON", str(path)]
            try:
                process = subprocess.run(
                    command,
                    text=True,
                    capture_output=True,
                    check=False,
                )
            except OSError:
                continue
            if process.returncode != 0:
                continue
            try:
                payload = json.loads(process.stdout)
            except json.JSONDecodeError:
                continue
            if mediainfo_bin != self.config.tooling.mediainfo_bin:
                LOGGER.warning(
                    "Primary mediainfo unavailable, using fallback binary '%s' for %s",
                    mediainfo_bin,
                    path,
                )
                self.config.tooling.mediainfo_bin = mediainfo_bin
            return payload
        return {}

    @staticmethod
    def _inspect_dolby_vision_from_ffprobe(media: MediaInfo) -> DolbyVisionEvidence:
        for stream in media.video_streams:
            side_data_list = list(stream.side_data_list or [])
            for side_data in side_data_list:
                side_type = str(side_data.get("side_data_type", "")).lower()
                if "dovi" not in side_type and "dolby vision" not in side_type:
                    continue
                profile = _normalize_dolby_vision_profile(
                    side_data.get("dv_profile")
                    or side_data.get("dv_profile_string")
                    or side_data.get("profile")
                    or stream.dv_profile,
                    compatibility_id=side_data.get("dv_bl_signal_compatibility_id"),
                )
                return DolbyVisionEvidence(present=True, profile=profile, source="ffprobe")

            if stream.dv_profile:
                profile = _normalize_dolby_vision_profile(stream.dv_profile)
                return DolbyVisionEvidence(present=True, profile=profile, source="ffprobe")

        return DolbyVisionEvidence(present=False)

    @staticmethod
    def _inspect_dolby_vision_from_mediainfo(raw_mediainfo: dict[str, Any]) -> DolbyVisionEvidence:
        if not raw_mediainfo:
            return DolbyVisionEvidence(present=False)

        media_node = raw_mediainfo.get("media", {}) or {}
        tracks = media_node.get("track", []) or []
        general_track = next((track for track in tracks if str(track.get("@type")) == "General"), {})
        compatible_brands = str(general_track.get("CodecID_Compatible", "")).lower()
        brand_hint = "dby1" in compatible_brands

        for track in tracks:
            if str(track.get("@type")) != "Video":
                continue
            hdr_format = str(track.get("HDR_Format", ""))
            if "dolby vision" not in hdr_format.lower():
                continue
            profile = _normalize_dolby_vision_profile(
                track.get("HDR_Format_Profile") or track.get("Format_Profile"),
                compatibility_text=str(track.get("HDR_Format_Compatibility", "")),
            )
            return DolbyVisionEvidence(
                present=True,
                profile=profile,
                source="mediainfo",
                brand_hint=brand_hint,
            )

        return DolbyVisionEvidence(present=False, brand_hint=brand_hint, source="mediainfo-brand" if brand_hint else None)

    @staticmethod
    def _mediainfo_text_tracks(raw_mediainfo: dict[str, Any]) -> list[dict[str, Any]]:
        if not raw_mediainfo:
            return []
        media_node = raw_mediainfo.get("media", {}) or {}
        tracks = media_node.get("track", []) or []
        return [track for track in tracks if str(track.get("@type")) == "Text"]


def _frame_rate_to_float(value: str | None) -> float | None:
    if not value or value in {"0/0", "N/A"}:
        return None
    if "/" in value:
        left, right = value.split("/", 1)
        try:
            denom = float(right)
            if denom == 0:
                return None
            return float(left) / denom
        except ValueError:
            return None
    try:
        return float(value)
    except ValueError:
        return None


def _normalize_dolby_vision_profile(
    profile: Any,
    *,
    compatibility_id: Any = None,
    compatibility_text: str | None = None,
) -> str | None:
    if profile is None:
        return None

    text = str(profile).strip().lower()
    if not text:
        return None

    match = re.search(r"(?:dvh[e1]\.)?(\d{1,2})(?:[._](\d{1,2}))?", text)
    if not match:
        return text

    major = int(match.group(1))
    minor = int(match.group(2)) if match.group(2) is not None else None

    if minor is None and compatibility_id not in {None, ""}:
        try:
            minor = int(str(compatibility_id).strip())
        except ValueError:
            minor = None

    if minor is None and compatibility_text and major == 8 and "hdr10" in compatibility_text.lower():
        minor = 1

    if minor is None:
        minor = 0
    return f"{major}.{minor}"


def _normalize_language(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None

    aliases = {
        "en": "eng",
        "eng": "eng",
        "fr": "fra",
        "fre": "fra",
        "fra": "fra",
        "ja": "jpn",
        "jpn": "jpn",
        "und": "und",
    }
    return aliases.get(text, text)


def _mediainfo_yes(value: Any) -> bool:
    if value is None:
        return False
    return "yes" in str(value).strip().lower()


def _mediainfo_subtitle_codec(track: dict[str, Any]) -> str:
    codec_id = str(track.get("CodecID", "")).lower()
    format_name = str(track.get("Format", "")).lower()
    if codec_id == "tx3g" or "timed text" in format_name:
        return "mov_text"
    return codec_id or format_name


def _clean_mediainfo_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
