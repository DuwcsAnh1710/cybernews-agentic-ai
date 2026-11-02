"""Hermetic CLI integration tests for cyberpulse export portal subcommand."""

from __future__ import annotations

from pathlib import Path
import pytest

from cyberpulse.cli import main
from tests.test_ui_data import make_sample_editorial, make_sample_research


def _setup_cli_run(output_dir: Path, run_id: str, with_audio: bool = False) -> None:
    """Helper to set up mock editorial and research files."""
    r_dir = output_dir / "research"
    e_dir = output_dir / "editorial"
    r_dir.mkdir(parents=True, exist_ok=True)
    e_dir.mkdir(parents=True, exist_ok=True)

    res = make_sample_research(run_id)
    pkg = make_sample_editorial(run_id)

    (r_dir / f"{run_id}.json").write_text(res.model_dump_json(), encoding="utf-8")
    (e_dir / f"{run_id}.json").write_text(pkg.model_dump_json(), encoding="utf-8")

    if with_audio:
        media_dir = output_dir / "media" / "podcast"
        media_dir.mkdir(parents=True, exist_ok=True)
        (media_dir / f"{run_id}.mp3").write_bytes(b"MOCK_MP3_CONTENT")


def test_cli_export_portal_help(capsys: pytest.CaptureFixture[str]) -> None:
    """AC 1: Test cyberpulse export portal --help displays usage and options."""
    ret = main(["export", "portal", "--help"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "--output-dir" in captured.out
    assert "--dist-dir" in captured.out
    assert "--run-id" in captured.out
    assert "--serve" in captured.out
    assert "--port" in captured.out


def test_cli_export_portal_success_default_args(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC 2: Test CLI export portal without --serve generates dist/index.html and exits 0."""
    output_dir = tmp_path / "output"
    dist_dir = tmp_path / "dist"
    run_id = "run-20261102T020834Z-cli-001"

    _setup_cli_run(output_dir, run_id, with_audio=True)

    code = main([
        "export",
        "portal",
        "--output-dir",
        str(output_dir),
        "--dist-dir",
        str(dist_dir),
    ])

    assert code == 0
    captured = capsys.readouterr()
    assert "Successfully generated CyberPulse Reader portal at:" in captured.out
    assert f"Featured: {run_id}" in captured.out
    assert (dist_dir / "index.html").is_file()
    assert (dist_dir / "archive.html").is_file()
    assert not (dist_dir / "feed.json").exists()
    assert (dist_dir / "articles").is_dir()
    assert (dist_dir / "assets" / "style.css").is_file()
    assert (dist_dir / "assets" / "app.js").is_file()
    assert (dist_dir / "media" / f"{run_id}.mp3").is_file()


def test_cli_export_portal_custom_dist_and_run_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC 3: Test CLI export portal with custom --dist-dir and specific --run-id."""
    output_dir = tmp_path / "custom_output"
    custom_dist = tmp_path / "my_custom_site"

    run_1 = "run-20261031T033708Z-older-edition"
    run_2 = "run-20261102T020834Z-newer-edition"

    _setup_cli_run(output_dir, run_1)
    _setup_cli_run(output_dir, run_2)

    code = main([
        "export",
        "portal",
        "--output-dir",
        str(output_dir),
        "--dist-dir",
        str(custom_dist),
        "--run-id",
        run_1,
    ])

    assert code == 0
    captured = capsys.readouterr()
    assert f"Featured: {run_1}" in captured.out
    assert (custom_dist / "index.html").is_file()
    index_html = (custom_dist / "index.html").read_text(encoding="utf-8")
    assert run_1 in index_html


def test_cli_export_portal_missing_output_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC 4: Test CLI error handling for missing output directory."""
    non_existent_output = tmp_path / "non_existent_output_dir"

    code = main([
        "export",
        "portal",
        "--output-dir",
        str(non_existent_output),
    ])

    assert code == 2
    captured = capsys.readouterr()
    assert "Error: Output directory not found" in captured.err


def test_cli_export_portal_serve_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC 5: Test CLI export portal with --serve triggers HTTP server and handles shutdown."""
    output_dir = tmp_path / "output"
    dist_dir = tmp_path / "dist"
    run_id = "run-20261102T020834Z-serve-test"

    _setup_cli_run(output_dir, run_id)

    # Mock ThreadingHTTPServer so test remains hermetic and non-blocking
    class MockHTTPServer:
        def __init__(self, addr: tuple[str, int], handler: object) -> None:
            self.addr = addr
            self.handler = handler

        def serve_forever(self) -> None:
            raise KeyboardInterrupt()

        def server_close(self) -> None:
            pass

        def __enter__(self) -> MockHTTPServer:
            return self

        def __exit__(self, *args: object) -> None:
            pass

    import http.server
    monkeypatch.setattr(http.server, "ThreadingHTTPServer", MockHTTPServer)

    code = main([
        "export",
        "portal",
        "--output-dir",
        str(output_dir),
        "--dist-dir",
        str(dist_dir),
        "--serve",
        "--port",
        "8999",
    ])

    assert code == 0
    captured = capsys.readouterr()
    assert "Trang web độc giả đang chạy tại: http://localhost:8999" in captured.out
    assert "Đã dừng máy chủ web." in captured.out
