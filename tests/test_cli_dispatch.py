"""Unit tests for 'cyberpulse dispatch' CLI command (T8.2)."""

from __future__ import annotations

from pathlib import Path
import pytest

from cyberpulse.cli import main
from tests.test_ui_data import make_sample_editorial


def test_cli_dispatch_help_exits_0(capsys: pytest.CaptureFixture[str]) -> None:
    """cyberpulse dispatch --help prints help and exits 0."""
    code = main(["dispatch", "--help"])
    assert code == 0
    captured = capsys.readouterr()
    assert "--dry-run" in captured.out
    assert "--telegram-only" in captured.out
    assert "--discord-only" in captured.out


def test_cli_dispatch_dry_run_exits_0(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """cyberpulse dispatch --dry-run renders preview and exits 0."""
    out_dir = tmp_path / "output"
    editorial_dir = out_dir / "editorial"
    editorial_dir.mkdir(parents=True, exist_ok=True)

    rid = "run-20261103T110000Z-disptest"
    pkg = make_sample_editorial(rid)
    pkg_file = editorial_dir / f"{rid}.json"
    pkg_file.write_text(pkg.model_dump_json(), encoding="utf-8")

    settings_file = tmp_path / "settings.toml"
    settings_file.write_text(
        f"""
        project_name = "CyberPulse"
        topics = ["cyber"]
        [paths]
        output_dir = "{out_dir.as_posix()}"
        [dispatch]
        enabled = true
        portal_base_url = "https://example.com"
        """,
        encoding="utf-8",
    )

    code = main([
        "dispatch",
        rid,
        "--dry-run",
        "--settings",
        str(settings_file),
    ])

    assert code == 0
    captured = capsys.readouterr()
    assert "TELEGRAM (DRY-RUN)" in captured.out
    assert "DISCORD (DRY-RUN)" in captured.out
    assert pkg.digest.title in captured.out


def test_cli_dispatch_missing_run_id_exits_2(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Non-existent run_id exits 2."""
    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    settings_file = tmp_path / "settings.toml"
    settings_file.write_text(
        f"""
        project_name = "CyberPulse"
        topics = ["cyber"]
        [paths]
        output_dir = "{out_dir.as_posix()}"
        """,
        encoding="utf-8",
    )

    code = main([
        "dispatch",
        "non-existent-run-999",
        "--settings",
        str(settings_file),
    ])

    assert code == 2
    captured = capsys.readouterr()
    assert "Error:" in captured.err


def test_cli_dispatch_skips_when_no_credentials(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When secrets are unset, dispatch reports channel skipped and exits 0."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)

    out_dir = tmp_path / "output"
    editorial_dir = out_dir / "editorial"
    editorial_dir.mkdir(parents=True, exist_ok=True)

    rid = "run-20261103T110000Z-nocreds"
    pkg = make_sample_editorial(rid)
    pkg_file = editorial_dir / f"{rid}.json"
    pkg_file.write_text(pkg.model_dump_json(), encoding="utf-8")

    settings_file = tmp_path / "settings.toml"
    settings_file.write_text(
        f"""
        project_name = "CyberPulse"
        topics = ["cyber"]
        [paths]
        output_dir = "{out_dir.as_posix()}"
        """,
        encoding="utf-8",
    )

    code = main([
        "dispatch",
        rid,
        "--settings",
        str(settings_file),
    ])

    assert code == 0
    captured = capsys.readouterr()
    assert "TELEGRAM: Bo qua" in captured.out
    assert "DISCORD: Bo qua" in captured.out
