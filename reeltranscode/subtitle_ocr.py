from __future__ import annotations

import json
import logging
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

from reeltranscode.models import OcrSubtitleTask
from reeltranscode.process_registry import PROCESS_REGISTRY, ShutdownRequestedError

LOGGER = logging.getLogger(__name__)


class SubtitleOcrError(RuntimeError):
    pass


def ocr_image_subtitle_to_srt(task: OcrSubtitleTask, *, max_workers: int | None = None) -> Path:
    try:
        from pgsrip.options import Options
        from pgsrip.ripper import PgsToSrtRipper
        from pgsrip.sup import Sup
        import pytesseract
    except ImportError as exc:
        raise SubtitleOcrError(
            "PGS OCR dependencies are unavailable. Install the backend with OCR support to convert image subtitles."
        ) from exc

    tesseract_bin = _resolve_tesseract_binary()
    if tesseract_bin is None:
        raise SubtitleOcrError(
            "Tesseract is not available. Install 'tesseract' to convert image subtitles to Apple-native mov_text."
        )

    pytesseract.pytesseract.tesseract_cmd = tesseract_bin

    options = Options(
        overwrite=True,
        encoding="utf-8",
        keep_temp_files=False,
        max_workers=max_workers,
    )
    sup_media = Sup(str(task.sup_path))
    pgs = next(iter(sup_media.get_pgs_medias(options)), None)
    if pgs is None:
        raise SubtitleOcrError(f"Unable to decode extracted subtitle stream: {task.sup_path}")

    available_languages = set(pytesseract.get_languages(config=""))
    if pgs.media_path.language and pgs.media_path.language.alpha3 not in available_languages:
        LOGGER.warning(
            "Tesseract language '%s' is unavailable for %s; falling back to default OCR language",
            pgs.media_path.language.alpha3,
            task.sup_path,
        )
        pgs.media_path.language = None

    with pgs as pgs_session:
        subtitle_file = PgsToSrtRipper(pgs_session, options).rip(lambda text: text.strip())
        subtitle_file.path = str(task.output_path)
        subtitle_file.clean_indexes()
        subtitle_file.save(encoding="utf-8")

    output_path = Path(task.output_path)
    if not output_path.exists():
        raise SubtitleOcrError(f"OCR completed without producing an SRT file: {output_path}")
    if not output_path.read_text(encoding="utf-8").strip():
        raise SubtitleOcrError(f"OCR produced an empty SRT file: {output_path}")
    return output_path


def ocr_image_subtitle_to_srt_subprocess(task: OcrSubtitleTask, *, max_workers: int | None = None) -> Path:
    command = _build_ocr_worker_command(task, max_workers=max_workers)

    try:
        result = PROCESS_REGISTRY.run(command, text=True, cwd=task.sup_path.parent)
    except OSError as exc:
        raise SubtitleOcrError(f"Unable to launch OCR worker: {exc}") from exc

    if result.returncode != 0 and PROCESS_REGISTRY.is_stopping():
        raise ShutdownRequestedError("OCR interrupted because service shutdown was requested")
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip() or "OCR worker failed"
        raise SubtitleOcrError(details)

    output_path = Path(task.output_path)
    if not output_path.exists():
        raise SubtitleOcrError(f"OCR worker completed without producing an SRT file: {output_path}")
    return output_path


def _build_ocr_worker_command(task: OcrSubtitleTask, *, max_workers: int | None = None) -> list[str]:
    command = [sys.executable]
    if getattr(sys, "frozen", False):
        command.append("ocr-worker")
    else:
        command.extend(["-m", "reeltranscode.cli", "ocr-worker"])
    command.extend(["--task-json", _task_json(task)])
    if max_workers is not None:
        command.extend(["--max-workers", str(max_workers)])
    return command


def _resolve_tesseract_binary() -> str | None:
    candidates = [
        shutil.which("tesseract"),
        "/opt/homebrew/bin/tesseract",
        "/usr/local/bin/tesseract",
        "/usr/bin/tesseract",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.exists():
            return str(path)
    return None


def _task_json(task: OcrSubtitleTask) -> str:
    payload = asdict(task)
    payload["sup_path"] = str(task.sup_path)
    payload["output_path"] = str(task.output_path)
    return json.dumps(payload, separators=(",", ":"))
