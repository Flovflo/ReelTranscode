from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

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
        process = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            cwd=str(cwd) if cwd else None,
        )
        result = CommandResult(
            command=command,
            return_code=process.returncode,
            stdout=process.stdout,
            stderr=process.stderr,
        )
        if process.returncode != 0:
            raise CommandFailedError(process.stderr.strip() or "ffmpeg command failed")
        return result
