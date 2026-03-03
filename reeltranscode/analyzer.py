from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from reeltranscode.config import AppConfig
from reeltranscode.models import MediaInfo, StreamInfo

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
        return [
            self.config.tooling.ffprobe_bin,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-show_chapters",
            str(path),
        ]

    def analyze(self, path: Path) -> tuple[MediaInfo, list[str]]:
        command = self.probe_command(path)
        LOGGER.debug("ffprobe command: %s", " ".join(command))
        process = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
        )
        if process.returncode != 0:
            raise ProbeError(process.stderr.strip() or f"ffprobe failed for {path}")

        payload = json.loads(process.stdout)
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
        media = MediaInfo(
            path=path,
            format_name=str(format_node.get("format_name", "")),
            duration=duration_value,
            bit_rate=bit_rate_value,
            size=size_value,
            streams=streams,
            raw_probe=payload,
        )
        return media, command

    @staticmethod
    def detect_dolby_vision(media: MediaInfo) -> tuple[bool, str | None]:
        for stream in media.video_streams:
            for side_data in stream.side_data_list:
                side_type = str(side_data.get("side_data_type", "")).lower()
                if "dovi" in side_type or "dolby vision" in side_type:
                    profile = side_data.get("dv_profile") or side_data.get("dv_profile_string")
                    if profile is None:
                        profile = side_data.get("profile")
                    if profile is None:
                        return True, None
                    profile_str = str(profile)
                    if profile_str.isdigit():
                        profile_str = f"{profile_str}.0"
                    return True, profile_str
        return False, None

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
            if (stream.codec_name or "") not in MP4_SUBTITLE_CODECS:
                reasons.append(f"Subtitle codec {stream.codec_name} incompatible with MP4")
        return len(reasons) == 0, reasons


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
