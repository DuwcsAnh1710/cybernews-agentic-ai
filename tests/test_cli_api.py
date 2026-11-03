"""Unit tests for cyberpulse api CLI subcommand (Task T10)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from cyberpulse.cli import main, run_api


def test_cli_api_help(capsys: pytest.CaptureFixture[str]) -> None:
    """cyberpulse api --help exits 0 and displays CLI options."""
    ret = main(["api", "--help"])
    assert ret == 0

    out = capsys.readouterr().out
    assert "--port" in out
    assert "--host" in out
    assert "--reload" in out
    assert "--settings" in out
    assert "--print-command" in out


def test_cli_api_invalid_ports(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Invalid port numbers (< 1 or > 65535) return exit code 1."""
    settings_file = tmp_path / "settings.toml"
    settings_file.write_text(
        "project_name = 'CyberPulse AI'\ntopics = ['cybersecurity']\n",
        encoding="utf-8",
    )

    ret1 = main(["api", "--port", "0", "--settings", str(settings_file)])
    assert ret1 == 1
    assert "between 1 and 65535" in capsys.readouterr().err

    ret2 = main(["api", "--port", "70000", "--settings", str(settings_file)])
    assert ret2 == 1
    assert "between 1 and 65535" in capsys.readouterr().err


def test_cli_api_bad_settings(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Malformed settings.toml causes ConfigError and returns exit code 1."""
    bad_settings = tmp_path / "bad.toml"
    bad_settings.write_text("invalid_toml = [broken", encoding="utf-8")

    ret = main(["api", "--settings", str(bad_settings)])
    assert ret == 1
    err = capsys.readouterr().err
    assert "ConfigError" in err


def test_cli_api_fastapi_missing_exits_9(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing FastAPI or Uvicorn returns exit code 9 with installation instructions."""
    settings_file = tmp_path / "settings.toml"
    settings_file.write_text(
        "project_name = 'CyberPulse AI'\ntopics = ['cybersecurity']\n",
        encoding="utf-8",
    )

    def mock_find_spec(name: str) -> object:
        if name in {"fastapi", "uvicorn"}:
            return None
        return True

    monkeypatch.setattr(importlib.util, "find_spec", mock_find_spec)

    ret = main(["api", "--settings", str(settings_file)])
    assert ret == 9
    err = capsys.readouterr().err
    assert "FastAPI or Uvicorn is not installed" in err
    assert 'pip install -e ".[api]"' in err


def test_cli_api_print_command(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--print-command outputs full uvicorn invocation argv."""
    settings_file = tmp_path / "settings.toml"
    settings_file.write_text(
        "project_name = 'CyberPulse AI'\ntopics = ['cybersecurity']\n",
        encoding="utf-8",
    )

    ret = main([
        "api",
        "--port",
        "8000",
        "--host",
        "127.0.0.1",
        "--settings",
        str(settings_file),
        "--print-command",
    ])
    assert ret == 0

    lines = capsys.readouterr().out.strip().splitlines()
    assert "-m" in lines
    assert "uvicorn" in lines
    assert "cyberpulse.api.app:create_app" in lines
    assert "--factory" in lines
    assert "--host" in lines
    assert "127.0.0.1" in lines
    assert "--port" in lines
    assert "8000" in lines
    assert "--reload" not in lines


def test_cli_api_print_command_with_reload_and_host(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--print-command with custom host and --reload flag."""
    settings_file = tmp_path / "settings.toml"
    settings_file.write_text(
        "project_name = 'CyberPulse AI'\ntopics = ['cybersecurity']\n",
        encoding="utf-8",
    )

    ret = main([
        "api",
        "--host",
        "0.0.0.0",
        "--port",
        "9000",
        "--reload",
        "--settings",
        str(settings_file),
        "--print-command",
    ])
    assert ret == 0

    lines = capsys.readouterr().out.strip().splitlines()
    assert "0.0.0.0" in lines
    assert "9000" in lines
    assert "--reload" in lines


def test_run_api_executes_uvicorn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_api invokes uvicorn.run with exact configured arguments."""
    settings_file = tmp_path / "settings.toml"
    settings_file.write_text(
        "project_name = 'CyberPulse AI'\ntopics = ['cybersecurity']\n",
        encoding="utf-8",
    )

    import uvicorn
    mock_run = MagicMock()
    monkeypatch.setattr(uvicorn, "run", mock_run)

    ret = run_api(
        port=8080,
        host="0.0.0.0",
        reload=False,
        settings_path=settings_file,
        print_command=False,
    )
    assert ret == 0
    mock_run.assert_called_once_with(
        "cyberpulse.api.app:create_app",
        host="0.0.0.0",
        port=8080,
        reload=False,
        factory=True,
    )
