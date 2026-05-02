from __future__ import annotations

import signal
import sys
from typing import Any

from reeltranscode.process_registry import ManagedProcessRegistry


def test_registry_text_mode_tolerates_invalid_process_output_bytes():
    registry = ManagedProcessRegistry()

    result = registry.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "sys.stdout.buffer.write(b'ok\\xff\\n'); "
                "sys.stderr.buffer.write(b'err\\x80\\n')"
            ),
        ],
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout == "ok\\xff\n"
    assert result.stderr == "err\\x80\n"


def test_registry_terminates_child_when_communicate_raises(monkeypatch):
    class CommunicateBoom(RuntimeError):
        pass

    class FakeProcess:
        pid = 12345
        returncode: int | None = None

        def __init__(self) -> None:
            self.signals: list[signal.Signals] = []

        def communicate(self):
            raise CommunicateBoom("decoder exploded")

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
            self.returncode = -signal.SIGTERM
            return self.returncode

        def send_signal(self, sig: signal.Signals) -> None:
            self.signals.append(sig)

    fake = FakeProcess()

    def fake_popen(*_args: Any, **_kwargs: Any) -> FakeProcess:
        return fake

    def fake_killpg(pid: int, sig: signal.Signals) -> None:
        assert pid == fake.pid
        fake.signals.append(sig)

    monkeypatch.setattr("reeltranscode.process_registry.subprocess.Popen", fake_popen)
    monkeypatch.setattr("reeltranscode.process_registry.os.killpg", fake_killpg)

    registry = ManagedProcessRegistry()

    try:
        registry.run(["fake-ffmpeg"], text=True)
    except CommunicateBoom:
        pass
    else:
        raise AssertionError("communicate failure should be re-raised")

    assert signal.SIGTERM in fake.signals
    assert registry._snapshot() == []
