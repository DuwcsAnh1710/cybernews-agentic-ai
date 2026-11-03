"""CLI unit tests for cyberpulse media video subcommand."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import pytest

from cyberpulse.cli import main
from cyberpulse.media_video import (
    MediaDependencyError,
    MediaInputError,
    MediaSynthesisError,
    VideoMediaResult,
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
    media_dir = output_dir / "media" / "video"

    editorial_dir.mkdir(parents=True)
    validation_dir.mkdir(parents=True)
    media_dir.mkdir(parents=True)

    settings_content = f"""project_name = "CyberPulse AI"
topics = ["cybersecurity"]
output_language = "vi"

[paths]
output_dir = "{output_dir.as_posix()}"
"""
    settings_path.write_text(settings_content, encoding="utf-8")

    run_id = "test-video-run-123"
    pkg = EditorialPackage(
        run_id=run_id,
        language="vi",
        digest=Digest(
            title="Điểm tin an ninh mạng",
            intro="Tóm tắt tình hình an ninh mạng.",
            sections=[
                DigestSection(
                    heading="Tin 1",
                    body=CitedText(text="Nội dung 1.", cited_article_ids=["a1"]),
                )
            ],
        ),
        podcast=PodcastScript(
            title="Podcast 1",
            segments=[
                PodcastSegment(
                    speaker="Minh",
                    line=CitedText(text="Minh chào quý vị.", cited_article_ids=["a1"]),
                ),
            ],
        ),
        video=VideoScript(
            title="Video Bản Tin 9:16",
            scenes=[
                VideoScene(
                    scene_no=1,
                    on_screen_text="Bản tin An ninh Mạng",
                    narration=CitedText(
                        text="Chào mừng quý vị đến với bản tin hôm nay.",
                        cited_article_ids=["a1"],
                    ),
                    duration_sec=5.0,
                ),
                VideoScene(
                    scene_no=2,
                    on_screen_text="Ivanti vá khẩn cấp",
                    narration=CitedText(
                        text="CISA yêu cầu vá khẩn cấp lỗ hổng bảo mật.",
                        cited_article_ids=["a1"],
                    ),
                    duration_sec=6.0,
                ),
            ],
        ),
        captions=[
            Caption(
                platform="tiktok",
                text="Điểm tin video #CyberSecurity",
                hashtags=["#CyberSecurity"],
                cited_article_ids=["a1"],
            )
        ],
    )

    editorial_file = editorial_dir / f"{run_id}.json"
    editorial_file.write_text(pkg.model_dump_json(indent=2), encoding="utf-8")

    # Validated report
    val_file = validation_dir / f"{run_id}.json"
    val_file.write_text(json.dumps({"run_id": run_id, "passed": True, "issues": []}), encoding="utf-8")

    return {
        "settings_path": settings_path,
        "output_dir": output_dir,
        "editorial_dir": editorial_dir,
        "validation_dir": validation_dir,
        "media_dir": media_dir,
        "run_id": run_id,
        "pkg": pkg,
    }


def test_cli_media_video_success(
    cli_test_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test CLI media video runs successfully and prints technical summary."""
    run_id = cli_test_env["run_id"]
    settings_path = cli_test_env["settings_path"]
    media_dir = cli_test_env["media_dir"]

    # Mock synthesize_video to avoid real TTS and FFmpeg in CLI test
    mock_res = VideoMediaResult(
        run_id=run_id,
        video_path=media_dir / f"{run_id}.mp4",
        srt_path=media_dir / f"{run_id}.srt",
        meta_path=media_dir / f"{run_id}.json",
        cover_image=media_dir / f"{run_id}_cover.png",
        scene_images=[media_dir / f"{run_id}_scenes" / "scene_01.png"],
        total_duration_sec=11.0,
        scene_count=2,
        resolution="1080x1920",
        voice="vi-VN-NamMinhNeural",
        preview_only=False,
    )
    monkeypatch.setattr("cyberpulse.cli.synthesize_video", lambda **kw: mock_res)

    argv = ["media", "video", run_id, "--settings", str(settings_path)]
    code = main(argv)
    assert code == 0

    captured = capsys.readouterr()
    assert "=== Video Media Synthesized ===" in captured.out
    assert f"Run ID:         {run_id}" in captured.out
    assert "Resolution:     1080x1920 (9:16 Vertical)" in captured.out
    assert "Total Duration: 11.00s" in captured.out


def test_cli_media_video_preview_only(
    cli_test_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test CLI media video --preview-only outputs Preview Only notice."""
    run_id = cli_test_env["run_id"]
    settings_path = cli_test_env["settings_path"]
    media_dir = cli_test_env["media_dir"]

    mock_res = VideoMediaResult(
        run_id=run_id,
        video_path=None,
        srt_path=media_dir / f"{run_id}.srt",
        meta_path=media_dir / f"{run_id}.json",
        cover_image=media_dir / f"{run_id}_cover.png",
        scene_images=[media_dir / f"{run_id}_scenes" / "scene_01.png"],
        total_duration_sec=11.0,
        scene_count=2,
        resolution="1080x1920",
        voice="vi-VN-NamMinhNeural",
        preview_only=True,
    )
    called_opts = []

    def _mock_synth(**kw: Any) -> VideoMediaResult:
        called_opts.append(kw["options"])
        return mock_res

    monkeypatch.setattr("cyberpulse.cli.synthesize_video", _mock_synth)

    argv = ["media", "video", run_id, "--settings", str(settings_path), "--preview-only"]
    code = main(argv)
    assert code == 0
    assert len(called_opts) == 1
    assert called_opts[0].preview_only is True

    captured = capsys.readouterr()
    assert "Video File:     None (Preview Only)" in captured.out


def test_cli_media_video_resolution_720x1280(
    cli_test_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test CLI accepts --resolution 720x1280."""
    run_id = cli_test_env["run_id"]
    settings_path = cli_test_env["settings_path"]
    media_dir = cli_test_env["media_dir"]

    mock_res = VideoMediaResult(
        run_id=run_id,
        video_path=media_dir / f"{run_id}.mp4",
        srt_path=media_dir / f"{run_id}.srt",
        meta_path=media_dir / f"{run_id}.json",
        cover_image=media_dir / f"{run_id}_cover.png",
        scene_images=[],
        total_duration_sec=11.0,
        scene_count=2,
        resolution="720x1280",
        voice="vi-VN-NamMinhNeural",
        preview_only=False,
    )
    monkeypatch.setattr("cyberpulse.cli.synthesize_video", lambda **kw: mock_res)

    argv = ["media", "video", run_id, "--settings", str(settings_path), "--resolution", "720x1280"]
    code = main(argv)
    assert code == 0

    captured = capsys.readouterr()
    assert "Resolution:     720x1280 (9:16 Vertical)" in captured.out


def test_cli_media_video_force_flag_passed(
    cli_test_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test CLI passes force flag to options."""
    run_id = cli_test_env["run_id"]
    settings_path = cli_test_env["settings_path"]
    media_dir = cli_test_env["media_dir"]

    called_opts = []

    def _mock_synth(**kw: Any) -> VideoMediaResult:
        called_opts.append(kw["options"])
        return VideoMediaResult(
            run_id=run_id,
            video_path=media_dir / f"{run_id}.mp4",
            srt_path=media_dir / f"{run_id}.srt",
            meta_path=media_dir / f"{run_id}.json",
            cover_image=media_dir / f"{run_id}_cover.png",
            scene_images=[],
            total_duration_sec=10.0,
            scene_count=2,
            resolution="1080x1920",
            voice="vi-VN-NamMinhNeural",
            preview_only=False,
        )

    monkeypatch.setattr("cyberpulse.cli.synthesize_video", _mock_synth)

    argv = ["media", "video", run_id, "--settings", str(settings_path), "--force"]
    code = main(argv)
    assert code == 0
    assert called_opts[0].force is True


def test_cli_media_video_invalid_run_id(cli_test_env: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    """Test CLI exits 7 on invalid run_id characters."""
    settings_path = cli_test_env["settings_path"]
    code = main(["media", "video", "invalid/run!id", "--settings", str(settings_path)])
    assert code == 7
    captured = capsys.readouterr()
    assert "Error: invalid run_id" in captured.err


def test_cli_media_video_missing_editorial(cli_test_env: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    """Test CLI exits 7 if editorial package does not exist."""
    settings_path = cli_test_env["settings_path"]
    code = main(["media", "video", "non-existent-run-999", "--settings", str(settings_path)])
    assert code == 7
    captured = capsys.readouterr()
    assert "Error: editorial file not found" in captured.err


def test_cli_media_video_unvalidated_without_override(
    cli_test_env: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test CLI exits 8 if run has not passed validation and --allow-unvalidated is not passed."""
    val_file = cli_test_env["validation_dir"] / f"{cli_test_env['run_id']}.json"
    val_file.write_text(json.dumps({"passed": False}), encoding="utf-8")

    argv = ["media", "video", cli_test_env["run_id"], "--settings", str(cli_test_env["settings_path"])]
    code = main(argv)
    assert code == 8
    captured = capsys.readouterr()
    assert "has not passed validation" in captured.err


def test_cli_media_video_unvalidated_with_override(
    cli_test_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test CLI allows synthesis of unvalidated run when --allow-unvalidated is set."""
    val_file = cli_test_env["validation_dir"] / f"{cli_test_env['run_id']}.json"
    val_file.write_text(json.dumps({"passed": False}), encoding="utf-8")

    run_id = cli_test_env["run_id"]
    media_dir = cli_test_env["media_dir"]

    mock_res = VideoMediaResult(
        run_id=run_id,
        video_path=media_dir / f"{run_id}.mp4",
        srt_path=media_dir / f"{run_id}.srt",
        meta_path=media_dir / f"{run_id}.json",
        cover_image=media_dir / f"{run_id}_cover.png",
        scene_images=[],
        total_duration_sec=10.0,
        scene_count=2,
        resolution="1080x1920",
        voice="vi-VN-NamMinhNeural",
        preview_only=False,
    )
    monkeypatch.setattr("cyberpulse.cli.synthesize_video", lambda **kw: mock_res)

    argv = [
        "media",
        "video",
        cli_test_env["run_id"],
        "--settings",
        str(cli_test_env["settings_path"]),
        "--allow-unvalidated",
    ]
    code = main(argv)
    assert code == 0
    captured = capsys.readouterr()
    assert "[WARNING] Synthesizing video from UNVALIDATED editorial package!" in captured.err
    assert "=== Video Media Synthesized ===" in captured.out


def test_cli_media_video_custom_output_dir(
    cli_test_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Test CLI routes output to custom --output-dir."""
    run_id = cli_test_env["run_id"]
    settings_path = cli_test_env["settings_path"]
    custom_dir = tmp_path / "custom_video_export"

    called_output_dirs = []

    def _mock_synth(**kw: Any) -> VideoMediaResult:
        called_output_dirs.append(kw["output_media_dir"])
        return VideoMediaResult(
            run_id=run_id,
            video_path=custom_dir / f"{run_id}.mp4",
            srt_path=custom_dir / f"{run_id}.srt",
            meta_path=custom_dir / f"{run_id}.json",
            cover_image=custom_dir / f"{run_id}_cover.png",
            scene_images=[],
            total_duration_sec=10.0,
            scene_count=2,
            resolution="1080x1920",
            voice="vi-VN-NamMinhNeural",
            preview_only=False,
        )

    monkeypatch.setattr("cyberpulse.cli.synthesize_video", _mock_synth)

    argv = [
        "media",
        "video",
        run_id,
        "--settings",
        str(settings_path),
        "--output-dir",
        str(custom_dir),
    ]
    code = main(argv)
    assert code == 0
    assert len(called_output_dirs) == 1
    assert called_output_dirs[0] == custom_dir


def test_cli_media_video_error_mappings(
    cli_test_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test proper exit code mapping for MediaDependencyError (12), MediaSynthesisError (13), MediaInputError (7)."""
    run_id = cli_test_env["run_id"]
    settings_path = cli_test_env["settings_path"]

    # 1. Dependency error -> 12
    def _raise_dep(**kw: Any) -> Any:
        raise MediaDependencyError("Missing tool")

    monkeypatch.setattr("cyberpulse.cli.synthesize_video", _raise_dep)
    assert main(["media", "video", run_id, "--settings", str(settings_path)]) == 12

    # 2. Synthesis error -> 13
    def _raise_synth(**kw: Any) -> Any:
        raise MediaSynthesisError("Encoding crashed")

    monkeypatch.setattr("cyberpulse.cli.synthesize_video", _raise_synth)
    assert main(["media", "video", run_id, "--settings", str(settings_path)]) == 13

    # 3. Input error -> 7
    def _raise_input(**kw: Any) -> Any:
        raise MediaInputError("Collision")

    monkeypatch.setattr("cyberpulse.cli.synthesize_video", _raise_input)
    assert main(["media", "video", run_id, "--settings", str(settings_path)]) == 7
