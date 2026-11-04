"""Autonomous Docker Scheduler Daemon for CyberPulse AI (Task T12).

Provides continuous 24/7 background orchestration for RSS intelligence collection,
AI editorial synthesis, podcast audio and SRT subtitles, short-form 9:16 video rendering,
static portal deployment, and multi-channel instant notifications.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import signal
import threading
from typing import Any

from cyberpulse.config import Settings, load_settings
from cyberpulse.orchestrator import generate_run_id
from cyberpulse.runlock import is_pid_alive, read_lock_info

logger = logging.getLogger("cyberpulse.scheduler")


def get_scheduler_status_path(output_dir: Path | str) -> Path:
    """Return filesystem path to scheduler_status.json."""
    return Path(output_dir) / "scheduler_status.json"


def read_scheduler_status(output_dir: Path | str) -> dict[str, Any] | None:
    """Read and deserialize scheduler status if present, returning None if missing or corrupt."""
    status_file = get_scheduler_status_path(output_dir)
    if not status_file.exists():
        return None
    try:
        content = status_file.read_text(encoding="utf-8")
        return json.loads(content)
    except Exception as err:
        logger.warning("[CYBERPULSE INTEL] Failed to read scheduler status: %s", err)
        return None


def write_scheduler_status(output_dir: Path | str, status_data: dict[str, Any]) -> None:
    """Write scheduler status atomically via temporary file replacement."""
    status_file = get_scheduler_status_path(output_dir)
    status_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = status_file.with_suffix(".tmp")
    try:
        tmp_file.write_text(
            json.dumps(status_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp_file, status_file)
    except Exception as err:
        logger.error("[CYBERPULSE INTEL] Failed to write scheduler status: %s", err)
        if tmp_file.exists():
            try:
                tmp_file.unlink()
            except OSError:
                pass


def find_latest_editorial_run_id(output_dir: Path | str) -> str | None:
    """Discover newest published editorial package run_id."""
    import re

    editorial_dir = Path(output_dir) / "editorial"
    if not editorial_dir.exists():
        return None
    candidates = [p for p in editorial_dir.glob("*.json") if not p.name.endswith(".original.json")]
    if not candidates:
        return None

    def _sort_key(p: Path) -> tuple[str, float]:
        m = re.search(r"^(?:run|standalone)-(\d{8}T\d{6}Z)", p.stem)
        ts = m.group(1) if m else ""
        return (ts, p.stat().st_mtime)

    sorted_candidates = sorted(candidates, key=_sort_key, reverse=True)
    return sorted_candidates[0].stem


class PipelineScheduler:
    """Autonomous scheduler daemon executing the 5-stage automated intelligence pipeline."""

    def __init__(
        self,
        settings: Settings | None = None,
        settings_path: Path | str = Path("config/settings.toml"),
        sources_path: Path | str = Path("config/sources.toml"),
        dist_dir: Path | str = Path("dist"),
        interval_hours: float | None = None,
        interval_seconds: float | None = None,
        run_pipeline: bool | None = None,
        generate_podcast: bool | None = None,
        generate_video: bool | None = None,
        build_portal: bool | None = None,
        dispatch_notifications: bool | None = None,
        clock_fn: Callable[[], datetime] | None = None,
        pipeline_step_fn: Callable[[str], int | None] | None = None,
        podcast_step_fn: Callable[[str], int | None] | None = None,
        video_step_fn: Callable[[str], int | None] | None = None,
        portal_step_fn: Callable[[str], int | None] | None = None,
        dispatch_step_fn: Callable[[str], int | None] | None = None,
    ) -> None:
        self.settings_path = Path(settings_path)
        self.sources_path = Path(sources_path)
        self.dist_dir = Path(dist_dir)
        self.clock_fn = clock_fn or (lambda: datetime.now(timezone.utc))

        if settings is not None:
            self.settings = settings
        else:
            self.settings = load_settings(self.settings_path)

        sched_cfg = getattr(self.settings, "scheduler", None)

        # Interval calculation
        if interval_seconds is not None:
            self.interval_seconds = float(interval_seconds)
            self.interval_hours = self.interval_seconds / 3600.0
        elif interval_hours is not None:
            self.interval_hours = float(interval_hours)
            self.interval_seconds = self.interval_hours * 3600.0
        elif sched_cfg is not None:
            self.interval_hours = float(sched_cfg.interval_hours)
            self.interval_seconds = self.interval_hours * 3600.0
        else:
            self.interval_hours = 12.0
            self.interval_seconds = 12.0 * 3600.0

        # Step feature flags
        self.run_pipeline = (
            run_pipeline
            if run_pipeline is not None
            else (sched_cfg.run_pipeline if sched_cfg else True)
        )
        self.generate_podcast = (
            generate_podcast
            if generate_podcast is not None
            else (sched_cfg.generate_podcast if sched_cfg else True)
        )
        self.generate_video = (
            generate_video
            if generate_video is not None
            else (sched_cfg.generate_video if sched_cfg else True)
        )
        self.build_portal = (
            build_portal
            if build_portal is not None
            else (sched_cfg.build_portal if sched_cfg else True)
        )
        self.dispatch_notifications = (
            dispatch_notifications
            if dispatch_notifications is not None
            else (sched_cfg.dispatch_notifications if sched_cfg else True)
        )

        # Injectable steps (defaults call internal implementations)
        self._pipeline_step_fn = pipeline_step_fn
        self._podcast_step_fn = podcast_step_fn
        self._video_step_fn = video_step_fn
        self._portal_step_fn = portal_step_fn
        self._dispatch_step_fn = dispatch_step_fn

        # Runtime daemon state
        self._running = False
        self._stop_event = threading.Event()
        self.runs_completed = 0
        self.last_run_id: str | None = None
        self.last_run_time: datetime | None = None
        self.last_status: str | None = None
        self.last_error: str | None = None
        self.next_run_time: datetime | None = None

        # Restore previous completed count if status file exists
        prev = read_scheduler_status(self.settings.paths.output_dir)
        if prev:
            self.runs_completed = int(prev.get("runs_completed", 0))
            self.last_run_id = prev.get("last_run_id")
            self.last_status = prev.get("last_status")
            self.last_error = prev.get("last_error")
            l_time = prev.get("last_run_time")
            if l_time:
                try:
                    self.last_run_time = datetime.fromisoformat(l_time)
                except Exception:
                    pass

    @property
    def output_dir(self) -> Path:
        """Base output directory from application settings."""
        return Path(self.settings.paths.output_dir)

    def _get_config_dict(self) -> dict[str, Any]:
        """Return active scheduler feature flags and configuration."""
        return {
            "run_pipeline": self.run_pipeline,
            "generate_podcast": self.generate_podcast,
            "generate_video": self.generate_video,
            "build_portal": self.build_portal,
            "dispatch_notifications": self.dispatch_notifications,
        }

    def _update_status(
        self,
        status: str,
        current_step: str | None = None,
        next_run_time: datetime | None = None,
        last_status: str | None = None,
        last_error: str | None = None,
    ) -> None:
        """Update in-memory state and persist scheduler_status.json atomically."""
        if last_status is not None:
            self.last_status = last_status
        if last_error is not None:
            self.last_error = last_error
        if next_run_time is not None:
            self.next_run_time = next_run_time

        data: dict[str, Any] = {
            "status": status,
            "current_step": current_step,
            "pid": os.getpid(),
            "runs_completed": self.runs_completed,
            "last_run_id": self.last_run_id,
            "last_run_time": self.last_run_time.isoformat() if self.last_run_time else None,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "next_run_time": self.next_run_time.isoformat() if self.next_run_time else None,
            "interval_hours": self.interval_hours,
            "config": self._get_config_dict(),
        }
        write_scheduler_status(self.output_dir, data)

    def is_lock_busy(self) -> tuple[bool, int | None]:
        """Check if pipeline lock is currently acquired by an active process."""
        lock_info = read_lock_info(self.output_dir)
        if not lock_info:
            return False, None

        owner_pid = lock_info.get("child_pid") or lock_info.get("owner_pid")
        if owner_pid is not None:
            pid = int(owner_pid)
            if is_pid_alive(pid):
                return True, pid
        return False, None

    def stop(self) -> None:
        """Signal scheduler to stop daemon loop cleanly."""
        logger.info("[CYBERPULSE INTEL] Stopping autonomous scheduler...")
        self._running = False
        self._stop_event.set()

    # Default step executors
    def _execute_pipeline_step(self, run_id: str) -> int:
        if self._pipeline_step_fn is not None:
            res = self._pipeline_step_fn(run_id)
            return 0 if res is None else res

        from cyberpulse.cli import run_collect, run_run

        try:
            run_collect(self.settings_path, self.sources_path, dry_run=False)
        except Exception as exc:
            logger.warning("[CYBERPULSE INTEL] Pre-run collect warning: %s", exc)

        return run_run(
            settings_path=self.settings_path,
            sources_path=self.sources_path,
            allow_insufficient=True,
            max_rewrites=2,
            no_llm_review=False,
            run_id=run_id,
        )

    def _execute_podcast_step(self, run_id: str) -> int:
        if self._podcast_step_fn is not None:
            res = self._podcast_step_fn(run_id)
            return 0 if res is None else res

        from cyberpulse.cli import run_media_podcast

        return run_media_podcast(
            run_id=run_id,
            settings_path=self.settings_path,
            force=True,
            allow_unvalidated=True,
        )

    def _execute_video_step(self, run_id: str) -> int:
        if self._video_step_fn is not None:
            res = self._video_step_fn(run_id)
            return 0 if res is None else res

        from cyberpulse.cli import run_media_video

        return run_media_video(
            run_id=run_id,
            settings_path=self.settings_path,
            force=True,
            allow_unvalidated=True,
        )

    def _execute_portal_step(self, run_id: str) -> int:
        if self._portal_step_fn is not None:
            res = self._portal_step_fn(run_id)
            return 0 if res is None else res

        from cyberpulse.cli import run_export_portal

        return run_export_portal(
            output_dir=self.output_dir,
            dist_dir=self.dist_dir,
            run_id=run_id,
        )

    def _execute_dispatch_step(self, run_id: str) -> int:
        if self._dispatch_step_fn is not None:
            res = self._dispatch_step_fn(run_id)
            return 0 if res is None else res

        from cyberpulse.cli import run_dispatch

        return run_dispatch(
            run_id=run_id,
            settings_path=self.settings_path,
        )

    def run_cycle(self) -> dict[str, Any]:
        """Execute a complete end-to-end 5-step intelligence orchestration cycle."""
        logger.info("[CYBERPULSE INTEL] Starting scheduler cycle...")

        # Guard against concurrent pipeline execution
        busy, active_pid = self.is_lock_busy()
        if busy:
            msg = f"Pipeline lock held by PID {active_pid}"
            logger.warning("[PIPELINE BUSY] %s. Skipping scheduled cycle.", msg)
            self._update_status(
                status="idle",
                last_status="skipped_busy",
                last_error=msg,
            )
            return {
                "success": False,
                "skipped": True,
                "reason": "busy",
                "error": msg,
            }

        start_time = self.clock_fn()
        self._update_status(status="running", current_step="init")

        steps_executed: list[str] = []
        steps_failed: list[str] = []
        cycle_errors: list[str] = []

        try:
            # Determine run identifier
            if self.run_pipeline:
                run_id = generate_run_id(self.clock_fn)
            else:
                existing_run_id = find_latest_editorial_run_id(self.output_dir)
                if not existing_run_id:
                    err_msg = "No existing editorial package found to process."
                    logger.error("[CYBERPULSE INTEL] %s", err_msg)
                    self._update_status(
                        status="idle",
                        last_status="failed",
                        last_error=err_msg,
                    )
                    return {"success": False, "error": err_msg}
                run_id = existing_run_id

            logger.info("[CYBERPULSE INTEL] Active run identifier: %s", run_id)

            # Step 1: Core pipeline run
            if self.run_pipeline:
                self._update_status(status="running", current_step="pipeline")
                logger.info("[CYBERPULSE INTEL] Step 1/5: Executing core pipeline collection...")
                steps_executed.append("pipeline")
                rc = self._execute_pipeline_step(run_id)
                if rc != 0:
                    err = f"Step 'pipeline' failed with exit code {rc}"
                    logger.error("[CYBERPULSE INTEL] %s", err)
                    steps_failed.append("pipeline")
                    cycle_errors.append(err)
                    self._update_status(
                        status="idle",
                        current_step=None,
                        last_status="failed",
                        last_error=err,
                    )
                    return {
                        "success": False,
                        "run_id": run_id,
                        "steps_executed": steps_executed,
                        "steps_failed": steps_failed,
                        "error": err,
                    }
                logger.info("[CYBERPULSE INTEL] Step 1/5: Core pipeline completed successfully.")

            # Step 2: Podcast media synthesis
            if self.generate_podcast:
                self._update_status(status="running", current_step="podcast")
                logger.info("[CYBERPULSE INTEL] Step 2/5: Synthesizing podcast audio and subtitles...")
                steps_executed.append("podcast")
                rc = self._execute_podcast_step(run_id)
                if rc != 0:
                    err = f"Step 'podcast' failed with code {rc}"
                    logger.warning("[CYBERPULSE INTEL] %s", err)
                    steps_failed.append("podcast")
                    cycle_errors.append(err)
                else:
                    logger.info("[CYBERPULSE INTEL] Step 2/5: Podcast synthesis succeeded.")

            # Step 3: Short video rendering
            if self.generate_video:
                self._update_status(status="running", current_step="video")
                logger.info("[CYBERPULSE INTEL] Step 3/5: Rendering 9:16 short video...")
                steps_executed.append("video")
                rc = self._execute_video_step(run_id)
                if rc != 0:
                    err = f"Step 'video' failed with code {rc}"
                    logger.warning("[CYBERPULSE INTEL] %s", err)
                    steps_failed.append("video")
                    cycle_errors.append(err)
                else:
                    logger.info("[CYBERPULSE INTEL] Step 3/5: Short video rendering succeeded.")

            # Step 4: Static portal build
            if self.build_portal:
                self._update_status(status="running", current_step="portal")
                logger.info("[CYBERPULSE INTEL] Step 4/5: Compiling static reader portal into %s...", self.dist_dir)
                steps_executed.append("portal")
                rc = self._execute_portal_step(run_id)
                if rc != 0:
                    err = f"Step 'portal' failed with code {rc}"
                    logger.warning("[CYBERPULSE INTEL] %s", err)
                    steps_failed.append("portal")
                    cycle_errors.append(err)
                else:
                    logger.info("[CYBERPULSE INTEL] Step 4/5: Static portal build succeeded.")

            # Step 5: Multi-channel dispatch
            if self.dispatch_notifications:
                self._update_status(status="running", current_step="dispatch")
                logger.info("[CYBERPULSE INTEL] Step 5/5: Broadcasting instant notifications...")
                steps_executed.append("dispatch")
                rc = self._execute_dispatch_step(run_id)
                if rc != 0:
                    err = f"Step 'dispatch' failed with code {rc}"
                    logger.warning("[CYBERPULSE INTEL] %s", err)
                    steps_failed.append("dispatch")
                    cycle_errors.append(err)
                else:
                    logger.info("[CYBERPULSE INTEL] Step 5/5: Multi-channel dispatch succeeded.")

            # Finalize cycle metrics
            self.runs_completed += 1
            self.last_run_id = run_id
            self.last_run_time = start_time
            overall_status = "success" if not cycle_errors else "partial"
            overall_error = "; ".join(cycle_errors) if cycle_errors else None

            self._update_status(
                status="idle",
                current_step=None,
                last_status=overall_status,
                last_error=overall_error,
            )

            logger.info(
                "[CYBERPULSE INTEL] Scheduler cycle completed: status=%s, run_id=%s, runs_completed=%d",
                overall_status,
                run_id,
                self.runs_completed,
            )
            return {
                "success": True,
                "run_id": run_id,
                "steps_executed": steps_executed,
                "steps_failed": steps_failed,
                "status": overall_status,
                "error": overall_error,
            }

        except Exception as exc:
            logger.error("[CYBERPULSE INTEL] Unexpected error during scheduler cycle: %s", exc, exc_info=True)
            self._update_status(
                status="idle",
                current_step=None,
                last_status="error",
                last_error=str(exc),
            )
            return {
                "success": False,
                "error": str(exc),
                "steps_executed": steps_executed,
                "steps_failed": steps_failed,
            }

    def run_once(self) -> int:
        """Execute all configured stages once and return CLI exit code."""
        result = self.run_cycle()
        if result.get("skipped"):
            return 10
        if result.get("success"):
            return 0
        return 1

    def run_daemon(self) -> int:
        """Run infinite background daemon loop with responsive shutdown."""
        self._running = True
        self._stop_event.clear()

        def _sig_handler(signum: int, frame: Any) -> None:
            logger.info("[CYBERPULSE INTEL] Signal %d received. Initiating graceful shutdown...", signum)
            self.stop()

        # Attach signal handlers if in main thread
        old_sigint = None
        old_sigterm = None
        try:
            if threading.current_thread() is threading.main_thread():
                old_sigint = signal.signal(signal.SIGINT, _sig_handler)
                if hasattr(signal, "SIGTERM"):
                    old_sigterm = signal.signal(signal.SIGTERM, _sig_handler)
        except Exception as err:
            logger.debug("Signal handler registration skipped: %s", err)

        logger.info(
            "[CYBERPULSE INTEL] Autonomous Scheduler Daemon started. Interval: %.1fh (%.0fs). Press Ctrl+C to terminate.",
            self.interval_hours,
            self.interval_seconds,
        )

        try:
            while self._running and not self._stop_event.is_set():
                cycle_start = self.clock_fn()
                self.run_cycle()

                if not self._running or self._stop_event.is_set():
                    break

                next_run = cycle_start + timedelta(seconds=self.interval_seconds)
                self.next_run_time = next_run
                self._update_status(status="idle", next_run_time=next_run)

                now = self.clock_fn()
                sleep_sec = max(0.0, (next_run - now).total_seconds())
                logger.info(
                    "[CYBERPULSE INTEL] Next execution scheduled for %s (sleeping %.1fs)",
                    next_run.isoformat(),
                    sleep_sec,
                )

                interrupted = self._stop_event.wait(timeout=sleep_sec)
                if interrupted:
                    break

            self._update_status(status="stopped", next_run_time=None)
            logger.info("[CYBERPULSE INTEL] Autonomous Scheduler Daemon stopped cleanly.")
            return 0
        finally:
            try:
                if threading.current_thread() is threading.main_thread():
                    if old_sigint:
                        signal.signal(signal.SIGINT, old_sigint)
                    if old_sigterm and hasattr(signal, "SIGTERM"):
                        signal.signal(signal.SIGTERM, old_sigterm)
            except Exception:
                pass
