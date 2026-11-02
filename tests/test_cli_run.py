"""Hermetic unit tests for 'cyberpulse run' CLI command (T5.2)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
from typing import Any
from unittest.mock import MagicMock

import pytest

from cyberpulse.cli import main
from cyberpulse.config import load_settings
from cyberpulse.llm import FakeLLMClient, LLMRateLimitError
from cyberpulse.models import RunStatus
from cyberpulse.orchestrator import FailureReason, RunResult
from cyberpulse.runlock import acquire_lock, get_lock_path, release_lock
from cyberpulse.storage import connect, init_db, upsert_article
from tests.test_orchestrator import (
    make_article,
    make_editorial_package,
    make_research_result,
    make_validation_report,
)


def _setup_db_with_articles(db_path: Path, count: int = 5) -> None:
    conn = connect(db_path)
    init_db(conn)
    for i in range(count):
        art = make_article(f"art-{i:03d}", f"Article Title {i}", source_name=f"Source {i}")
        upsert_article(conn, art)
    conn.close()


def test_cli_run_help(capsys: pytest.CaptureFixture[str]) -> None:
    """--help on run shows all required options."""
    ret = main(["run", "--help"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "--allow-insufficient" in out
    assert "--max-rewrites" in out
    assert "--no-llm-review" in out
    assert "--run-id" in out


def test_cli_run_config_error_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Non-existent settings file exits 1."""
    ret = main(["run", "--settings", str(tmp_path / "nonexistent.toml")])
    assert ret == 1
    err = capsys.readouterr().err
    assert "ConfigError" in err


def test_cli_run_invalid_max_rewrites_exits_7(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """--max-rewrites out of bounds (0..3) exits 7."""
    settings_file = Path("config/settings.toml")
    ret_high = main(["run", "--settings", str(settings_file), "--max-rewrites", "4"])
    assert ret_high == 7
    assert "--max-rewrites must be between 0 and 3" in capsys.readouterr().err

    ret_low = main(["run", "--settings", str(settings_file), "--max-rewrites", "-1"])
    assert ret_low == 7
    assert "--max-rewrites must be between 0 and 3" in capsys.readouterr().err


def test_cli_run_invalid_run_id_exits_7(capsys: pytest.CaptureFixture[str]) -> None:
    """Empty --run-id exits 7."""
    settings_file = Path("config/settings.toml")
    ret = main(["run", "--settings", str(settings_file), "--run-id", "   "])
    assert ret == 7
    assert "--run-id cannot be empty" in capsys.readouterr().err


def test_cli_run_missing_api_key_exits_4(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When GEMINI_API_KEY is unset and provider is gemini, exits 4."""
    settings_file = Path("config/settings.toml")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    ret = main(["run", "--settings", str(settings_file)])
    assert ret == 4
    out = capsys.readouterr().out
    assert "GEMINI_API_KEY: missing" in out


def test_cli_run_busy_lock_exits_10_zero_llm_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When pipeline.lock is held, run exits 10 with zero calls to LLM."""
    settings = load_settings(Path("config/settings.toml"))
    out_dir = tmp_path / "output"
    settings.paths.output_dir = out_dir
    settings.paths.data_dir = tmp_path / "data"

    settings_file = tmp_path / "settings.toml"
    # Write custom settings with updated output_dir
    settings_text = Path("config/settings.toml").read_text(encoding="utf-8")
    settings_file.write_text(settings_text, encoding="utf-8")

    monkeypatch.setenv("GEMINI_API_KEY", "mock-key")
    monkeypatch.setattr("cyberpulse.cli.load_settings", lambda p: settings)

    # Pre-acquire lock with an active process PID
    acquire_lock(out_dir, step="collect", job_id="blocking-job", is_pid_alive_fn=lambda pid: True)

    llm_called = []

    def mock_create_llm(cfg: Any) -> Any:
        llm_called.append("called")
        return FakeLLMClient(responses=[])

    monkeypatch.setattr("cyberpulse.cli.create_llm_client", mock_create_llm)

    ret = main(["run", "--settings", str(settings_file)])
    assert ret == 10
    assert len(llm_called) == 0  # Zero LLM calls
    err = capsys.readouterr().err
    assert "[PIPELINE BUSY]" in err


def test_cli_run_happy_path_exits_0(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Happy path run reaching awaiting_review exits 0, writes artifacts, and releases lock."""
    settings = load_settings(Path("config/settings.toml"))
    out_dir = tmp_path / "output"
    data_dir = tmp_path / "data"
    settings.paths.output_dir = out_dir
    settings.paths.data_dir = data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    db_path = data_dir / "cyberpulse.db"
    _setup_db_with_articles(db_path, count=5)

    monkeypatch.setenv("GEMINI_API_KEY", "mock-key")
    monkeypatch.setattr("cyberpulse.cli.load_settings", lambda p: settings)

    rid = "run-20261101T150000Z-happy001"
    research_res = make_research_result(rid)
    pkg = make_editorial_package(rid, research_res)
    val_rep = make_validation_report(rid, passed=True)

    class MockOrchestrator:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def run(self, run_id: str | None = None) -> RunResult:
            # Emulate successful run artifacts
            research_path = out_dir / "research" / f"{rid}.json"
            research_path.parent.mkdir(parents=True, exist_ok=True)
            research_path.write_text(research_res.model_dump_json(), encoding="utf-8")

            val_path = out_dir / "validation" / f"{rid}.json"
            val_path.parent.mkdir(parents=True, exist_ok=True)
            val_path.write_text(val_rep.model_dump_json(), encoding="utf-8")

            return RunResult(
                run_id=rid,
                status=RunStatus.awaiting_review,
                failure_reason=None,
                degraded=False,
                artifacts={"research": research_path, "validation": val_path},
                step_attempts={"research": 1, "editorial": 1, "validate": 1},
                error_message=None,
                rewrite_attempts=0,
                stop_reason=None,
            )

    monkeypatch.setattr("cyberpulse.cli.Orchestrator", MockOrchestrator)

    ret = main(["run", "--settings", str(Path("config/settings.toml")), "--run-id", rid])
    assert ret == 0

    out = capsys.readouterr().out
    assert "Status: awaiting_review" in out
    assert f"Run ID: {rid}" in out

    # Verify lock is released in finally
    lock_file = get_lock_path(out_dir)
    assert not lock_file.exists()


@pytest.mark.parametrize(
    "fail_reason,expected_code",
    [
        (FailureReason.insufficient_research, 3),
        (FailureReason.llm_error, 5),
        (FailureReason.validation_failed, 8),
        (FailureReason.research_failed, 11),
        (FailureReason.editorial_failed, 11),
        (FailureReason.internal_error, 11),
        (FailureReason.interrupted, 11),
    ],
)
def test_cli_run_failure_reasons_mapped_to_exit_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_reason: FailureReason,
    expected_code: int,
) -> None:
    """Each FailureReason maps to its specified exit code (R3)."""
    settings = load_settings(Path("config/settings.toml"))
    out_dir = tmp_path / "output"
    data_dir = tmp_path / "data"
    settings.paths.output_dir = out_dir
    settings.paths.data_dir = data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    db_path = data_dir / "cyberpulse.db"
    _setup_db_with_articles(db_path, count=5)

    monkeypatch.setenv("GEMINI_API_KEY", "mock-key")
    monkeypatch.setattr("cyberpulse.cli.load_settings", lambda p: settings)

    rid = f"run-20261101T150000Z-{fail_reason.value[:8]}"

    class MockFailingOrchestrator:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def run(self, run_id: str | None = None) -> RunResult:
            return RunResult(
                run_id=rid,
                status=RunStatus.failed,
                failure_reason=fail_reason,
                degraded=False,
                artifacts={},
                step_attempts={},
                error_message=f"Simulated failure: {fail_reason.value}",
                rewrite_attempts=0,
                stop_reason=None,
            )

    monkeypatch.setattr("cyberpulse.cli.Orchestrator", MockFailingOrchestrator)

    ret = main(["run", "--settings", str(Path("config/settings.toml")), "--run-id", rid])
    assert ret == expected_code

    # Lock always released
    lock_file = get_lock_path(out_dir)
    assert not lock_file.exists()


def test_cli_run_lock_adoption_from_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When Runner passes CYBERPULSE_LOCK_TOKEN, CLI adopts lock instead of raising busy."""
    settings = load_settings(Path("config/settings.toml"))
    out_dir = tmp_path / "output"
    data_dir = tmp_path / "data"
    settings.paths.output_dir = out_dir
    settings.paths.data_dir = data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    db_path = data_dir / "cyberpulse.db"
    _setup_db_with_articles(db_path, count=5)

    token = "runner-handshake-token-999"
    # Pre-acquire with token by 'runner' (current PID for test)
    acquire_lock(out_dir, step="run", job_id="job-runner", token=token, is_pid_alive_fn=lambda pid: True)

    monkeypatch.setenv("GEMINI_API_KEY", "mock-key")
    monkeypatch.setenv("CYBERPULSE_LOCK_TOKEN", token)
    monkeypatch.setattr("cyberpulse.cli.load_settings", lambda p: settings)

    rid = "run-20261101T150000Z-adopt001"

    class MockAdoptOrchestrator:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def run(self, run_id: str | None = None) -> RunResult:
            return RunResult(
                run_id=rid,
                status=RunStatus.awaiting_review,
                failure_reason=None,
                degraded=False,
                artifacts={},
                step_attempts={},
                error_message=None,
                rewrite_attempts=0,
                stop_reason=None,
            )

    monkeypatch.setattr("cyberpulse.cli.Orchestrator", MockAdoptOrchestrator)

    ret = main(["run", "--settings", str(Path("config/settings.toml")), "--run-id", rid])
    assert ret == 0

    # Clean up lock manually since child adopted and relinquished for runner
    release_lock(out_dir, force=True)
