from __future__ import annotations

import os
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class WatchRepro:
    config_path: Path
    fake_pid_path: Path
    launch_count_path: Path


def test_watch_sigterm_stops_active_transcode_children(tmp_path):
    repro = _make_watch_repro(tmp_path, retry_attempts=1)
    proc = _start_watch_process(repro)
    fake_pid = None

    try:
        fake_pid = _wait_for_fake_pid(repro.fake_pid_path)
        os.kill(proc.pid, signal.SIGTERM)
        assert _wait_until(lambda: proc.poll() is not None, timeout=10)
        stdout, stderr = proc.communicate(timeout=1)
        assert not _is_process_alive(fake_pid), (
            f"watch child still alive after SIGTERM: pid={fake_pid}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        )
    finally:
        _cleanup_watch_process(proc, fake_pid)


def test_watch_sigterm_does_not_retry_terminated_transcode(tmp_path):
    repro = _make_watch_repro(tmp_path, retry_attempts=3)
    proc = _start_watch_process(repro)
    fake_pid = None

    try:
        fake_pid = _wait_for_fake_pid(repro.fake_pid_path)
        os.kill(proc.pid, signal.SIGTERM)
        assert _wait_until(lambda: proc.poll() is not None, timeout=10)
        proc.communicate(timeout=1)
        assert repro.launch_count_path.read_text(encoding="utf-8").strip() == "1"
    finally:
        _cleanup_watch_process(proc, fake_pid)


def _make_watch_repro(tmp_path: Path, *, retry_attempts: int) -> WatchRepro:
    watch_root = tmp_path / "watch"
    watch_root.mkdir()
    (watch_root / "sample.mkv").write_bytes(b"placeholder")

    fake_ffprobe = tmp_path / "fake_ffprobe.py"
    fake_ffprobe.write_text(_fake_ffprobe_script(), encoding="utf-8")
    fake_ffprobe.chmod(fake_ffprobe.stat().st_mode | stat.S_IEXEC)

    fake_pid_path = tmp_path / "fake_ffmpeg.pid"
    launch_count_path = tmp_path / "fake_ffmpeg.count"
    fake_ffmpeg = tmp_path / "fake_ffmpeg.sh"
    fake_ffmpeg.write_text(_fake_ffmpeg_script(fake_pid_path, launch_count_path), encoding="utf-8")
    fake_ffmpeg.chmod(fake_ffmpeg.stat().st_mode | stat.S_IEXEC)

    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
watch:
  folders:
    - {watch_root}
  recursive: true
  stable_wait_seconds: 1
  stable_checks: 1
  poll_interval_seconds: 1
output:
  mode: keep_original
  output_root: {tmp_path / "optimized"}
  overwrite: true
paths:
  state_db: {tmp_path / "state.db"}
  reports_dir: {tmp_path / "reports"}
  csv_summary: {tmp_path / "reports" / "summary.csv"}
  temp_dir: {tmp_path / "tmp"}
tooling:
  ffmpeg_bin: {fake_ffmpeg}
  ffprobe_bin: {fake_ffprobe}
logging:
  level: INFO
validation:
  run_post_ffprobe: false
concurrency:
  max_workers: 1
retry:
  max_attempts: {retry_attempts}
  backoff_initial_seconds: 0.1
  backoff_max_seconds: 0.1
""".strip(),
        encoding="utf-8",
    )
    return WatchRepro(config_path=config, fake_pid_path=fake_pid_path, launch_count_path=launch_count_path)


def _start_watch_process(repro: WatchRepro) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-m", "reeltranscode.cli", "--config", str(repro.config_path), "watch"],
        cwd=str(Path(__file__).resolve().parents[2]),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_for_fake_pid(fake_pid_path: Path) -> int:
    assert _wait_until(lambda: fake_pid_path.exists() and fake_pid_path.read_text().strip(), timeout=10)
    return int(fake_pid_path.read_text(encoding="utf-8").strip())


def _cleanup_watch_process(proc: subprocess.Popen[str], fake_pid: int | None) -> None:
    if proc.poll() is None:
        proc.kill()
        proc.communicate(timeout=5)
    if fake_pid is not None and _is_process_alive(fake_pid):
        os.kill(fake_pid, signal.SIGKILL)


def _wait_until(predicate, timeout: float) -> bool:  # noqa: ANN001
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def _is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _fake_ffprobe_script() -> str:
    return """#!/usr/bin/env python3
import json

print(json.dumps({
    "format": {"format_name": "matroska,webm", "duration": "2.0", "bit_rate": "1000", "size": "1"},
    "streams": [
        {"index": 0, "codec_type": "video", "codec_name": "h264", "codec_tag_string": "avc1", "width": 128, "height": 72, "pix_fmt": "yuv420p", "avg_frame_rate": "24/1", "r_frame_rate": "24/1"},
        {"index": 1, "codec_type": "audio", "codec_name": "aac", "channels": 2, "channel_layout": "stereo"}
    ],
    "chapters": []
}))
"""


def _fake_ffmpeg_script(fake_pid_path: Path, launch_count_path: Path) -> str:
    return f"""#!/bin/sh
set -eu
count=0
if [ -f "{launch_count_path}" ]; then
  count=$(cat "{launch_count_path}")
fi
count=$((count + 1))
echo "$count" > "{launch_count_path}"
echo $$ > "{fake_pid_path}"
sleep 300
"""
