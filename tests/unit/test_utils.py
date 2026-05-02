from __future__ import annotations

import errno
import os
from pathlib import Path

from reeltranscode.utils import atomic_replace, is_generated_metadata_path, is_media_file


def test_atomic_replace_stages_cross_device_publish_on_destination_volume(tmp_path: Path, monkeypatch):
    src = tmp_path / "src.mp4"
    dst = tmp_path / "nested" / "dst.mp4"
    src.write_bytes(b"movie")

    replace_calls = {"count": 0}
    real_replace = os.replace

    def fake_replace(src_name: str, dst_name: str):
        replace_calls["count"] += 1
        if Path(src_name) == src and Path(dst_name) == dst:
            raise OSError(errno.EXDEV, "Cross-device link")
        return real_replace(src_name, dst_name)

    monkeypatch.setattr("reeltranscode.utils.os.replace", fake_replace)

    result = atomic_replace(src, dst)

    assert result.used_cross_device_fallback is True
    assert replace_calls["count"] == 2
    assert dst.read_bytes() == b"movie"
    assert not src.exists()
    assert not list(dst.parent.glob(".dst.mp4.*.publish"))


def test_generated_metadata_paths_are_not_media_candidates(tmp_path: Path):
    media = tmp_path / "Show" / "S01" / ".@__thumb" / "s100S01E01.avi"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"thumbnail")

    assert is_generated_metadata_path(media) is True
    assert is_media_file(media, {".avi"}) is False


def test_generated_metadata_paths_are_case_insensitive(tmp_path: Path):
    media = tmp_path / "Show" / "@eaDir" / "episode.mkv"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"metadata")

    assert is_generated_metadata_path(media) is True
    assert is_media_file(media, {".mkv"}) is False
