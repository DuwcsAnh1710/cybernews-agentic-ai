"""Hermetic unit tests for CLI export bundle subcommand (Task T8.1)."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from cyberpulse.cli import main
from tests.test_ui_data import make_sample_editorial, make_sample_research


def test_cli_export_bundle_help() -> None:
    """Test cyberpulse export --help and cyberpulse export bundle --help."""
    ret_export = main(["export", "--help"])
    assert ret_export == 0

    ret_bundle = main(["export", "bundle", "--help"])
    assert ret_bundle == 0


def test_cli_export_bundle_non_existent_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test CLI export bundle with non-existent run ID returns exit code 2."""
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True)

    code = main([
        "export",
        "bundle",
        "non-existent-run-12345",
        "--output-dir",
        str(output_dir),
    ])

    assert code == 2
    captured = capsys.readouterr()
    assert "Editorial package not found" in captured.err


def test_cli_export_bundle_success(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test CLI export bundle with valid editorial package returns 0 and exports bundle."""
    run_id = "test-cli-run-001"
    output_dir = tmp_path / "output"
    export_dir = tmp_path / "custom_export"
    e_dir = output_dir / "editorial"
    r_dir = output_dir / "research"
    e_dir.mkdir(parents=True)
    r_dir.mkdir(parents=True)

    pkg = make_sample_editorial(run_id)
    res = make_sample_research(run_id)

    (e_dir / f"{run_id}.json").write_text(pkg.model_dump_json(), encoding="utf-8")
    (r_dir / f"{run_id}.json").write_text(res.model_dump_json(), encoding="utf-8")

    code = main([
        "export",
        "bundle",
        run_id,
        "--output-dir",
        str(output_dir),
        "--export-dir",
        str(export_dir),
    ])

    assert code == 0
    captured = capsys.readouterr()
    assert f"Successfully exported bundle for run '{run_id}'" in captured.out
    assert "digest.md" in captured.out
    assert "digest.html" in captured.out

    bundle_path = export_dir / run_id
    assert bundle_path.exists()
    assert (bundle_path / "digest.md").exists()
    assert (bundle_path / "digest.html").exists()
    assert (bundle_path / "social_facebook.txt").exists()
    assert (bundle_path / "social_youtube.txt").exists()
    assert (bundle_path / "social_tiktok.txt").exists()
    assert (bundle_path / "huong_dan_dang_bai.md").exists()
    assert (bundle_path / "manifest.json").exists()


def test_cli_export_bundle_corrupt_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test CLI export bundle handles malformed editorial JSON with exit code 2."""
    run_id = "test-corrupt-run"
    output_dir = tmp_path / "output"
    e_dir = output_dir / "editorial"
    e_dir.mkdir(parents=True)

    (e_dir / f"{run_id}.json").write_text("{not-valid-json", encoding="utf-8")

    code = main([
        "export",
        "bundle",
        run_id,
        "--output-dir",
        str(output_dir),
    ])

    assert code == 2
    captured = capsys.readouterr()
    assert "Error loading editorial package" in captured.err
