from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from reeltranscode.process_registry import PROCESS_REGISTRY, ShutdownRequestedError

LOGGER = logging.getLogger(__name__)


class CommandFailedError(RuntimeError):
    pass


@dataclass(slots=True)
class CommandResult:
    command: list[str]
    return_code: int
    stdout: str
    stderr: str


class FFmpegRunner:
    def run(self, command: list[str], cwd: Path | None = None) -> CommandResult:
        LOGGER.info("Executing: %s", " ".join(command))
        process = PROCESS_REGISTRY.run(
            command,
            text=True,
            cwd=cwd,
        )
        result = CommandResult(
            command=command,
            return_code=process.returncode,
            stdout=process.stdout,
            stderr=process.stderr,
        )
        if process.returncode != 0 and PROCESS_REGISTRY.is_stopping():
            raise ShutdownRequestedError("Transcode interrupted because service shutdown was requested")
        if process.returncode != 0:
            raise CommandFailedError(process.stderr.strip() or "ffmpeg command failed")
        return result
