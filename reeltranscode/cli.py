from __future__ import annotations

import argparse
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import yaml

from reeltranscode.analyzer import FFprobeAnalyzer
from reeltranscode.config import AppConfig
from reeltranscode.pipeline import PipelineProcessor
from reeltranscode.reporter import Reporter
from reeltranscode.scanner import iter_media_files
from reeltranscode.state_store import StateStore
from reeltranscode.tooling import ToolchainResolver
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

    status = sub.add_parser("status", help="Show latest jobs and status summary")
    status.add_argument("--json", action="store_true", dest="json_output", help="Output machine-readable JSON")
    status.add_argument("--limit", type=int, default=50, help="Max number of latest jobs to return")

    export_cfg = sub.add_parser("config-export", help="Export normalized config with defaults")
    export_cfg.add_argument("--json", action="store_true", dest="json_output", help="Output machine-readable JSON")

    validate_cfg = sub.add_parser("config-validate", help="Validate configuration file")
    validate_cfg.add_argument("--json", action="store_true", dest="json_output", help="Output machine-readable JSON")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "config-validate":
        _run_config_validate(Path(args.config), args.json_output)
        return

    config = AppConfig.load(args.config)
    setup_logging(config)

    state = StateStore(config.paths.state_db)
    pipeline: PipelineProcessor | None = None

    try:
        if args.command in {"batch", "watch", "process"}:
            reporter = Reporter(config)
            pipeline = PipelineProcessor(config=config, state_store=state, reporter=reporter)

        if args.command == "batch":
            assert pipeline is not None
            _run_batch(config, pipeline, args.dry_run, args.limit)
        elif args.command == "watch":
            assert pipeline is not None
            _run_watch(config, pipeline, args.dry_run)
        elif args.command == "analyze":
            _run_analyze(config, Path(args.path))
        elif args.command == "process":
            assert pipeline is not None
            _run_single(config, pipeline, Path(args.path), args.dry_run)
        elif args.command == "status":
            _run_status(config, state, args.limit, args.json_output)
        elif args.command == "config-export":
            _run_config_export(config, args.json_output)
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
        report = pipeline.process_path(path, root, dry_run_override=dry_run)
        reason = "; ".join(report.reasons[:3]) if report.reasons else "-"
        LOGGER.info(
            "Watch processed: status=%s case=%s source=%s target=%s reason=%s",
            report.status,
            report.case_label,
            report.source_path,
            report.target_path or "-",
            reason,
        )

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


def _run_status(config: AppConfig, state: StateStore, limit: int, json_output: bool) -> None:
    payload = state.status_snapshot(limit=limit)
    payload["api_version"] = 1
    payload["paths"] = {
        "state_db": str(config.paths.state_db),
        "reports_dir": str(config.paths.reports_dir),
        "csv_summary": str(config.paths.csv_summary),
    }
    payload["capabilities"] = ToolchainResolver(config).resolve_dolby_vision_mux_capabilities().as_json()
    if json_output:
        _emit_json(payload)
        return

    summary = payload["summary"]
    print(
        "Summary: "
        f"total={summary['total']} pending={summary['pending']} running={summary['running']} "
        f"success={summary['success']} failed={summary['failed']} skipped={summary['skipped']}"
    )
    dv_caps = payload["capabilities"]
    print(
        "Capabilities: "
        f"dv_mp4_safe_mux={dv_caps['dv_mp4_safe_mux']} missing_tools={','.join(dv_caps['missing_tools']) or '-'}"
    )
    for job in payload["latest_jobs"]:
        print(
            f"[{job['status']}] {job['job_id']} case={job['case_label']} strategy={job['strategy']} "
            f"source={job['source_path']}"
        )


def _run_config_export(config: AppConfig, json_output: bool) -> None:
    payload = {
        "api_version": 1,
        "config": config.to_dict(),
    }
    if json_output:
        _emit_json(payload)
        return
    print(yaml.safe_dump(payload["config"], sort_keys=False))


def _run_config_validate(config_path: Path, json_output: bool) -> None:
    errors: list[dict[str, str]] = []

    def _add_error(field: str, message: str) -> None:
        errors.append({"field": field, "message": message})

    raw: dict[str, Any] | None = None
    try:
        with config_path.expanduser().open("r", encoding="utf-8") as handle:
            parsed = yaml.safe_load(handle)
        if parsed is None:
            raw = {}
        elif isinstance(parsed, dict):
            raw = parsed
        else:
            _add_error("config", "root YAML object must be a mapping")
    except FileNotFoundError:
        _add_error("config", f"file not found: {config_path}")
    except yaml.YAMLError as exc:
        _add_error("config", f"invalid YAML: {exc}")
    except OSError as exc:
        _add_error("config", str(exc))

    if raw is not None:
        try:
            cfg = AppConfig.from_dict(raw)
            errors.extend(cfg.validate())
        except (TypeError, ValueError) as exc:
            _add_error("config", f"invalid value type: {exc}")
        except Exception as exc:
            _add_error("config", f"validation error: {exc}")

    payload = {
        "api_version": 1,
        "valid": len(errors) == 0,
        "errors": errors,
    }

    if json_output:
        _emit_json(payload)
        return

    if payload["valid"]:
        print("Config valid")
    else:
        print("Config invalid")
        for err in errors:
            print(f"- {err['field']}: {err['message']}")


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


def _emit_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
