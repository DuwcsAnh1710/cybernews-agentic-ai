"""Unit tests for 'run' command support in ui/runner.py (T5.2)."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Any

import pytest

from cyberpulse.ui.runner import (
    EXIT_LABELS_VI,
    JobStatus,
    build_argv,
    read_lock_info,
    release_lock,
    start_job,
)


def test_runner_build_argv_run_command(tmp_path: Path) -> None:
    """build_argv correctly builds command line argument list for 'run' step."""
    out_dir = tmp_path / "output"
    settings_file = tmp_path / "settings.toml"

    # Default parameters
    argv = build_argv("run", output_dir=out_dir, settings_path=settings_file)
    assert argv[-3:] == ["run", "--settings", str(settings_file)]
    assert "--allow-insufficient" not in argv
    assert "--no-llm-review" not in argv

    # Explicit parameters
    params = {
        "allow_insufficient": True,
        "max_rewrites": 3,
        "no_llm_review": True,
        "run_id": "custom-run-123",
    }
    argv_full = build_argv(
        "run",
        output_dir=out_dir,
        settings_path=settings_file,
        params=params,
    )
    assert "--allow-insufficient" in argv_full
    assert "--max-rewrites" in argv_full
    idx_rw = argv_full.index("--max-rewrites")
    assert argv_full[idx_rw + 1] == "3"
    assert "--no-llm-review" in argv_full
    assert "--run-id" in argv_full
    idx_rid = argv_full.index("--run-id")
    assert argv_full[idx_rid + 1] == "custom-run-123"


def test_runner_starts_run_and_passes_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """start_job('run', ...) acquires lock with token and passes CYBERPULSE_LOCK_TOKEN to child."""
    out_dir = tmp_path / "output"
    settings_file = tmp_path / "settings.toml"

    captured_env = {}

    import subprocess

    class DummyProc:
        def __init__(self) -> None:
            self.pid = 98765

        def wait(self, timeout: float | None = None) -> int:
            return 0

    def mock_popen(argv: list[str], **kwargs: Any) -> DummyProc:
        captured_env.update(kwargs.get("env", {}))
        return DummyProc()

    monkeypatch.setattr(subprocess, "Popen", mock_popen)

    record = start_job(
        step="run",
        output_dir=out_dir,
        settings_path=settings_file,
        is_pid_alive_fn=lambda pid: True,
    )

    assert record.step == "run"
    assert "CYBERPULSE_LOCK_TOKEN" in captured_env
    token = captured_env["CYBERPULSE_LOCK_TOKEN"]
    assert len(token) >= 16

    # Verify lock had the matching token
    lock_data = read_lock_info(out_dir)
    assert lock_data is not None
    assert lock_data.get("token") == token

    release_lock(out_dir, force=True)


def test_runner_exit_code_labels_10_and_11() -> None:
    """EXIT_LABELS_VI contains entries for 10 and 11."""
    assert 10 in EXIT_LABELS_VI
    assert "Pipeline đang bận" in EXIT_LABELS_VI[10]
    assert 11 in EXIT_LABELS_VI
    assert "Thất bại trong pipeline" in EXIT_LABELS_VI[11]
