"""Tests for CyberPulse CLI commands."""

from __future__ import annotations

from pathlib import Path
import pytest

from cyberpulse import __version__
from cyberpulse.cli import main


def test_cli_help(capsys: pytest.CaptureFixture[str]) -> None:
    """8. --help exits 0 and lists version and check-config."""
    ret = main(["--help"])
    assert ret == 0
    captured = capsys.readouterr()
    stdout = captured.out
    assert "version" in stdout
    assert "check-config" in stdout


def test_cli_version(capsys: pytest.CaptureFixture[str]) -> None:
    """version command exits 0 and prints package version."""
    ret = main(["version"])
    assert ret == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == f"cyberpulse {__version__}"


def test_cli_check_config_valid(
    config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valid config check exits 0 and prints details."""
    settings_path, sources_path = config_paths
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    ret = main(["check-config", "--settings", str(settings_path), "--sources", str(sources_path)])
    assert ret == 0

    captured = capsys.readouterr()
    out = captured.out
    assert "Project name: CyberPulse AI" in out
    assert "Topics: cybersecurity, vulnerability" in out
    assert "Items per run: 5" in out
    assert "Lookback hours: 48" in out
    assert "Output language: vi" in out
    assert "Enabled sources: 1" in out


def test_cli_secret_set_not_leaked(
    config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """6. Secret set to 'sk-test-SECRET123' -> output shows 'set'; stdout and log file do not contain 'SECRET123'."""
    settings_path, sources_path = config_paths
    secret_value = "sk-test-SECRET123"
    monkeypatch.setenv("GEMINI_API_KEY", secret_value)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    ret = main(["check-config", "--settings", str(settings_path), "--sources", str(sources_path)])
    assert ret == 0

    captured = capsys.readouterr()
    stdout = captured.out

    # Must show 'set'
    assert "GEMINI_API_KEY: set" in stdout

    # Must NOT contain secret or substring
    assert "SECRET123" not in stdout
    assert secret_value not in stdout

    # Verify log file also does not contain the secret
    log_file = settings_path.parent / "logs" / "cyberpulse.log"
    assert log_file.exists()
    log_content = log_file.read_text(encoding="utf-8")
    assert "SECRET123" not in log_content
    assert secret_value not in log_content


def test_cli_secret_unset(
    config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """7. Secret unset -> output shows missing; exit code 0."""
    settings_path, sources_path = config_paths
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    ret = main(["check-config", "--settings", str(settings_path), "--sources", str(sources_path)])
    assert ret == 0

    captured = capsys.readouterr()
    assert "GEMINI_API_KEY: missing" in captured.out
    assert "GEMINI_MODEL: missing" in captured.out


def test_cli_malformed_settings_exits_1(
    tmp_path: Path,
    config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """3. Malformed settings -> check-config exits 1 and names the bad field."""
    _, sources_path = config_paths
    bad_settings = tmp_path / "bad_settings.toml"
    bad_settings.write_text(
        """project_name = "CyberPulse AI"
topics = ["cybersecurity"]
items_per_run = 99
lookback_hours = 48
output_language = "vi"
log_level = "INFO"

[paths]
data_dir = "data"
output_dir = "output"
log_dir = "logs"
""",
        encoding="utf-8",
    )

    ret = main(["check-config", "--settings", str(bad_settings), "--sources", str(sources_path)])
    assert ret == 1

    captured = capsys.readouterr()
    err = captured.err
    assert "items_per_run" in err
    assert "ConfigError" in err


def test_cli_init_db(
    config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """17. init-db with tmp settings (data_dir in tmp_path) -> exit 0, db file exists."""
    settings_path, _ = config_paths
    ret = main(["init-db", "--settings", str(settings_path)])
    assert ret == 0

    captured = capsys.readouterr()
    stdout = captured.out
    assert "Database initialized:" in stdout
    assert "(schema version 1)" in stdout

    db_path = settings_path.parent / "data" / "cyberpulse.db"
    assert db_path.exists()

