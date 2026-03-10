from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from reeltranscode.config import AppConfig


@dataclass(slots=True)
class DolbyVisionMuxCapabilities:
    available: bool
    ffmpeg_bin: str
    dovi_muxer_bin: str | None
    mp4box_bin: str | None
    mediainfo_bin: str | None
    mp4muxer_bin: str | None
    missing_tools: list[str]

    def as_json(self) -> dict[str, object]:
        return {
            "dv_mp4_safe_mux": self.available,
            "missing_tools": self.missing_tools,
            "resolved": {
                "ffmpeg_bin": self.ffmpeg_bin,
                "dovi_muxer_bin": self.dovi_muxer_bin,
                "mp4box_bin": self.mp4box_bin,
                "mediainfo_bin": self.mediainfo_bin,
                "mp4muxer_bin": self.mp4muxer_bin,
            },
        }


class ToolchainResolver:
    def __init__(self, config: AppConfig):
        self.config = config

    def resolve_dolby_vision_mux_capabilities(self) -> DolbyVisionMuxCapabilities:
        ffmpeg_bin = self._resolve_binary(self.config.tooling.ffmpeg_bin, ["ffmpeg"]) or self.config.tooling.ffmpeg_bin
        sibling_dir = None
        ffmpeg_path = Path(ffmpeg_bin).expanduser()
        if ffmpeg_path.is_absolute():
            sibling_dir = ffmpeg_path.parent
            compat_wrapper = sibling_dir / "ffmpeg_dovi_compat"
            if compat_wrapper.exists() and os.access(compat_wrapper, os.X_OK):
                ffmpeg_bin = str(compat_wrapper)

        dovi_muxer_bin = self._resolve_binary(
            self.config.tooling.dovi_muxer_bin,
            ["DoViMuxer", "dovimuxer"],
            sibling_dir=sibling_dir,
        )
        mp4box_bin = self._resolve_binary(
            self.config.tooling.mp4box_bin,
            ["MP4Box", "mp4box"],
            sibling_dir=sibling_dir,
        )
        mediainfo_bin = self._resolve_binary(
            self.config.tooling.mediainfo_bin,
            ["mediainfo", "MediaInfo"],
            sibling_dir=sibling_dir,
        )
        mp4muxer_bin = self._resolve_binary(
            self.config.tooling.mp4muxer_bin,
            ["mp4muxer", "MP4Muxer"],
            sibling_dir=sibling_dir,
        )
        resolved = {
            "dovi_muxer": dovi_muxer_bin,
            "mp4box": mp4box_bin,
            "mediainfo": mediainfo_bin,
            "mp4muxer": mp4muxer_bin,
        }
        missing_tools = [name for name, value in resolved.items() if not value]
        return DolbyVisionMuxCapabilities(
            available=not missing_tools,
            ffmpeg_bin=ffmpeg_bin,
            dovi_muxer_bin=dovi_muxer_bin,
            mp4box_bin=mp4box_bin,
            mediainfo_bin=mediainfo_bin,
            mp4muxer_bin=mp4muxer_bin,
            missing_tools=missing_tools,
        )

    def _resolve_binary(
        self,
        configured: str | None,
        binary_names: list[str],
        sibling_dir: Path | None = None,
    ) -> str | None:
        candidates: list[str] = []
        seen: set[str] = set()

        def append(candidate: str | Path | None) -> None:
            if candidate is None:
                return
            text = str(candidate).strip()
            if not text or text in seen:
                return
            seen.add(text)
            candidates.append(text)

        append(configured)
        if sibling_dir is not None:
            for name in binary_names:
                append(sibling_dir / name)
        for name in binary_names:
            append(shutil.which(name))
            append(f"/opt/homebrew/bin/{name}")
            append(f"/usr/local/bin/{name}")
            append(f"/usr/bin/{name}")

        for candidate in candidates:
            path = Path(candidate).expanduser()
            if path.is_absolute():
                if path.exists() and os.access(path, os.X_OK):
                    return str(path)
                continue

            resolved = shutil.which(candidate)
            if resolved and os.access(resolved, os.X_OK):
                return resolved

        return None
