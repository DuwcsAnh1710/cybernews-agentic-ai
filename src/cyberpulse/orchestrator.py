"""Core pipeline orchestrator with explicit state machine, persistence, and bounded retry."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
import logging
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Callable, Literal
import uuid

from cyberpulse.config import Settings
from cyberpulse.models import (
    Article,
    EditorialPackage,
    ResearchResult,
    RunStatus,
    StepLog,
)
from cyberpulse.render_preview import render_markdown
from cyberpulse.storage import (
    add_step_log,
    create_run,
    get_run,
    list_recent_articles,
    save_editorial_package,
    save_research_result,
    save_validation_report,
    update_run_status,
)

logger = logging.getLogger("cyberpulse.orchestrator")

DEGRADED_MARKER = "[DEGRADED]"
_SECRET_PATTERN = re.compile(r"AIza[0-9A-Za-z_\-]{20,}")


class FailureReason(str, Enum):
    """Typed failure reasons for pipeline run termination."""

    insufficient_research = "insufficient_research"
    research_failed = "research_failed"
    editorial_failed = "editorial_failed"
    validation_failed = "validation_failed"
    llm_error = "llm_error"
    interrupted = "interrupted"
    internal_error = "internal_error"


class IllegalTransition(Exception):
    """Raised when an illegal state machine transition is attempted."""

    def __init__(self, from_status: RunStatus, to_status: RunStatus) -> None:
        super().__init__(f"Illegal transition from '{from_status.value}' to '{to_status.value}'")
        self.from_status = from_status
        self.to_status = to_status


# Explicit transition table for orchestrator pipeline runs.
ALLOWED_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.pending: {RunStatus.researching, RunStatus.failed},
    RunStatus.researching: {RunStatus.editing, RunStatus.failed},
    RunStatus.editing: {RunStatus.validating, RunStatus.failed},
    RunStatus.validating: {RunStatus.awaiting_review, RunStatus.failed},
    RunStatus.awaiting_review: set(),
    RunStatus.failed: set(),
    RunStatus.rendering: set(),
    RunStatus.approved: set(),
    RunStatus.exported: set(),
    RunStatus.published: set(),
    RunStatus.partial: set(),
}

# awaiting_review is terminal only for the orchestrator in T5.1a (T8.1 will extend the table).
ORCHESTRATOR_TERMINAL: set[RunStatus] = {RunStatus.awaiting_review, RunStatus.failed}


def sanitize_error_message(msg: str, max_chars: int = 500) -> str:
    """Mask secrets and truncate error text to safe length."""
    if not msg:
        return ""
    masked = _SECRET_PATTERN.sub("[REDACTED]", msg)
    if len(masked) > max_chars:
        return masked[:max_chars]
    return masked


def atomic_write_text(final_path: Path, text: str, encoding: str = "utf-8") -> None:
    """Write text atomically via temporary file replacement.
    
    The temp file name is `.<filename>.tmp` (never ending in .json or .md)
    and is removed on write/replace failure.
    """
    final_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = final_path.parent / f".{final_path.name}.tmp"
    try:
        temp_path.write_text(text, encoding=encoding)
        os.replace(temp_path, final_path)
    except Exception:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise


def generate_run_id(clock_fn: Callable[[], datetime]) -> str:
    """Generate canonical run ID in format run-<timestamp>-<8hex>."""
    now_utc = clock_fn()
    ts = now_utc.strftime("%Y%m%dT%H%M%SZ")
    random_hex = uuid.uuid4().hex[:8]
    return f"run-{ts}-{random_hex}"


def is_run_degraded(conn: sqlite3.Connection, run_id: str) -> bool:
    """Check if a run completed with degraded status by inspecting step_logs."""
    cursor = conn.execute(
        "SELECT 1 FROM step_logs WHERE run_id = ? AND message LIKE ? LIMIT 1",
        (run_id, f"%{DEGRADED_MARKER}%"),
    )
    return cursor.fetchone() is not None


def default_candidate_fetcher(
    conn: sqlite3.Connection,
    settings: Settings,
    clock_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> list[Article]:
    """Default candidate selection matching cli.py parameters."""
    now = clock_fn()
    since = now - timedelta(hours=settings.lookback_hours)
    return list_recent_articles(
        conn,
        since=since,
        limit=settings.research.max_candidates,
    )


def _is_retryable_llm_error(exc: Exception) -> bool:
    """Check if exception is a retryable transient LLM error without importing llm modules."""
    return any(cls.__name__ in ("LLMTransientError", "LLMRateLimitError") for cls in type(exc).__mro__)


def _is_llm_error(exc: Exception) -> bool:
    """Check if exception is any LLMError subclass without importing llm modules."""
    return any(cls.__name__ == "LLMError" for cls in type(exc).__mro__)


def _is_editorial_error(exc: Exception) -> bool:
    """Check if exception is an EditorialError subclass without importing agent modules."""
    return any(cls.__name__ == "EditorialError" for cls in type(exc).__mro__)


def calculate_backoff(attempt: int, backoff_base_sec: float, exc: Exception | None = None) -> float:
    """Calculate exponential backoff delay capped at 60s, honoring retry_after_sec if <= 60."""
    delay = backoff_base_sec * (2 ** (attempt - 1))
    if exc is not None:
        retry_after = getattr(exc, "retry_after_sec", None)
        if isinstance(retry_after, (int, float)) and retry_after <= 60.0:
            delay = max(delay, float(retry_after))
    return min(delay, 60.0)


@dataclass(frozen=True)
class StageOutcome:
    """Outcome of editorial and validation stage."""

    pkg: EditorialPackage | None
    report: Any | None
    failure_reason: FailureReason | None = None
    error_message: str | None = None


@dataclass
class RunResult:
    """Final pipeline run execution result."""

    run_id: str
    status: RunStatus
    failure_reason: FailureReason | None
    degraded: bool
    artifacts: dict[str, Path] = field(default_factory=dict)
    step_attempts: dict[str, int] = field(default_factory=dict)
    error_message: str | None = None


class Orchestrator:
    """State machine orchestrator managing pipeline execution, persistence, and bounded retry."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        settings: Settings,
        research_step: Callable[[str, list[Article]], ResearchResult],
        editorial_step: Callable[[ResearchResult], EditorialPackage],
        validate_step: Callable[[ResearchResult, EditorialPackage], Any],
        candidate_fetcher: Callable[[sqlite3.Connection, Settings], list[Article]] | None = None,
        clock_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleep_fn: Callable[[float], None] = time.sleep,
        max_step_attempts: int = 2,
        allow_insufficient: bool = False,
        backoff_base_sec: float = 5.0,
    ) -> None:
        self.conn = conn
        self.settings = settings
        self.research_step = research_step
        self.editorial_step = editorial_step
        self.validate_step = validate_step
        self.candidate_fetcher = candidate_fetcher
        self.clock_fn = clock_fn
        self.sleep_fn = sleep_fn
        self.max_step_attempts = max(1, max_step_attempts)
        self.allow_insufficient = allow_insufficient
        self.backoff_base_sec = backoff_base_sec
        self._current_status: dict[str, RunStatus] = {}

    def _transition_to(
        self,
        run_id: str,
        target_status: RunStatus,
        error: str | None = None,
        step: str = "orchestrator",
        attempt: int = 1,
        message: str | None = None,
    ) -> None:
        """Check transition legality, update runs status in DB, and append StepLog."""
        current = self._current_status.get(run_id, RunStatus.pending)
        allowed = ALLOWED_TRANSITIONS.get(current, set())
        if target_status not in allowed:
            raise IllegalTransition(current, target_status)

        # 1. Update DB runs row
        update_run_status(self.conn, run_id, target_status, error=error)
        self._current_status[run_id] = target_status

        # 2. Determine StepLog fields
        if message is None:
            if target_status == RunStatus.failed:
                message = error or "Run failed"
            else:
                message = f"Transition to {target_status.value}"

        sanitized_msg = sanitize_error_message(message)
        log_status: Literal["started", "succeeded", "failed", "retrying", "skipped"]
        if target_status == RunStatus.failed:
            log_status = "failed"
        elif target_status == RunStatus.awaiting_review:
            log_status = "succeeded"
        else:
            log_status = "started"

        log_entry = StepLog(
            run_id=run_id,
            step=step,
            status=log_status,
            attempt=attempt,
            message=sanitized_msg,
            created_at=self.clock_fn(),
        )
        add_step_log(self.conn, log_entry)

    def _append_event_log(
        self,
        run_id: str,
        step: str,
        status: Literal["started", "succeeded", "failed", "retrying", "skipped"],
        attempt: int,
        message: str,
    ) -> None:
        """Append non-transition event log entry to step_logs."""
        log_entry = StepLog(
            run_id=run_id,
            step=step,
            status=status,
            attempt=attempt,
            message=sanitize_error_message(message),
            created_at=self.clock_fn(),
        )
        add_step_log(self.conn, log_entry)

    def _mark_failed_best_effort(
        self,
        run_id: str,
        failure_reason: FailureReason,
        error_msg: str,
        step: str = "orchestrator",
        attempt: int = 1,
    ) -> None:
        """Best-effort recording of failure in DB, swallowing secondary DB errors."""
        sanitized = sanitize_error_message(f"[{failure_reason.value}] {error_msg}")
        try:
            self._transition_to(
                run_id=run_id,
                target_status=RunStatus.failed,
                error=sanitized,
                step=step,
                attempt=attempt,
                message=f"[{failure_reason.value}] {error_msg}",
            )
        except Exception as exc:
            logger.warning("Failed to record failure status for run '%s': %s", run_id, exc)

    def _edit_and_validate(
        self,
        run_id: str,
        research: ResearchResult,
        step_attempts: dict[str, int],
        artifacts: dict[str, Path],
    ) -> StageOutcome:
        """Private hook executing editing and validation (T5.1b will wrap in bounded rewrite loop)."""
        # Transition to editing
        self._transition_to(
            run_id=run_id,
            target_status=RunStatus.editing,
            step="editorial",
            attempt=1,
            message="Editorial started",
        )

        pkg: EditorialPackage | None = None
        attempt = 0
        while attempt < self.max_step_attempts:
            attempt += 1
            step_attempts["editorial"] = attempt
            try:
                # Note: A retried editorial step repeats BOTH LLM calls (written draft and scripts draft).
                pkg = self.editorial_step(research)
                break
            except Exception as exc:
                if _is_retryable_llm_error(exc) and attempt < self.max_step_attempts:
                    delay = calculate_backoff(attempt, self.backoff_base_sec, exc)
                    self._append_event_log(
                        run_id=run_id,
                        step="editorial",
                        status="retrying",
                        attempt=attempt,
                        message=f"Editorial retrying after transient error ({delay:.1f}s backoff): {exc}",
                    )
                    self.sleep_fn(delay)
                    continue

                # Non-retryable or retries exhausted
                reason = FailureReason.llm_error if _is_llm_error(exc) else (
                    FailureReason.editorial_failed if _is_editorial_error(exc) else FailureReason.internal_error
                )
                return StageOutcome(
                    pkg=None,
                    report=None,
                    failure_reason=reason,
                    error_message=sanitize_error_message(str(exc)),
                )

        if pkg is None:
            return StageOutcome(
                pkg=None,
                report=None,
                failure_reason=FailureReason.editorial_failed,
                error_message="Editorial returned no package",
            )

        if pkg.run_id != run_id:
            return StageOutcome(
                pkg=None,
                report=None,
                failure_reason=FailureReason.internal_error,
                error_message=f"Editorial package run_id mismatch: expected '{run_id}', got '{pkg.run_id}'",
            )

        # Persist editorial package in SQLite contents
        save_editorial_package(self.conn, pkg)

        # Write editorial artifacts atomically
        preview_md = render_markdown(pkg, research)
        editorial_dir = self.settings.paths.output_dir / "editorial"
        json_path = editorial_dir / f"{run_id}.json"
        md_path = editorial_dir / f"{run_id}.md"

        atomic_write_text(json_path, pkg.model_dump_json(indent=2))
        atomic_write_text(md_path, preview_md)
        artifacts["editorial_json"] = json_path
        artifacts["editorial_md"] = md_path

        self._append_event_log(
            run_id=run_id,
            step="editorial",
            status="succeeded",
            attempt=attempt,
            message="Editorial completed successfully",
        )

        # Transition to validating
        self._transition_to(
            run_id=run_id,
            target_status=RunStatus.validating,
            step="validate",
            attempt=1,
            message="Validation started",
        )
        step_attempts["validate"] = 1

        try:
            report = self.validate_step(research, pkg)
        except Exception as exc:
            return StageOutcome(
                pkg=pkg,
                report=None,
                failure_reason=FailureReason.internal_error,
                error_message=sanitize_error_message(f"Validator raised unexpected exception: {exc}"),
            )

        # Persist validation report in contents
        save_validation_report(self.conn, run_id, report)

        # Write validation artifact atomically
        validation_dir = self.settings.paths.output_dir / "validation"
        report_path = validation_dir / f"{run_id}.json"
        report_json = report.model_dump_json(indent=2) if hasattr(report, "model_dump_json") else str(report)
        atomic_write_text(report_path, report_json)
        artifacts["validation"] = report_path

        # Gate on report.passed ONLY (M1)
        passed = getattr(report, "passed", False)
        if not passed:
            self._append_event_log(
                run_id=run_id,
                step="validate",
                status="failed",
                attempt=1,
                message="Validation failed with blocking issues",
            )
            return StageOutcome(
                pkg=pkg,
                report=report,
                failure_reason=FailureReason.validation_failed,
                error_message="Validation checks failed",
            )

        self._append_event_log(
            run_id=run_id,
            step="validate",
            status="succeeded",
            attempt=1,
            message="Validation passed",
        )
        return StageOutcome(
            pkg=pkg,
            report=report,
            failure_reason=None,
            error_message=None,
        )

    def run(self, run_id: str | None = None) -> RunResult:
        """Execute one complete pipeline run attempt."""
        # M9: Caller-supplied run_id pre-check before any side effect
        if run_id is not None:
            existing = get_run(self.conn, run_id)
            if existing is not None:
                raise ValueError(f"Run ID '{run_id}' already exists in database")
        else:
            run_id = generate_run_id(self.clock_fn)

        artifacts: dict[str, Path] = {}
        step_attempts: dict[str, int] = {"research": 0, "editorial": 0, "validate": 0}
        degraded = False

        # Step 3a: Create the runs row with status 'pending' before any work
        create_run(self.conn, run_id=run_id)
        self._current_status[run_id] = RunStatus.pending

        # StepLog for creation
        self._append_event_log(
            run_id=run_id,
            step="orchestrator",
            status="started",
            attempt=1,
            message="Run created with status pending",
        )

        try:
            # Candidate article selection (M2, M3)
            if self.candidate_fetcher is not None:
                candidates = self.candidate_fetcher(self.conn, self.settings)
            else:
                candidates = default_candidate_fetcher(self.conn, self.settings, self.clock_fn)

            if not candidates:
                self._mark_failed_best_effort(
                    run_id=run_id,
                    failure_reason=FailureReason.insufficient_research,
                    error_msg="no candidates",
                    step="orchestrator",
                    attempt=1,
                )
                return RunResult(
                    run_id=run_id,
                    status=RunStatus.failed,
                    failure_reason=FailureReason.insufficient_research,
                    degraded=False,
                    artifacts=artifacts,
                    step_attempts=step_attempts,
                    error_message="no candidates",
                )

            # Step 3b: Transition to researching
            self._transition_to(
                run_id=run_id,
                target_status=RunStatus.researching,
                step="research",
                attempt=1,
                message="Research started",
            )

            research_result: ResearchResult | None = None
            attempt = 0
            while attempt < self.max_step_attempts:
                attempt += 1
                step_attempts["research"] = attempt
                try:
                    research_result = self.research_step(run_id, candidates)
                    break
                except Exception as exc:
                    if _is_retryable_llm_error(exc) and attempt < self.max_step_attempts:
                        delay = calculate_backoff(attempt, self.backoff_base_sec, exc)
                        self._append_event_log(
                            run_id=run_id,
                            step="research",
                            status="retrying",
                            attempt=attempt,
                            message=f"Research retrying after transient error ({delay:.1f}s backoff): {exc}",
                        )
                        self.sleep_fn(delay)
                        continue

                    # Non-retryable or retries exhausted
                    reason = FailureReason.llm_error if _is_llm_error(exc) else FailureReason.internal_error
                    err_msg = sanitize_error_message(str(exc))
                    self._mark_failed_best_effort(
                        run_id=run_id,
                        failure_reason=reason,
                        error_msg=err_msg,
                        step="research",
                        attempt=attempt,
                    )
                    return RunResult(
                        run_id=run_id,
                        status=RunStatus.failed,
                        failure_reason=reason,
                        degraded=False,
                        artifacts=artifacts,
                        step_attempts=step_attempts,
                        error_message=err_msg,
                    )

            if research_result is None:
                self._mark_failed_best_effort(
                    run_id=run_id,
                    failure_reason=FailureReason.internal_error,
                    error_msg="Research returned None",
                    step="research",
                    attempt=attempt,
                )
                return RunResult(
                    run_id=run_id,
                    status=RunStatus.failed,
                    failure_reason=FailureReason.internal_error,
                    degraded=False,
                    artifacts=artifacts,
                    step_attempts=step_attempts,
                    error_message="Research returned None",
                )

            # M9: Validate run_id consistency
            if research_result.run_id != run_id:
                err = f"Research result run_id mismatch: expected '{run_id}', got '{research_result.run_id}'"
                self._mark_failed_best_effort(
                    run_id=run_id,
                    failure_reason=FailureReason.internal_error,
                    error_msg=err,
                    step="research",
                    attempt=attempt,
                )
                return RunResult(
                    run_id=run_id,
                    status=RunStatus.failed,
                    failure_reason=FailureReason.internal_error,
                    degraded=False,
                    artifacts=artifacts,
                    step_attempts=step_attempts,
                    error_message=err,
                )

            # M3: Research policy enforcement
            kept_count = len(research_result.items)
            is_insufficient = research_result.status != "ok"

            if kept_count == 0:
                # Kept items == 0 always fails even if allow_insufficient is True
                err = "Insufficient research: 0 articles kept"
                self._mark_failed_best_effort(
                    run_id=run_id,
                    failure_reason=FailureReason.insufficient_research,
                    error_msg=err,
                    step="research",
                    attempt=attempt,
                )
                return RunResult(
                    run_id=run_id,
                    status=RunStatus.failed,
                    failure_reason=FailureReason.insufficient_research,
                    degraded=False,
                    artifacts=artifacts,
                    step_attempts=step_attempts,
                    error_message=err,
                )

            if is_insufficient and not self.allow_insufficient:
                err = f"Insufficient research: requested {research_result.requested_count}, got {kept_count}"
                self._mark_failed_best_effort(
                    run_id=run_id,
                    failure_reason=FailureReason.insufficient_research,
                    error_msg=err,
                    step="research",
                    attempt=attempt,
                )
                return RunResult(
                    run_id=run_id,
                    status=RunStatus.failed,
                    failure_reason=FailureReason.insufficient_research,
                    degraded=False,
                    artifacts=artifacts,
                    step_attempts=step_attempts,
                    error_message=err,
                )

            # Research succeeded (either ok or degraded)
            if is_insufficient and self.allow_insufficient:
                degraded = True

            # Persist research result in SQLite (articles, run_articles, contents)
            save_research_result(self.conn, research_result)

            # Write research artifact atomically
            research_dir = self.settings.paths.output_dir / "research"
            research_json_path = research_dir / f"{run_id}.json"
            atomic_write_text(research_json_path, research_result.model_dump_json(indent=2))
            artifacts["research"] = research_json_path

            if degraded:
                self._append_event_log(
                    run_id=run_id,
                    step="research",
                    status="succeeded",
                    attempt=attempt,
                    message=f"{DEGRADED_MARKER} Insufficient research articles: requested {research_result.requested_count}, got {kept_count} (allow_insufficient=True)",
                )
            else:
                self._append_event_log(
                    run_id=run_id,
                    step="research",
                    status="succeeded",
                    attempt=attempt,
                    message="Research completed successfully",
                )

            # Steps 3d & 3e: Editing and Validating
            stage_outcome = self._edit_and_validate(
                run_id=run_id,
                research=research_result,
                step_attempts=step_attempts,
                artifacts=artifacts,
            )

            if stage_outcome.failure_reason is not None:
                step_name = "editorial" if stage_outcome.pkg is None else "validate"
                step_att = step_attempts.get(step_name, 1)
                self._mark_failed_best_effort(
                    run_id=run_id,
                    failure_reason=stage_outcome.failure_reason,
                    error_msg=stage_outcome.error_message or "Stage failed",
                    step=step_name,
                    attempt=step_att,
                )
                return RunResult(
                    run_id=run_id,
                    status=RunStatus.failed,
                    failure_reason=stage_outcome.failure_reason,
                    degraded=degraded,
                    artifacts=artifacts,
                    step_attempts=step_attempts,
                    error_message=stage_outcome.error_message,
                )

            # Happy path: transition to awaiting_review
            self._transition_to(
                run_id=run_id,
                target_status=RunStatus.awaiting_review,
                step="orchestrator",
                attempt=1,
                message="Run completed awaiting review",
            )

            return RunResult(
                run_id=run_id,
                status=RunStatus.awaiting_review,
                failure_reason=None,
                degraded=degraded,
                artifacts=artifacts,
                step_attempts=step_attempts,
                error_message=None,
            )

        except KeyboardInterrupt as ki:
            # Rule 5 / Acceptance Criterion 9: KeyboardInterrupt marks failed(interrupted) and re-raises
            self._mark_failed_best_effort(
                run_id=run_id,
                failure_reason=FailureReason.interrupted,
                error_msg="Pipeline interrupted by user",
                step="orchestrator",
                attempt=1,
            )
            raise ki
        except Exception as exc:
            # Rule 5 / Acceptance Criterion 8: Unexpected exceptions end in failed(internal_error)
            err_msg = sanitize_error_message(str(exc))
            self._mark_failed_best_effort(
                run_id=run_id,
                failure_reason=FailureReason.internal_error,
                error_msg=err_msg,
                step="orchestrator",
                attempt=1,
            )
            return RunResult(
                run_id=run_id,
                status=RunStatus.failed,
                failure_reason=FailureReason.internal_error,
                degraded=degraded,
                artifacts=artifacts,
                step_attempts=step_attempts,
                error_message=err_msg,
            )
