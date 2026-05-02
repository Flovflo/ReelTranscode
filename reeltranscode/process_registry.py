from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

LOGGER = logging.getLogger(__name__)
PROCESS_OUTPUT_ENCODING = "utf-8"
PROCESS_OUTPUT_ERRORS = "backslashreplace"


class ShutdownRequestedError(RuntimeError):
    pass


class ManagedProcessRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._processes: dict[int, subprocess.Popen] = {}
        self._stopping = threading.Event()

    def run(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
        text: bool,
    ) -> subprocess.CompletedProcess:
        if self._stopping.is_set():
            raise ShutdownRequestedError("Process shutdown in progress")
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=text,
            encoding=PROCESS_OUTPUT_ENCODING if text else None,
            errors=PROCESS_OUTPUT_ERRORS if text else None,
            cwd=str(cwd) if cwd else None,
            start_new_session=True,
        )
        self._register(process)
        try:
            stdout, stderr = process.communicate()
        except BaseException:
            self._terminate_process_tree(process)
            raise
        finally:
            self._unregister(process.pid)
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

    def terminate_all(self, *, grace_period: float = 2.0) -> None:
        self._stopping.set()
        self._signal_all(signal.SIGTERM)
        if self._wait_until_drained(timeout=grace_period):
            return
        self._signal_all(signal.SIGKILL)
        self._wait_until_drained(timeout=1.0)

    def request_stop(self) -> None:
        self._stopping.set()
        self._signal_all(signal.SIGTERM)

    def clear_stop_request(self) -> None:
        self._stopping.clear()

    def is_stopping(self) -> bool:
        return self._stopping.is_set()

    def _register(self, process: subprocess.Popen) -> None:
        with self._lock:
            self._processes[process.pid] = process

    def _unregister(self, pid: int) -> None:
        with self._lock:
            self._processes.pop(pid, None)

    def _snapshot(self) -> list[subprocess.Popen]:
        with self._lock:
            return list(self._processes.values())

    def _signal_all(self, sig: signal.Signals) -> None:
        for process in self._snapshot():
            if process.poll() is not None:
                continue
            if self._send_signal(process, sig):
                LOGGER.info("Sent %s to managed child process tree pid=%s", sig.name, process.pid)

    def _terminate_process_tree(self, process: subprocess.Popen, *, grace_period: float = 2.0) -> None:
        if process.poll() is not None:
            return
        if self._send_signal(process, signal.SIGTERM):
            LOGGER.info("Sent SIGTERM to managed child process tree after communication failure pid=%s", process.pid)
        try:
            process.wait(timeout=grace_period)
            return
        except subprocess.TimeoutExpired:
            pass
        if self._send_signal(process, signal.SIGKILL):
            LOGGER.warning("Sent SIGKILL to managed child process tree after communication failure pid=%s", process.pid)
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            LOGGER.warning("Managed child process tree did not exit after SIGKILL pid=%s", process.pid)

    @staticmethod
    def _send_signal(process: subprocess.Popen, sig: signal.Signals) -> bool:
        try:
            os.killpg(process.pid, sig)
            return True
        except ProcessLookupError:
            return False
        except OSError:
            try:
                process.send_signal(sig)
                return True
            except OSError:
                return False

    def _wait_until_drained(self, *, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if all(process.poll() is not None for process in self._snapshot()):
                return True
            time.sleep(0.05)
        return all(process.poll() is not None for process in self._snapshot())


PROCESS_REGISTRY = ManagedProcessRegistry()
