"""Unit tests for Autonomous Docker Scheduler Daemon (Task T12)."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
from unittest.mock import MagicMock
import pytest

from cyberpulse.config import ConfigError, SchedulerConfig, Settings, load_settings
from cyberpulse.scheduler import (
    PipelineScheduler,
    find_latest_editorial_run_id,
    get_scheduler_status_path,
    read_scheduler_status,
    write_scheduler_status,
)


def test_scheduler_config_defaults() -> None:
    """SchedulerConfig initializes with sensible defaults and validates interval_hours."""
    cfg = SchedulerConfig()
    assert cfg.enabled is True
    assert cfg.interval_hours == 12
    assert cfg.run_pipeline is True
    assert cfg.generate_podcast is True
    assert cfg.generate_video is True
    assert cfg.build_portal is True
    assert cfg.dispatch_notifications is True

    # Validation: interval_hours must be > 0
    with pytest.raises(ValueError, match="interval_hours must be greater than 0"):
        SchedulerConfig(interval_hours=0)

    with pytest.raises(ValueError, match="interval_hours must be greater than 0"):
        SchedulerConfig(interval_hours=-5)


def test_scheduler_status_read_write(tmp_path: Path) -> None:
    """write_scheduler_status and read_scheduler_status handle atomic persistence and corruption."""
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Missing file returns None
    assert read_scheduler_status(output_dir) is None

    # Write status
    sample_data = {
        "status": "idle",
        "pid": 1234,
        "runs_completed": 3,
        "last_run_id": "run-20261103T120000Z-abcdef12",
        "last_status": "success",
        "last_error": None,
        "next_run_time": "2026-11-04T00:00:00Z",
        "interval_hours": 12.0,
    }
    write_scheduler_status(output_dir, sample_data)

    loaded = read_scheduler_status(output_dir)
    assert loaded is not None
    assert loaded["status"] == "idle"
    assert loaded["runs_completed"] == 3
    assert loaded["last_run_id"] == "run-20261103T120000Z-abcdef12"

    # Corrupt file returns None safely without raising
    status_path = get_scheduler_status_path(output_dir)
    status_path.write_text("INVALID_JSON{{{", encoding="utf-8")
    assert read_scheduler_status(output_dir) is None


def test_scheduler_initialization_from_settings(tmp_path: Path) -> None:
    """PipelineScheduler initializes properties from settings and custom interval overrides."""
    settings = load_settings("config/settings.toml")
    settings.paths.output_dir = tmp_path / "output"

    # Default from settings
    scheduler = PipelineScheduler(settings=settings)
    assert scheduler.interval_hours == 12.0
    assert scheduler.interval_seconds == 12.0 * 3600.0
    assert scheduler.run_pipeline is True

    # Custom interval_hours override
    scheduler2 = PipelineScheduler(settings=settings, interval_hours=6.0)
    assert scheduler2.interval_hours == 6.0
    assert scheduler2.interval_seconds == 6.0 * 3600.0

    # Custom interval_seconds override (for testing)
    scheduler3 = PipelineScheduler(settings=settings, interval_seconds=30.0)
    assert scheduler3.interval_seconds == 30.0
    assert scheduler3.interval_hours == 30.0 / 3600.0


def test_scheduler_5_steps_orchestrated_mock(tmp_path: Path) -> None:
    """PipelineScheduler executes all 5 steps in order with the generated run_id."""
    settings = load_settings("config/settings.toml")
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    settings.paths.output_dir = output_dir

    called_steps: list[tuple[str, str]] = []

    def mock_pipeline(run_id: str) -> int:
        called_steps.append(("pipeline", run_id))
        return 0

    def mock_podcast(run_id: str) -> int:
        called_steps.append(("podcast", run_id))
        return 0

    def mock_video(run_id: str) -> int:
        called_steps.append(("video", run_id))
        return 0

    def mock_portal(run_id: str) -> int:
        called_steps.append(("portal", run_id))
        return 0

    def mock_dispatch(run_id: str) -> int:
        called_steps.append(("dispatch", run_id))
        return 0

    scheduler = PipelineScheduler(
        settings=settings,
        pipeline_step_fn=mock_pipeline,
        podcast_step_fn=mock_podcast,
        video_step_fn=mock_video,
        portal_step_fn=mock_portal,
        dispatch_step_fn=mock_dispatch,
    )

    result = scheduler.run_cycle()
    assert result["success"] is True
    assert result["status"] == "success"
    assert result["steps_executed"] == ["pipeline", "podcast", "video", "portal", "dispatch"]
    assert result["steps_failed"] == []

    # Verify all 5 steps were called with the exact same run_id
    run_id = result["run_id"]
    assert len(called_steps) == 5
    assert [step[0] for step in called_steps] == ["pipeline", "podcast", "video", "portal", "dispatch"]
    for step_name, r_id in called_steps:
        assert r_id == run_id

    # Verify status file was persisted properly
    status_data = read_scheduler_status(output_dir)
    assert status_data is not None
    assert status_data["status"] == "idle"
    assert status_data["last_status"] == "success"
    assert status_data["runs_completed"] == 1
    assert status_data["last_run_id"] == run_id


def test_scheduler_step1_failure_aborts_subsequent_steps(tmp_path: Path) -> None:
    """When Step 1 (pipeline) fails, subsequent steps are skipped and cycle is marked failed."""
    settings = load_settings("config/settings.toml")
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    settings.paths.output_dir = output_dir

    mock_podcast = MagicMock()

    scheduler = PipelineScheduler(
        settings=settings,
        pipeline_step_fn=lambda r_id: 11,  # Non-zero exit code
        podcast_step_fn=mock_podcast,
    )

    result = scheduler.run_cycle()
    assert result["success"] is False
    assert result["steps_executed"] == ["pipeline"]
    assert result["steps_failed"] == ["pipeline"]
    assert not mock_podcast.called

    status_data = read_scheduler_status(output_dir)
    assert status_data is not None
    assert status_data["last_status"] == "failed"
    assert "Step 'pipeline' failed" in status_data["last_error"]


def test_scheduler_noncritical_step_failure_partial(tmp_path: Path) -> None:
    """When a non-critical step (podcast) fails, remaining steps still proceed and status is partial."""
    settings = load_settings("config/settings.toml")
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    settings.paths.output_dir = output_dir

    mock_video = MagicMock(return_value=0)
    mock_portal = MagicMock(return_value=0)
    mock_dispatch = MagicMock(return_value=0)

    scheduler = PipelineScheduler(
        settings=settings,
        pipeline_step_fn=lambda r_id: 0,
        podcast_step_fn=lambda r_id: 9,  # Missing media dependency code
        video_step_fn=mock_video,
        portal_step_fn=mock_portal,
        dispatch_step_fn=mock_dispatch,
    )

    result = scheduler.run_cycle()
    assert result["success"] is True
    assert result["status"] == "partial"
    assert "podcast" in result["steps_failed"]
    assert mock_video.called
    assert mock_portal.called
    assert mock_dispatch.called

    status_data = read_scheduler_status(output_dir)
    assert status_data is not None
    assert status_data["last_status"] == "partial"
    assert "podcast" in status_data["last_error"]


def test_scheduler_skip_flags(tmp_path: Path) -> None:
    """Scheduler flags (--no-video, --no-podcast, --no-dispatch) properly skip stages."""
    settings = load_settings("config/settings.toml")
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    settings.paths.output_dir = output_dir

    mock_podcast = MagicMock()
    mock_video = MagicMock()
    mock_dispatch = MagicMock()
    mock_portal = MagicMock(return_value=0)

    scheduler = PipelineScheduler(
        settings=settings,
        pipeline_step_fn=lambda r_id: 0,
        podcast_step_fn=mock_podcast,
        video_step_fn=mock_video,
        portal_step_fn=mock_portal,
        dispatch_step_fn=mock_dispatch,
        generate_podcast=False,
        generate_video=False,
        dispatch_notifications=False,
    )

    result = scheduler.run_cycle()
    assert result["success"] is True
    assert result["steps_executed"] == ["pipeline", "portal"]
    assert not mock_podcast.called
    assert not mock_video.called
    assert not mock_dispatch.called
    assert mock_portal.called


def test_scheduler_runlock_prevention(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When pipeline lock is held by another active process, scheduled cycle is skipped."""
    settings = load_settings("config/settings.toml")
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    settings.paths.output_dir = output_dir

    # Mock lock busy
    mock_pipeline = MagicMock()
    scheduler = PipelineScheduler(settings=settings, pipeline_step_fn=mock_pipeline)
    monkeypatch.setattr(scheduler, "is_lock_busy", lambda: (True, 99999))

    result = scheduler.run_cycle()
    assert result["success"] is False
    assert result["skipped"] is True
    assert result["reason"] == "busy"
    assert not mock_pipeline.called

    status_data = read_scheduler_status(output_dir)
    assert status_data is not None
    assert status_data["last_status"] == "skipped_busy"
    assert "99999" in status_data["last_error"]


def test_scheduler_run_once_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """run_once returns 0 on success, 10 on busy lock, and 1 on failure."""
    settings = load_settings("config/settings.toml")
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    settings.paths.output_dir = output_dir

    # Success case -> 0
    scheduler_ok = PipelineScheduler(settings=settings, pipeline_step_fn=lambda r_id: 0)
    assert scheduler_ok.run_once() == 0

    # Busy case -> 10
    scheduler_busy = PipelineScheduler(settings=settings)
    monkeypatch.setattr(scheduler_busy, "is_lock_busy", lambda: (True, 99999))
    assert scheduler_busy.run_once() == 10

    # Failure case -> 1
    scheduler_fail = PipelineScheduler(settings=settings, pipeline_step_fn=lambda r_id: 11)
    assert scheduler_fail.run_once() == 1


def test_scheduler_graceful_shutdown(tmp_path: Path) -> None:
    """Calling stop() signals daemon loop to wake up immediately and stop cleanly."""
    settings = load_settings("config/settings.toml")
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    settings.paths.output_dir = output_dir

    scheduler = PipelineScheduler(
        settings=settings,
        interval_seconds=3600.0,  # Long 1-hour interval
        pipeline_step_fn=lambda r_id: 0,
        podcast_step_fn=lambda r_id: 0,
        video_step_fn=lambda r_id: 0,
        portal_step_fn=lambda r_id: 0,
        dispatch_step_fn=lambda r_id: 0,
    )

    # Run daemon in separate thread
    daemon_thread = threading.Thread(target=scheduler.run_daemon, daemon=True)
    daemon_thread.start()

    # Wait for first cycle to complete and scheduler to sleep
    time.sleep(0.3)
    assert daemon_thread.is_alive()

    # Stop scheduler
    t0 = time.time()
    scheduler.stop()
    daemon_thread.join(timeout=2.0)

    elapsed = time.time() - t0
    assert not daemon_thread.is_alive(), "Daemon thread did not terminate"
    assert elapsed < 1.5, f"Daemon took too long to terminate ({elapsed:.2f}s)"

    status_data = read_scheduler_status(output_dir)
    assert status_data is not None
    assert status_data["status"] == "stopped"


def test_scheduler_find_latest_run_id_when_pipeline_disabled(tmp_path: Path) -> None:
    """When run_pipeline=False, scheduler discovers latest existing editorial package."""
    output_dir = tmp_path / "output"
    editorial_dir = output_dir / "editorial"
    editorial_dir.mkdir(parents=True, exist_ok=True)

    # Write two sample editorial packages with different timestamps
    file1 = editorial_dir / "run-20261101T000000Z-11111111.json"
    file1.write_text("{}", encoding="utf-8")
    time.sleep(0.01)

    file2 = editorial_dir / "run-20261102T000000Z-22222222.json"
    file2.write_text("{}", encoding="utf-8")

    latest = find_latest_editorial_run_id(output_dir)
    assert latest == "run-20261102T000000Z-22222222"
