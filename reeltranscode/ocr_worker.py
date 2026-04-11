from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from reeltranscode.models import OcrSubtitleTask
from reeltranscode.subtitle_ocr import ocr_image_subtitle_to_srt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reeltranscode-ocr-worker")
    parser.add_argument("--task-json", required=True, help="Serialized OCR task payload")
    parser.add_argument("--max-workers", type=int, default=None, help="Optional OCR worker parallelism")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = json.loads(args.task_json)
    task = OcrSubtitleTask(
        source_subtitle_index=int(payload["source_subtitle_index"]),
        source_codec=payload.get("source_codec"),
        language=payload.get("language"),
        title=payload.get("title"),
        default=bool(payload.get("default")),
        forced=bool(payload.get("forced")),
        hearing_impaired=bool(payload.get("hearing_impaired")),
        captions=bool(payload.get("captions")),
        sup_path=Path(payload["sup_path"]),
        output_path=Path(payload["output_path"]),
    )
    ocr_image_subtitle_to_srt(task, max_workers=args.max_workers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
