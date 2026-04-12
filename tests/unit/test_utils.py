from __future__ import annotations

import errno
import os
from pathlib import Path

from reeltranscode.utils import atomic_replace


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
