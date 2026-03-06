from __future__ import annotations

import logging
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from reeltranscode.analyzer import FFprobeAnalyzer, ProbeError
from reeltranscode.config import AppConfig
from reeltranscode.decision_engine import DecisionEngine
from reeltranscode.ffmpeg_runner import CommandFailedError, FFmpegRunner
from reeltranscode.models import JobReport, JobStatus
from reeltranscode.planner import CommandPlanner
from reeltranscode.reporter import Reporter
from reeltranscode.retry import run_with_retry
from reeltranscode.state_store import StateStore
from reeltranscode.utils import atomic_replace, ensure_parent, now_utc_iso
from reeltranscode.validator import OutputValidator

LOGGER = logging.getLogger(__name__)


class PipelineProcessor:
    def __init__(self, config: AppConfig, state_store: StateStore, reporter: Reporter):
        self.config = config
        self.state_store = state_store
        self.reporter = reporter
        self.analyzer = FFprobeAnalyzer(config)
        self.engine = DecisionEngine(config)
        self.planner = CommandPlanner(config)
        self.runner = FFmpegRunner()
        self.validator = OutputValidator(config)

    def process_path(self, path: Path, source_root: Path | None, dry_run_override: bool | None = None) -> JobReport:
        dry_run = self.config.dry_run if dry_run_override is None else dry_run_override
        started_monotonic = time.monotonic()
        started_at = now_utc_iso()
        job_id = uuid.uuid4().hex

        ffmpeg_commands: list[list[str]] = []
        probe_command: list[str] = []
        validations: list[str] = []
        target_path: Path | None = None
        temp_path: Path | None = None
        external_subtitle_outputs: list[Path] = []

        stat = path.stat()
        device = stat.st_dev
        inode = stat.st_ino
        size = stat.st_size
        mtime_ns = stat.st_mtime_ns

        error_class: str | None = None
        error_message: str | None = None
        status = JobStatus.RUNNING
        decision = None
        stream_fp = ""
        metadata_fp = ""
        strategy_override: str | None = None
        case_label_override: str | None = None
        reasons_override: list[str] | None = None
        expected_safe_override: bool | None = None

        try:
            # Record a running job row before probing so probe failures are visible in status/jobs.
            self.state_store.mark_job_started(
                job_id,
                path,
                None,
                "analyze",
                "ANALYZE",
                "",
                "",
            )

            media, probe_command = self.analyzer.analyze(path)
            stream_fp = self.analyzer.stream_fingerprint(media)
            metadata_fp = self.analyzer.metadata_fingerprint(media)

            should_skip, reason = self.state_store.should_skip(path, stream_fp, metadata_fp, size, mtime_ns)
            if should_skip:
                self.state_store.mark_job_started(
                    job_id,
                    path,
                    None,
                    "skip",
                    "STATE_SKIP",
                    stream_fp,
                    metadata_fp,
                )
                status = JobStatus.SKIPPED
                strategy_override = "skip"
                case_label_override = "STATE_SKIP"
                reasons_override = [f"Skipped due to state dedupe: {reason}"]
                expected_safe_override = True
            else:
                decision, compatibility = self.engine.decide(media)

                if (
                    self.config.validation.require_dv_preservation
                    and compatibility.dv_present
                    and decision.case_label.value == "F_DOLBY_VISION_FRAGILE"
                    and not decision.use_dovi_muxer
                ):
                    target_path = self.planner.preview_target_path(path, source_root)
                    quarantine_note = self._quarantine_incompatible_existing_target(
                        source_media=media,
                        decision=decision,
                        target_path=target_path,
                    )
                    self.state_store.mark_job_started(
                        job_id,
                        path,
                        target_path,
                        "skip",
                        "DV_STRICT_SKIP",
                        stream_fp,
                        metadata_fp,
                    )
                    status = JobStatus.SKIPPED
                    strategy_override = "skip"
                    case_label_override = "DV_STRICT_SKIP"
                    reasons_override = [
                        "Skipped: Dolby Vision preservation is required, but the current MP4 path is flagged as fragile."
                    ]
                    for reason in decision.reasons:
                        if reason not in reasons_override:
                            reasons_override.append(reason)
                    if quarantine_note:
                        reasons_override.append(quarantine_note)
                    expected_safe_override = True
                    return self._finalize_report(
                        job_id=job_id,
                        path=path,
                        target_path=target_path,
                        started_at=started_at,
                        started_monotonic=started_monotonic,
                        decision=decision,
                        strategy_override=strategy_override,
                        case_label_override=case_label_override,
                        reasons_override=reasons_override,
                        expected_safe_override=expected_safe_override,
                        status=status,
                        probe_command=probe_command,
                        ffmpeg_commands=ffmpeg_commands,
                        validations=validations,
                        stream_fp=stream_fp,
                        metadata_fp=metadata_fp,
                        error_class=error_class,
                        error_message=error_message,
                        device=device,
                        inode=inode,
                        size=size,
                        mtime_ns=mtime_ns,
                    )

                plan = self.planner.build(media, decision, compatibility, source_root)
                target_path = plan.target_path
                temp_path = plan.temp_path
                external_subtitle_outputs = list(plan.external_subtitle_outputs)

                if (
                    target_path
                    and target_path.exists()
                    and not self.config.output.overwrite
                    and decision.strategy.value != "no_op"
                ):
                    self.state_store.mark_job_started(
                        job_id,
                        path,
                        target_path,
                        "skip",
                        "TARGET_EXISTS",
                        stream_fp,
                        metadata_fp,
                    )
                    status = JobStatus.SKIPPED
                    strategy_override = "skip"
                    case_label_override = "TARGET_EXISTS"
                    reasons_override = [f"Skipped because target exists and overwrite=false: {target_path}"]
                    expected_safe_override = True
                    return self._finalize_report(
                        job_id=job_id,
                        path=path,
                        target_path=target_path,
                        started_at=started_at,
                        started_monotonic=started_monotonic,
                        decision=decision,
                        strategy_override=strategy_override,
                        case_label_override=case_label_override,
                        reasons_override=reasons_override,
                        expected_safe_override=expected_safe_override,
                        status=status,
                        probe_command=probe_command,
                        ffmpeg_commands=ffmpeg_commands,
                        validations=validations,
                        stream_fp=stream_fp,
                        metadata_fp=metadata_fp,
                        error_class=error_class,
                        error_message=error_message,
                        device=device,
                        inode=inode,
                        size=size,
                        mtime_ns=mtime_ns,
                    )

                self.state_store.mark_job_started(
                    job_id,
                    path,
                    target_path,
                    decision.strategy.value,
                    decision.case_label.value,
                    stream_fp,
                    metadata_fp,
                )

                if decision.strategy.value == "no_op":
                    status = JobStatus.SUCCESS
                    validations.append("No-op: source already compliant")
                elif dry_run:
                    status = JobStatus.SKIPPED
                    validations.append("Dry-run: execution skipped")
                    ffmpeg_commands = [step.command for step in plan.steps]
                else:
                    ffmpeg_commands = [step.command for step in plan.steps]
                    for step in plan.steps:
                        result = run_with_retry(lambda cmd=step.command: self.runner.run(cmd), self.config.retry)
                        missing_outputs = [output for output in step.expected_outputs if not output.exists()]
                        if missing_outputs:
                            missing_text = ", ".join(str(output) for output in missing_outputs)
                            details = " | ".join(
                                part
                                for part in [result.stdout.strip(), result.stderr.strip()]
                                if part
                            )
                            if details:
                                raise RuntimeError(
                                    f"Step '{step.name}' completed without expected outputs: {missing_text}. "
                                    f"tool output: {details}"
                                )
                            raise RuntimeError(f"Step '{step.name}' completed without expected outputs: {missing_text}")

                    if plan.target_path and self.config.validation.run_post_ffprobe:
                        validation_path = plan.temp_path if plan.temp_path and plan.temp_path.exists() else plan.target_path
                        output_media, _ = self.analyzer.analyze(validation_path)
                        validation = self.validator.validate(media, output_media, decision, plan=plan)
                        if validation.ok:
                            validations.append("Validation passed")
                        else:
                            validations.extend(validation.reasons)
                            raise RuntimeError("Validation failed: " + "; ".join(validation.reasons))

                    # Commit temporary output to final target only after successful validation.
                    if plan.temp_path and plan.target_path:
                        ensure_parent(plan.target_path)
                        atomic_replace(plan.temp_path, plan.target_path)
                        temp_path = None
                        self._post_success_source_handling(path, plan.target_path, source_root)
                    status = JobStatus.SUCCESS

        except (ProbeError, CommandFailedError, RuntimeError, OSError) as exc:
            status = JobStatus.FAILED
            error_class = exc.__class__.__name__
            error_message = str(exc)
            LOGGER.exception("Job failed for %s", path)
            if temp_path and temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    LOGGER.warning("Unable to remove temporary output after failure: %s", temp_path)
            for subtitle_path in external_subtitle_outputs:
                if subtitle_path.exists():
                    try:
                        subtitle_path.unlink()
                    except OSError:
                        LOGGER.warning("Unable to remove subtitle sidecar after failure: %s", subtitle_path)
        return self._finalize_report(
            job_id=job_id,
            path=path,
            target_path=target_path,
            started_at=started_at,
            started_monotonic=started_monotonic,
            decision=decision,
            strategy_override=strategy_override,
            case_label_override=case_label_override,
            reasons_override=reasons_override,
            expected_safe_override=expected_safe_override,
            status=status,
            probe_command=probe_command,
            ffmpeg_commands=ffmpeg_commands,
            validations=validations,
            stream_fp=stream_fp,
            metadata_fp=metadata_fp,
            error_class=error_class,
            error_message=error_message,
            device=device,
            inode=inode,
            size=size,
            mtime_ns=mtime_ns,
        )

    def _finalize_report(
        self,
        job_id: str,
        path: Path,
        target_path: Path | None,
        started_at: str,
        started_monotonic: float,
        decision,
        strategy_override: str | None,
        case_label_override: str | None,
        reasons_override: list[str] | None,
        expected_safe_override: bool | None,
        status: JobStatus,
        probe_command: list[str],
        ffmpeg_commands: list[list[str]],
        validations: list[str],
        stream_fp: str,
        metadata_fp: str,
        error_class: str | None,
        error_message: str | None,
        device: int,
        inode: int,
        size: int,
        mtime_ns: int,
    ) -> JobReport:
        finished_at = now_utc_iso()
        duration_seconds = time.monotonic() - started_monotonic

        strategy = strategy_override or (decision.strategy.value if decision else "analysis_failed")
        case_label = case_label_override or (decision.case_label.value if decision else "UNKNOWN")
        reasons = reasons_override if reasons_override is not None else (
            decision.reasons if decision else ["Failed before decision stage"]
        )
        expected_direct_play_safe = (
            expected_safe_override
            if expected_safe_override is not None
            else (decision.expected_direct_play_safe if decision else False)
        )
        report = JobReport(
            job_id=job_id,
            source_path=str(path),
            target_path=str(target_path) if target_path else None,
            strategy=strategy,
            case_label=case_label,
            status=status.value,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration_seconds,
            reasons=reasons,
            ffprobe_command=probe_command,
            ffmpeg_commands=ffmpeg_commands,
            expected_direct_play_safe=expected_direct_play_safe,
            validations=validations,
            stream_fingerprint=stream_fp,
            metadata_fingerprint=metadata_fp,
            dv_fallback_applied=decision.dv_fallback_applied if decision else False,
            dv_fallback_reason=decision.dv_fallback_reason if decision else None,
            error_class=error_class,
            error_message=error_message,
        )
        report_path = self.reporter.write_job_report(report)
        self.state_store.mark_job_finished(job_id, status, error_class, error_message, report_path)
        if stream_fp and metadata_fp:
            self.state_store.upsert_file_state(
                path,
                device,
                inode,
                size,
                mtime_ns,
                stream_fp,
                metadata_fp,
                status,
                job_id,
            )
        return report

    def _quarantine_incompatible_existing_target(
        self,
        source_media,
        decision,
        target_path: Path | None,
    ) -> str | None:
        if target_path is None or not target_path.exists():
            return None

        try:
            existing_media, _ = self.analyzer.analyze(target_path)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Unable to inspect existing target during DV strict skip: %s", exc)
            return None

        validation = self.validator.validate(source_media, existing_media, decision)
        dv_loss = any("Dolby Vision lost" in reason for reason in validation.reasons)
        if not dv_loss:
            return None

        quarantine_path = self._build_quarantine_path(target_path)
        ensure_parent(quarantine_path)
        shutil.move(str(target_path), str(quarantine_path))
        LOGGER.warning(
            "Quarantined stale target after DV strict skip: %s -> %s",
            target_path,
            quarantine_path,
        )
        return f"Existing incompatible output was quarantined: {quarantine_path}"

    @staticmethod
    def _build_quarantine_path(target_path: Path) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        quarantine_dir = target_path.parent / "_quarantine"
        return quarantine_dir / f"{target_path.stem}.dv-invalid-{stamp}{target_path.suffix}"

    def _post_success_source_handling(self, source: Path, target: Path, source_root: Path | None) -> None:
        mode = self.config.output.mode
        if mode == "archive_original":
            archive_target = self._archive_path(source, source_root)
            ensure_parent(archive_target)
            shutil.move(str(source), str(archive_target))
            return

        if mode == "replace_original":
            if source != target and source.exists():
                source.unlink()
            return

        if self.config.output.delete_original_after_success and source.exists() and source != target:
            source.unlink()

    def _archive_path(self, source: Path, source_root: Path | None) -> Path:
        if source_root:
            try:
                relative = source.relative_to(source_root)
            except ValueError:
                relative = Path(source.name)
        else:
            relative = Path(source.name)
        return (self.config.output.archive_root / relative).resolve()
