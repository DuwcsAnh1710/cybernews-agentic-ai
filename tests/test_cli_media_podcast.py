"""CLI tests for cyberpulse media podcast subcommand."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import pytest

from cyberpulse.cli import main
from cyberpulse.media.podcast import (
    MediaDependencyError,
    MediaInputError,
    MediaSynthesisError,
    PodcastMediaResult,
)
from cyberpulse.models import (
    Caption,
    CitedText,
    Digest,
    DigestSection,
    EditorialPackage,
    PodcastScript,
    PodcastSegment,
    VideoScene,
    VideoScript,
)


@pytest.fixture(autouse=True)
def _block_external_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block external network connections while allowing local loopback."""
    import socket

    orig_connect = socket.socket.connect

    def _no_connect(sock: socket.socket, address: Any, *args: Any, **kwargs: Any) -> None:
        host = address[0] if isinstance(address, tuple) and len(address) > 0 else str(address)
        if host in ("127.0.0.1", "localhost", "::1"):
            return orig_connect(sock, address, *args, **kwargs)
        raise RuntimeError(f"Hermetic test violation: external connection to {address}")

    monkeypatch.setattr("socket.socket.connect", _no_connect)


@pytest.fixture
def cli_test_env(tmp_path: Path) -> dict[str, Any]:
    """Setup isolated filesystem with config, editorial, and validation files."""
    settings_path = tmp_path / "settings.toml"
    output_dir = tmp_path / "output"
    editorial_dir = output_dir / "editorial"
    validation_dir = output_dir / "validation"
    media_dir = output_dir / "media" / "podcast"

    editorial_dir.mkdir(parents=True)
    validation_dir.mkdir(parents=True)
    media_dir.mkdir(parents=True)

    settings_content = f"""project_name = "CyberPulse AI"
topics = ["cybersecurity"]
output_language = "vi"

[paths]
output_dir = "{output_dir.as_posix()}"

[editorial]
podcast_speakers = ["Minh", "Lan"]
"""
    settings_path.write_text(settings_content, encoding="utf-8")

    run_id = "test-run-123"
    pkg = EditorialPackage(
        run_id=run_id,
        language="vi",
        digest=Digest(
            title="Điểm tin",
            intro="Tóm tắt an ninh mạng.",
            sections=[
                DigestSection(
                    heading="Tin 1",
                    body=CitedText(text="Nội dung 1.", cited_article_ids=["a1"]),
                )
            ],
        ),
        podcast=PodcastScript(
            title="Podcast số 1",
            segments=[
                PodcastSegment(
                    speaker="Minh",
                    line=CitedText(text="Minh chào quý vị.", cited_article_ids=["a1"]),
                ),
                PodcastSegment(
                    speaker="Lan",
                    line=CitedText(text="Lan chào quý vị.", cited_article_ids=["a1"]),
                ),
            ],
        ),
        video=VideoScript(
            title="Video 1",
            scenes=[
                VideoScene(
                    scene_no=1,
                    on_screen_text="Text 1",
                    narration=CitedText(text="Narration 1", cited_article_ids=["a1"]),
                    duration_sec=10.0,
                )
            ],
        ),
        captions=[
            Caption(
                platform="facebook",
                text="Caption text",
                hashtags=["#cyber"],
                cited_article_ids=["a1"],
            )
        ],
    )
    (editorial_dir / f"{run_id}.json").write_text(
        pkg.model_dump_json(indent=2), encoding="utf-8"
    )

    # Valid report
    val_report = {"passed": True, "errors": [], "warnings": []}
    (validation_dir / f"{run_id}.json").write_text(
        json.dumps(val_report, indent=2), encoding="utf-8"
    )

    return {
        "settings_path": settings_path,
        "output_dir": output_dir,
        "run_id": run_id,
        "editorial_dir": editorial_dir,
        "validation_dir": validation_dir,
        "media_dir": media_dir,
        "pkg": pkg,
    }


def test_cli_media_podcast_help(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["media", "podcast", "--help"])
    assert code == 0
    captured = capsys.readouterr()
    assert "usage: cyberpulse media podcast" in captured.out
    assert "--settings" in captured.out
    assert "--voice" in captured.out
    assert "--pause-ms" in captured.out
    assert "--force" in captured.out
    assert "--allow-unvalidated" in captured.out


def test_cli_media_podcast_success(
    cli_test_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = cli_test_env["run_id"]
    settings_path = cli_test_env["settings_path"]
    media_dir = cli_test_env["media_dir"]

    # Mock synthesize_podcast to return deterministic result without edge-tts
    def _mock_synthesize(*args: Any, **kwargs: Any) -> PodcastMediaResult:
        audio = media_dir / f"{run_id}.mp3"
        srt = media_dir / f"{run_id}.srt"
        meta = media_dir / f"{run_id}.json"
        audio.write_bytes(b"mock_mp3")
        srt.write_text("1\n00:00:00,000 --> 00:00:02,000\n[Minh] Chào quý vị.\n", encoding="utf-8")
        meta.write_text("{}", encoding="utf-8")
        return PodcastMediaResult(
            run_id=run_id,
            audio_path=audio,
            srt_path=srt,
            meta_path=meta,
            total_duration_sec=12.5,
            segment_count=2,
            voices={"Minh": "vi-VN-NamMinhNeural", "Lan": "vi-VN-HoaiMyNeural"},
        )

    monkeypatch.setattr("cyberpulse.cli.synthesize_podcast", _mock_synthesize)

    code = main(["media", "podcast", run_id, "--settings", str(settings_path)])
    assert code == 0
    captured = capsys.readouterr()
    assert f"Đang tạo audio podcast cho phiên chạy '{run_id}'" in captured.out
    assert "=== Podcast Audio Synthesized ===" in captured.out
    assert f"Run ID:         {run_id}" in captured.out
    assert "Segments:       2" in captured.out
    assert "Total Duration: 12.50s (00:12)" in captured.out


def test_cli_media_podcast_invalid_run_id_characters_exit_7(
    cli_test_env: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["media", "podcast", "invalid/run;id"])
    assert code == 7
    captured = capsys.readouterr()
    assert "invalid run_id" in captured.err


def test_cli_media_podcast_path_traversal_exit_7(
    cli_test_env: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings_path = cli_test_env["settings_path"]
    code = main(["media", "podcast", "../../../etc/passwd", "--settings", str(settings_path)])
    assert code == 7
    captured = capsys.readouterr()
    assert "invalid run_id" in captured.err or "path traversal" in captured.err


def test_cli_media_podcast_missing_editorial_exit_7(
    cli_test_env: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings_path = cli_test_env["settings_path"]
    code = main(["media", "podcast", "non-existent-run", "--settings", str(settings_path)])
    assert code == 7
    captured = capsys.readouterr()
    assert "editorial file not found" in captured.err


def test_cli_media_podcast_validation_gate_fails_exit_8(
    cli_test_env: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = cli_test_env["run_id"]
    settings_path = cli_test_env["settings_path"]
    val_dir = cli_test_env["validation_dir"]

    # Overwrite validation report to passed=False
    (val_dir / f"{run_id}.json").write_text(
        json.dumps({"passed": False, "errors": [{"code": "E_NUMBER"}]}),
        encoding="utf-8",
    )

    code = main(["media", "podcast", run_id, "--settings", str(settings_path)])
    assert code == 8
    captured = capsys.readouterr()
    assert "has not passed validation" in captured.err
    assert "--allow-unvalidated" in captured.err


def test_cli_media_podcast_validation_gate_missing_exit_8(
    cli_test_env: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = cli_test_env["run_id"]
    settings_path = cli_test_env["settings_path"]
    val_dir = cli_test_env["validation_dir"]

    # Delete validation report
    (val_dir / f"{run_id}.json").unlink()

    code = main(["media", "podcast", run_id, "--settings", str(settings_path)])
    assert code == 8
    captured = capsys.readouterr()
    assert "has not passed validation" in captured.err


def test_cli_media_podcast_validation_gate_override_exit_0(
    cli_test_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = cli_test_env["run_id"]
    settings_path = cli_test_env["settings_path"]
    val_dir = cli_test_env["validation_dir"]
    media_dir = cli_test_env["media_dir"]

    # Set validation report to failed
    (val_dir / f"{run_id}.json").write_text(
        json.dumps({"passed": False}), encoding="utf-8"
    )

    def _mock_synthesize(*args: Any, **kwargs: Any) -> PodcastMediaResult:
        audio = media_dir / f"{run_id}.mp3"
        srt = media_dir / f"{run_id}.srt"
        meta = media_dir / f"{run_id}.json"
        audio.write_bytes(b"mock")
        srt.write_text("cues", encoding="utf-8")
        meta.write_text("{}", encoding="utf-8")
        return PodcastMediaResult(
            run_id=run_id,
            audio_path=audio,
            srt_path=srt,
            meta_path=meta,
            total_duration_sec=10.0,
            segment_count=2,
            voices={"Minh": "vi-VN-NamMinhNeural", "Lan": "vi-VN-HoaiMyNeural"},
        )

    monkeypatch.setattr("cyberpulse.cli.synthesize_podcast", _mock_synthesize)

    code = main([
        "media",
        "podcast",
        run_id,
        "--settings",
        str(settings_path),
        "--allow-unvalidated",
    ])
    assert code == 0
    captured = capsys.readouterr()
    assert "[WARNING] Synthesizing podcast from UNVALIDATED editorial package!" in captured.err


def test_cli_media_podcast_output_exists_refuses_exit_7(
    cli_test_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = cli_test_env["run_id"]
    settings_path = cli_test_env["settings_path"]

    def _mock_synthesize(*args: Any, **kwargs: Any) -> PodcastMediaResult:
        raise MediaInputError("Output files already exist for run 'test-run-123'. Use --force to overwrite.")

    monkeypatch.setattr("cyberpulse.cli.synthesize_podcast", _mock_synthesize)

    code = main(["media", "podcast", run_id, "--settings", str(settings_path)])
    assert code == 7
    captured = capsys.readouterr()
    assert "Output files already exist" in captured.err


def test_cli_media_podcast_output_exists_force_overwrites_exit_0(
    cli_test_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = cli_test_env["run_id"]
    settings_path = cli_test_env["settings_path"]
    media_dir = cli_test_env["media_dir"]

    passed_force: list[bool] = []

    def _mock_synthesize(*args: Any, **kwargs: Any) -> PodcastMediaResult:
        options = kwargs.get("options")
        if options:
            passed_force.append(options.force)
        audio = media_dir / f"{run_id}.mp3"
        srt = media_dir / f"{run_id}.srt"
        meta = media_dir / f"{run_id}.json"
        audio.write_bytes(b"mock")
        srt.write_text("cues", encoding="utf-8")
        meta.write_text("{}", encoding="utf-8")
        return PodcastMediaResult(
            run_id=run_id,
            audio_path=audio,
            srt_path=srt,
            meta_path=meta,
            total_duration_sec=10.0,
            segment_count=2,
            voices={"Minh": "vi-VN-NamMinhNeural", "Lan": "vi-VN-HoaiMyNeural"},
        )

    monkeypatch.setattr("cyberpulse.cli.synthesize_podcast", _mock_synthesize)

    code = main(["media", "podcast", run_id, "--settings", str(settings_path), "--force"])
    assert code == 0
    assert passed_force == [True]


def test_cli_media_podcast_invalid_voice_arg_exit_7(
    cli_test_env: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = cli_test_env["run_id"]
    settings_path = cli_test_env["settings_path"]

    code = main([
        "media",
        "podcast",
        run_id,
        "--settings",
        str(settings_path),
        "--voice",
        "InvalidFormatNoEqualsSign",
    ])
    assert code == 7
    captured = capsys.readouterr()
    assert "invalid voice argument" in captured.err


def test_cli_media_podcast_missing_edge_tts_exit_12(
    cli_test_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = cli_test_env["run_id"]
    settings_path = cli_test_env["settings_path"]

    def _mock_synthesize(*args: Any, **kwargs: Any) -> PodcastMediaResult:
        raise MediaDependencyError(
            "edge-tts is required for podcast audio synthesis. Install with: pip install .[media]"
        )

    monkeypatch.setattr("cyberpulse.cli.synthesize_podcast", _mock_synthesize)

    code = main(["media", "podcast", run_id, "--settings", str(settings_path)])
    assert code == 12
    captured = capsys.readouterr()
    assert "edge-tts is required" in captured.err
    assert "pip install .[media]" in captured.err


def test_cli_media_podcast_synthesis_failure_exit_13(
    cli_test_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = cli_test_env["run_id"]
    settings_path = cli_test_env["settings_path"]

    def _mock_synthesize(*args: Any, **kwargs: Any) -> PodcastMediaResult:
        raise MediaSynthesisError("Audio assembly process failed with error code 1.")

    monkeypatch.setattr("cyberpulse.cli.synthesize_podcast", _mock_synthesize)

    code = main(["media", "podcast", run_id, "--settings", str(settings_path)])
    assert code == 13
    captured = capsys.readouterr()
    assert "Audio assembly process failed" in captured.err


def test_cli_media_podcast_config_error_exit_1(
    cli_test_env: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = cli_test_env["run_id"]
    code = main(["media", "podcast", run_id, "--settings", "non_existent_settings.toml"])
    assert code == 1
    captured = capsys.readouterr()
    assert "ConfigError" in captured.err


def test_cli_existing_commands_regression(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["version"])
    assert code == 0
    captured = capsys.readouterr()
    assert "cyberpulse" in captured.out
