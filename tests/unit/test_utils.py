from __future__ import annotations

import errno
from pathlib import Path

from reeltranscode.utils import atomic_replace


def test_atomic_replace_falls_back_to_move_on_cross_device_error(tmp_path: Path, monkeypatch):
    src = tmp_path / "src.mp4"
    dst = tmp_path / "nested" / "dst.mp4"
    src.write_bytes(b"movie")

    replace_calls = {"count": 0}
    move_calls = {"count": 0}

    def fake_replace(_src, _dst):
        replace_calls["count"] += 1
        raise OSError(errno.EXDEV, "Cross-device link")

    def fake_move(src_name: str, dst_name: str):
        move_calls["count"] += 1
        Path(dst_name).parent.mkdir(parents=True, exist_ok=True)
        Path(dst_name).write_bytes(Path(src_name).read_bytes())
        Path(src_name).unlink()

    monkeypatch.setattr("reeltranscode.utils.os.replace", fake_replace)
    monkeypatch.setattr("reeltranscode.utils.shutil.move", fake_move)

    atomic_replace(src, dst)

    assert replace_calls["count"] == 1
    assert move_calls["count"] == 1
    assert dst.read_bytes() == b"movie"
    assert not src.exists()
