from __future__ import annotations

import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from reeltranscode.analyzer import FFprobeAnalyzer
from reeltranscode.config import AppConfig
from reeltranscode.pipeline import PipelineProcessor
from reeltranscode.reporter import Reporter
from reeltranscode.scanner import iter_media_files
from reeltranscode.state_store import StateStore
from reeltranscode.utils import setup_logging
from reeltranscode.watcher import LibraryWatcher

LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reeltranscode", description="Apple-friendly library optimizer")
    parser.add_argument("--config", default="config/reeltranscode.yaml", help="Path to YAML config")

    sub = parser.add_subparsers(dest="command", required=True)

    batch = sub.add_parser("batch", help="Scan configured library roots and process existing media")
    batch.add_argument("--dry-run", action="store_true", help="Analyze only, do not write")
    batch.add_argument("--limit", type=int, default=0, help="Max number of files to process")

    watch = sub.add_parser("watch", help="Watch configured folders for new files")
    watch.add_argument("--dry-run", action="store_true", help="Analyze only, do not write")

    analyze = sub.add_parser("analyze", help="Analyze one file and print compatibility decision")
    analyze.add_argument("path", help="File path")

    process_one = sub.add_parser("process", help="Process a single file")
    process_one.add_argument("path", help="File path")
    process_one.add_argument("--dry-run", action="store_true", help="Analyze only, do not write")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    config = AppConfig.load(args.config)
    setup_logging(config)

    state = StateStore(config.paths.state_db)
    reporter = Reporter(config)
    pipeline = PipelineProcessor(config=config, state_store=state, reporter=reporter)

    try:
        if args.command == "batch":
            _run_batch(config, pipeline, args.dry_run, args.limit)
        elif args.command == "watch":
            _run_watch(config, pipeline, args.dry_run)
        elif args.command == "analyze":
            _run_analyze(config, Path(args.path))
        elif args.command == "process":
            _run_single(config, pipeline, Path(args.path), args.dry_run)
    finally:
        state.close()


def _run_batch(config: AppConfig, pipeline: PipelineProcessor, dry_run: bool, limit: int) -> None:
    files = iter_media_files(config)
    if limit > 0:
        files = files[:limit]
    LOGGER.info("Batch mode: discovered %d candidate files", len(files))

    results = []
    with ThreadPoolExecutor(max_workers=max(1, config.concurrency.max_workers)) as pool:
        futures = [
            pool.submit(pipeline.process_path, path, root, dry_run)
            for path, root in files
        ]
        for future in as_completed(futures):
            results.append(future.result())

    success = sum(1 for r in results if r.status == "success")
    failed = sum(1 for r in results if r.status == "failed")
    skipped = sum(1 for r in results if r.status == "skipped")
    LOGGER.info("Batch complete: success=%d failed=%d skipped=%d", success, failed, skipped)


def _run_watch(config: AppConfig, pipeline: PipelineProcessor, dry_run: bool) -> None:
    watcher = LibraryWatcher(config)

    def _process(path: Path, root: Path) -> None:
        pipeline.process_path(path, root, dry_run_override=dry_run)

    watcher.run_forever(_process)


def _run_analyze(config: AppConfig, path: Path) -> None:
    analyzer = FFprobeAnalyzer(config)
    media, _ = analyzer.analyze(path)
    from reeltranscode.decision_engine import DecisionEngine

    engine = DecisionEngine(config)
    decision, comp = engine.decide(media)
    print(f"File: {path}")
    print(f"Case: {decision.case_label.value}")
    print(f"Strategy: {decision.strategy.value}")
    print(f"Reasons: {'; '.join(decision.reasons)}")
    print(
        "Compatibility: "
        f"container_ok={comp.container_ok} video_ok={comp.video_ok} audio_ok={comp.audio_ok} subtitle_ok={comp.subtitle_ok}"
    )


def _run_single(config: AppConfig, pipeline: PipelineProcessor, path: Path, dry_run: bool) -> None:
    root = _find_root(path, config.watch.folders)
    report = pipeline.process_path(path, root, dry_run_override=dry_run)
    LOGGER.info("Single process complete: status=%s case=%s", report.status, report.case_label)


def _find_root(path: Path, roots: list[Path]) -> Path | None:
    best = None
    for root in roots:
        try:
            path.relative_to(root)
            if best is None or len(str(root)) > len(str(best)):
                best = root
        except ValueError:
            continue
    return best


if __name__ == "__main__":
    main()
