"""Unit tests for cyberpulse scheduler CLI subcommand (Task T12)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock
import pytest

from cyberpulse.cli import main, run_scheduler


def test_cli_scheduler_help(capsys: pytest.CaptureFixture[str]) -> None:
    """cyberpulse scheduler --help exits 0 and displays all CLI options."""
    ret = main(["scheduler", "--help"])
    assert ret == 0

    out = capsys.readouterr().out
    assert "--once" in out
    assert "--daemon" in out
    assert "--interval-hours" in out
    assert "--no-video" in out
    assert "--no-podcast" in out
    assert "--no-dispatch" in out
    assert "--settings" in out
    assert "--sources" in out
    assert "--dist-dir" in out


def test_cli_scheduler_once_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """cyberpulse scheduler --once invokes scheduler.run_once()."""
    mock_sched_instance = MagicMock()
    mock_sched_instance.run_once.return_value = 0
    mock_sched_cls = MagicMock(return_value=mock_sched_instance)

    import cyberpulse.cli as cli
    monkeypatch.setattr("cyberpulse.scheduler.PipelineScheduler", mock_sched_cls)

    ret = main(["scheduler", "--once"])
    assert ret == 0
    assert mock_sched_instance.run_once.called
    assert not mock_sched_instance.run_daemon.called


def test_cli_scheduler_daemon_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """cyberpulse scheduler --daemon invokes scheduler.run_daemon()."""
    mock_sched_instance = MagicMock()
    mock_sched_instance.run_daemon.return_value = 0
    mock_sched_cls = MagicMock(return_value=mock_sched_instance)

    import cyberpulse.cli as cli
    monkeypatch.setattr("cyberpulse.scheduler.PipelineScheduler", mock_sched_cls)

    ret = main(["scheduler", "--daemon"])
    assert ret == 0
    assert mock_sched_instance.run_daemon.called
    assert not mock_sched_instance.run_once.called


def test_cli_scheduler_bad_settings(capsys: pytest.CaptureFixture[str]) -> None:
    """Missing or invalid settings file returns exit code 1."""
    ret = main(["scheduler", "--settings", "nonexistent_settings.toml"])
    assert ret == 1
    err = capsys.readouterr().err
    assert "ConfigError" in err


def test_cli_scheduler_flags_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI flags (--interval-hours, --no-video, etc.) are forwarded to PipelineScheduler."""
    captured_kwargs: dict[str, any] = {}

    def mock_init(self, *args, **kwargs):
        captured_kwargs.update(kwargs)
        self.run_once = MagicMock(return_value=0)

    import cyberpulse.scheduler as scheduler_mod
    monkeypatch.setattr(scheduler_mod.PipelineScheduler, "__init__", mock_init)

    ret = main([
        "scheduler",
        "--once",
        "--interval-hours", "6.5",
        "--no-video",
        "--no-podcast",
        "--no-dispatch",
    ])
    assert ret == 0
    assert captured_kwargs.get("interval_hours") == 6.5
    assert captured_kwargs.get("generate_podcast") is False
    assert captured_kwargs.get("generate_video") is False
    assert captured_kwargs.get("dispatch_notifications") is False
