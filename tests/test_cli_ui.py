"""Unit tests for cyberpulse ui CLI subcommand (Task T4.3)."""

from __future__ import annotations

from pathlib import Path
import pytest

from cyberpulse.cli import main


def test_cli_ui_invalid_port(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings_file = tmp_path / "settings.toml"
    settings_file.write_text("project_name = 'CyberPulse AI'\ntopics = ['cybersecurity']\n", encoding="utf-8")

    # Port below 1024
    ret1 = main(["ui", "--port", "80", "--settings", str(settings_file)])
    assert ret1 == 1
    err1 = capsys.readouterr().err
    assert "between 1024 and 65535" in err1

    # Port above 65535
    ret2 = main(["ui", "--port", "70000", "--settings", str(settings_file)])
    assert ret2 == 1
    err2 = capsys.readouterr().err
    assert "between 1024 and 65535" in err2


def test_cli_ui_bad_settings(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bad_settings = tmp_path / "bad.toml"
    bad_settings.write_text("invalid = toml [broken", encoding="utf-8")

    ret = main(["ui", "--settings", str(bad_settings)])
    assert ret == 1
    err = capsys.readouterr().err
    assert "ConfigError" in err


def test_cli_ui_streamlit_missing_exits_9(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_file = tmp_path / "settings.toml"
    settings_file.write_text("project_name = 'CyberPulse AI'\ntopics = ['cybersecurity']\n", encoding="utf-8")

    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None if name == "streamlit" else True)

    ret = main(["ui", "--settings", str(settings_file)])
    assert ret == 9
    err = capsys.readouterr().err
    assert "Streamlit is not installed" in err
    assert "pip install -e \".[ui]\"" in err


def test_cli_ui_print_command(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings_file = tmp_path / "settings.toml"
    settings_file.write_text("project_name = 'CyberPulse AI'\ntopics = ['cybersecurity']\n", encoding="utf-8")

    ret = main(["ui", "--port", "8888", "--settings", str(settings_file), "--print-command"])
    assert ret == 0

    out = capsys.readouterr().out
    lines = out.strip().splitlines()

    assert "-m" in lines
    assert "streamlit" in lines
    assert "run" in lines
    assert "--server.address" in lines
    assert "127.0.0.1" in lines
    assert "--server.port" in lines
    assert "8888" in lines
    assert "--server.headless" in lines
    assert "true" in lines
    assert "--browser.gatherUsageStats" in lines
    assert "false" in lines
    assert "--" in lines
    assert "--settings" in lines
    assert str(settings_file.resolve()) in lines


def test_cli_ui_launch_invokes_subprocess(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_file = tmp_path / "settings.toml"
    settings_file.write_text("project_name = 'CyberPulse AI'\ntopics = ['cybersecurity']\n", encoding="utf-8")

    called_cmd = []

    class DummyCompletedProcess:
        returncode = 0

    def mock_subprocess_run(cmd, *args, **kwargs):
        called_cmd.extend(cmd)
        return DummyCompletedProcess()

    import subprocess

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

    ret = main(["ui", "--port", "8502", "--settings", str(settings_file)])
    assert ret == 0

    # Ensure cmd was a list of arguments, not a string
    assert isinstance(called_cmd, list)
    assert "127.0.0.1" in called_cmd
    assert "8502" in called_cmd
    assert str(settings_file.resolve()) in called_cmd

    out = capsys.readouterr().out
    assert "Workbench: http://127.0.0.1:8502" in out

