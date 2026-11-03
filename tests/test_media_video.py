"""Hermetic unit tests for cyberpulse.media_video module."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from typing import Any
import pytest
from PIL import Image

from cyberpulse.config import Settings
from cyberpulse.export import export_editorial_bundle
from cyberpulse.media_video import (
    DEFAULT_RESOLUTION,
    DEFAULT_VOICE,
    SUPPORTED_RESOLUTIONS,
    FFmpegVideoAssembler,
    MediaDependencyError,
    MediaInputError,
    MediaSynthesisError,
    MediaValidationError,
    PurePythonVideoAssembler,
    SceneRenderResult,
    VideoMediaOptions,
    VideoMediaResult,
    build_pure_python_mp4,
    build_video_metadata,
    build_video_srt,
    get_default_video_assembler,
    render_cyber_frame,
    resolve_font,
    synthesize_video,
    wrap_text_to_width,
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
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure tests are strictly hermetic: no external network connections allowed."""
    import socket

    orig_connect = socket.socket.connect

    def _no_connect(sock: socket.socket, address: Any, *args: Any, **kwargs: Any) -> None:
        host = address[0] if isinstance(address, tuple) and len(address) > 0 else str(address)
        if host in ("127.0.0.1", "localhost", "::1"):
            return orig_connect(sock, address, *args, **kwargs)
        raise RuntimeError(f"Hermetic violation: external network connection attempted to {address}")

    monkeypatch.setattr("socket.socket.connect", _no_connect)


class FakeVideoTTSBackend:
    """Deterministic in-memory TTS backend generating mock MP3 frames."""

    def __init__(self, segment_durations: list[float] | None = None, fail_counts: int = 0) -> None:
        self.durations = segment_durations or [4.0, 5.0, 6.0]
        self.fail_counts = fail_counts
        self.call_count = 0

    async def synthesize_segment(self, text: str, voice: str, output_path: Path) -> float:
        self.call_count += 1
        if self.call_count <= self.fail_counts:
            raise ConnectionError("Simulated TTS network error")

        dur = self.durations[(self.call_count - 1 - self.fail_counts) % len(self.durations)]
        num_frames = max(1, round(dur / 0.024))
        frame = b"\xff\xf3\x64\xc4" + b"\x00" * 140
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(frame * num_frames)
        return num_frames * 0.024


@pytest.fixture
def sample_editorial_pkg() -> EditorialPackage:
    """Create a minimal valid EditorialPackage for testing."""
    return EditorialPackage(
        run_id="test-video-run-20261103T100000Z",
        language="vi",
        digest=Digest(
            title="Điểm tin an ninh mạng",
            intro="Tóm tắt tình hình an ninh mạng hôm nay.",
            sections=[
                DigestSection(
                    heading="Lỗ hổng mới",
                    body=CitedText(
                        text="Chi tiết lỗ hổng CVE được công bố.",
                        cited_article_ids=["art-1"],
                    ),
                )
            ],
        ),
        podcast=PodcastScript(
            title="Podcast An Ninh Mạng",
            segments=[
                PodcastSegment(
                    speaker="Minh",
                    line=CitedText(text="Minh chào quý vị.", cited_article_ids=["art-1"]),
                ),
            ],
        ),
        video=VideoScript(
            title="Điểm Tin Video 9:16",
            scenes=[
                VideoScene(
                    scene_no=1,
                    on_screen_text="Bản tin An ninh Mạng",
                    narration=CitedText(
                        text="Chào mừng quý vị đến với điểm tin an ninh mạng nóng nhất hôm nay.",
                        cited_article_ids=["art-1"],
                    ),
                    duration_sec=5.0,
                ),
                VideoScene(
                    scene_no=2,
                    on_screen_text="CISA: Vá khẩn cấp Ivanti",
                    narration=CitedText(
                        text="CISA cảnh báo hai lỗ hổng nghiêm trọng trên hệ thống Ivanti đang bị khai thác.",
                        cited_article_ids=["art-1"],
                    ),
                    duration_sec=8.0,
                ),
            ],
        ),
        captions=[
            Caption(
                platform="tiktok",
                text="Xem video an ninh mạng hôm nay #CyberSecurity",
                hashtags=["#CyberSecurity"],
                cited_article_ids=["art-1"],
            )
        ],
    )


@pytest.fixture
def mock_settings(tmp_path: Path) -> Settings:
    """Create minimal Settings instance with tmp_path paths."""
    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        project_name="CyberPulse AI Test",
        topics=["cybersecurity"],
        paths={
            "data_dir": tmp_path / "data",
            "output_dir": out_dir,
            "log_dir": tmp_path / "logs",
        },
    )


# ==============================================================================
# Frame Rendering Tests
# ==============================================================================


def test_render_cyber_frame_dimensions_and_theme() -> None:
    """Test rendering 1080x1920 and 720x1280 frames with Dark Cybersecurity theme."""
    # 1. 1080x1920
    img_1080 = render_cyber_frame(
        scene_no=1,
        total_scenes=2,
        on_screen_text="CISA: Vá khẩn cấp Ivanti",
        narration_text="CISA cảnh báo lỗ hổng trên hệ thống Ivanti.",
        duration_sec=6.0,
        run_id="run-test-1080",
        width=1080,
        height=1920,
    )
    assert img_1080.size == (1080, 1920)
    # Background color check near top-left inside corner
    pixel = img_1080.getpixel((10, 10))
    assert pixel == (15, 23, 42)  # #0F172A

    # 2. 720x1280
    img_720 = render_cyber_frame(
        scene_no=2,
        total_scenes=2,
        on_screen_text="FBI công bố vụ hack",
        narration_text="FBI xác nhận tin tặc đánh cắp tài sản tiền số.",
        duration_sec=8.0,
        run_id="run-test-720",
        width=720,
        height=1280,
    )
    assert img_720.size == (720, 1280)

    # 3. Cover Slide
    img_cover = render_cyber_frame(
        scene_no=1,
        total_scenes=2,
        on_screen_text="BẢN TIN VIDEO BÌA",
        narration_text="",
        duration_sec=14.0,
        run_id="run-test-cover",
        width=1080,
        height=1920,
        is_cover=True,
    )
    assert img_cover.size == (1080, 1920)


def test_wrap_text_to_width() -> None:
    """Test word-wrapping helper produces bounded line lengths."""
    from PIL import ImageDraw

    img = Image.new("RGB", (500, 500))
    draw = ImageDraw.Draw(img)
    font = resolve_font(24)

    text = "Một chuỗi văn bản tiếng Việt rất dài cần được tự động ngắt dòng hợp lý trong khung hình."
    lines = wrap_text_to_width(text, font, max_width=250, draw=draw)
    assert len(lines) >= 2
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        assert (bbox[2] - bbox[0]) <= 250


def test_resolve_font_never_fails() -> None:
    """Test resolve_font returns usable font on any platform."""
    f1 = resolve_font(20, bold=False)
    assert f1 is not None
    f2 = resolve_font(36, bold=True)
    assert f2 is not None


# ==============================================================================
# Pure-Python MP4 Container Tests
# ==============================================================================


def test_build_pure_python_mp4_box_structure() -> None:
    """Test building minimal ISO BMFF MP4 with valid ftyp, moov, and mdat boxes."""
    payload = b"CYBERPULSE_MOCK_STREAM_BYTES"
    mp4_bytes = build_pure_python_mp4(
        width=1080,
        height=1920,
        duration_sec=12.5,
        media_data=payload,
    )
    assert len(mp4_bytes) > 200
    assert mp4_bytes[4:8] == b"ftyp"
    assert b"isom" in mp4_bytes[:30]
    assert b"moov" in mp4_bytes
    assert b"mvhd" in mp4_bytes
    assert b"trak" in mp4_bytes
    assert b"mdat" in mp4_bytes
    assert payload in mp4_bytes


def test_pure_python_video_assembler(tmp_path: Path) -> None:
    """Test PurePythonVideoAssembler creates an MP4 file with correct attributes."""
    asm = PurePythonVideoAssembler()
    out_file = tmp_path / "test_pure.mp4"

    # Create dummy slide
    dummy_img = tmp_path / "slide_01.png"
    Image.new("RGB", (1080, 1920)).save(dummy_img)

    scenes = [
        SceneRenderResult(
            scene_no=1,
            on_screen_text="Text 1",
            narration="Narr 1",
            slide_image_path=dummy_img,
            audio_path=None,
            duration_sec=5.0,
            start_sec=0.0,
            end_sec=5.0,
        )
    ]

    dur = asm.assemble_video(
        scenes=scenes,
        assembled_audio_path=None,
        output_path=out_file,
        width=1080,
        height=1920,
        fps=30,
    )
    assert dur == 5.0
    assert out_file.exists()
    assert out_file.stat().st_size > 100


def test_ffmpeg_video_assembler_missing_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test FFmpegVideoAssembler raises MediaDependencyError if ffmpeg is missing."""
    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    with pytest.raises(MediaDependencyError, match="ffmpeg executable not found"):
        FFmpegVideoAssembler()


def test_ffmpeg_video_assembler_mock_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test FFmpegVideoAssembler issues expected CLI command when ffmpeg is available."""
    monkeypatch.setattr(shutil, "which", lambda cmd: "C:/fake/ffmpeg.exe" if cmd == "ffmpeg" else None)

    cmd_executed: list[str] = []

    def _fake_run(cmd: list[str], *args: Any, **kwargs: Any) -> None:
        cmd_executed.extend(cmd)
        # Create output file so existence check passes
        out_f = Path(cmd[-1])
        out_f.write_bytes(b"FAKE_FFMPEG_MP4")

    monkeypatch.setattr("subprocess.run", _fake_run)

    asm = FFmpegVideoAssembler()
    out_file = tmp_path / "ffmpeg_out.mp4"
    dummy_img = tmp_path / "slide_01.png"
    Image.new("RGB", (1080, 1920)).save(dummy_img)

    scenes = [
        SceneRenderResult(
            scene_no=1,
            on_screen_text="Text",
            narration="Narr",
            slide_image_path=dummy_img,
            audio_path=None,
            duration_sec=7.0,
            start_sec=0.0,
            end_sec=7.0,
        )
    ]

    dur = asm.assemble_video(
        scenes=scenes,
        assembled_audio_path=None,
        output_path=out_file,
        width=1080,
        height=1920,
        fps=30,
    )
    assert dur == 7.0
    assert out_file.exists()
    assert "-s" in cmd_executed
    assert "1080x1920" in cmd_executed
    assert "libx264" in cmd_executed


def test_get_default_video_assembler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test assembler factory returns PurePython when ffmpeg missing, FFmpeg when present."""
    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    asm1 = get_default_video_assembler(prefer_ffmpeg=True)
    assert isinstance(asm1, PurePythonVideoAssembler)

    monkeypatch.setattr(shutil, "which", lambda cmd: "ffmpeg" if cmd == "ffmpeg" else None)
    asm2 = get_default_video_assembler(prefer_ffmpeg=True)
    assert isinstance(asm2, FFmpegVideoAssembler)


# ==============================================================================
# SRT & Metadata Tests
# ==============================================================================


def test_build_video_srt(tmp_path: Path) -> None:
    """Test SRT cue formatting has correct timestamps and scene numbers."""
    dummy_img = tmp_path / "slide.png"
    dummy_img.write_bytes(b"")

    scenes = [
        SceneRenderResult(
            scene_no=1,
            on_screen_text="Tiêu đề 1",
            narration="Lời dẫn 1",
            slide_image_path=dummy_img,
            audio_path=None,
            duration_sec=5.5,
            start_sec=0.0,
            end_sec=5.5,
        ),
        SceneRenderResult(
            scene_no=2,
            on_screen_text="Tiêu đề 2",
            narration="Lời dẫn 2",
            slide_image_path=dummy_img,
            audio_path=None,
            duration_sec=4.5,
            start_sec=5.5,
            end_sec=10.0,
        ),
    ]

    srt_str = build_video_srt(scenes)
    assert "1\n00:00:00,000 --> 00:00:05,500\n[SCENE 1] Tiêu đề 1\nLời dẫn 1" in srt_str
    assert "2\n00:00:05,500 --> 00:00:10,000\n[SCENE 2] Tiêu đề 2\nLời dẫn 2" in srt_str


def test_build_video_metadata(tmp_path: Path) -> None:
    """Test technical metadata dictionary matches specification."""
    dummy_img = tmp_path / "slide.png"
    dummy_img.write_bytes(b"")

    scenes = [
        SceneRenderResult(
            scene_no=1,
            on_screen_text="Text",
            narration="Narr",
            slide_image_path=dummy_img,
            audio_path=None,
            duration_sec=5.0,
            start_sec=0.0,
            end_sec=5.0,
        )
    ]
    opts = VideoMediaOptions(resolution="1080x1920", voice="vi-VN-NamMinhNeural", preview_only=False)
    now = datetime(2026, 11, 3, 10, 0, 0, tzinfo=timezone.utc)

    meta = build_video_metadata(
        run_id="run-123",
        video_title="Video Test",
        options=opts,
        scenes=scenes,
        total_duration_sec=5.0,
        video_file_name="run-123.mp4",
        created_at=now,
    )
    assert meta["run_id"] == "run-123"
    assert meta["aspect_ratio"] == "9:16"
    assert meta["width"] == 1080
    assert meta["height"] == 1920
    assert meta["total_duration_sec"] == 5.0
    assert meta["scene_count"] == 1
    assert meta["files"]["video"] == "run-123.mp4"
    assert meta["files"]["subtitles"] == "run-123.srt"


# ==============================================================================
# Full Pipeline Synthesis Tests
# ==============================================================================


def test_synthesize_video_full_pipeline(
    tmp_path: Path,
    sample_editorial_pkg: EditorialPackage,
    mock_settings: Settings,
) -> None:
    """Test end-to-end video synthesis producing mp4, srt, json, cover, and scene slides."""
    output_media = tmp_path / "output" / "media" / "video"
    fake_tts = FakeVideoTTSBackend(segment_durations=[5.0, 8.0])
    pure_asm = PurePythonVideoAssembler()

    opts = VideoMediaOptions(force=False, preview_only=False)
    res = synthesize_video(
        run_id=sample_editorial_pkg.run_id,
        editorial_pkg=sample_editorial_pkg,
        settings=mock_settings,
        options=opts,
        output_media_dir=output_media,
        tts_backend=fake_tts,
        assembler=pure_asm,
    )

    assert isinstance(res, VideoMediaResult)
    assert res.run_id == sample_editorial_pkg.run_id
    assert res.video_path is not None
    assert res.video_path.exists()
    assert res.video_path.name == f"{sample_editorial_pkg.run_id}.mp4"
    assert res.srt_path.exists()
    assert res.meta_path.exists()
    assert res.cover_image.exists()
    assert len(res.scene_images) == 2
    for sc_img in res.scene_images:
        assert sc_img.exists()
        with Image.open(sc_img) as im:
            assert im.size == (1080, 1920)

    # Check metadata file
    meta_data = json.loads(res.meta_path.read_text(encoding="utf-8"))
    assert meta_data["scene_count"] == 2
    assert meta_data["aspect_ratio"] == "9:16"
    assert meta_data["preview_only"] is False


def test_synthesize_video_preview_only(
    tmp_path: Path,
    sample_editorial_pkg: EditorialPackage,
    mock_settings: Settings,
) -> None:
    """Test preview_only=True generates slides and metadata without mp4 encoding."""
    output_media = tmp_path / "output" / "media" / "video"
    opts = VideoMediaOptions(preview_only=True, force=False)

    res = synthesize_video(
        run_id=sample_editorial_pkg.run_id,
        editorial_pkg=sample_editorial_pkg,
        settings=mock_settings,
        options=opts,
        output_media_dir=output_media,
    )

    assert res.video_path is None
    assert not (output_media / f"{sample_editorial_pkg.run_id}.mp4").exists()
    assert res.cover_image.exists()
    assert res.srt_path.exists()
    assert res.meta_path.exists()
    assert len(res.scene_images) == 2

    meta = json.loads(res.meta_path.read_text(encoding="utf-8"))
    assert meta["preview_only"] is True
    assert meta["files"]["video"] is None


def test_synthesize_video_overwrite_without_force(
    tmp_path: Path,
    sample_editorial_pkg: EditorialPackage,
    mock_settings: Settings,
) -> None:
    """Test running synthesis when files exist raises MediaInputError without --force, succeeds with --force."""
    output_media = tmp_path / "output" / "media" / "video"
    opts = VideoMediaOptions(preview_only=True, force=False)

    # First run succeeds
    synthesize_video(
        run_id=sample_editorial_pkg.run_id,
        editorial_pkg=sample_editorial_pkg,
        settings=mock_settings,
        options=opts,
        output_media_dir=output_media,
    )

    # Second run without force raises collision error
    with pytest.raises(MediaInputError, match="Output files already exist"):
        synthesize_video(
            run_id=sample_editorial_pkg.run_id,
            editorial_pkg=sample_editorial_pkg,
            settings=mock_settings,
            options=opts,
            output_media_dir=output_media,
        )

    # Third run with force=True succeeds
    opts_force = VideoMediaOptions(preview_only=True, force=True)
    res3 = synthesize_video(
        run_id=sample_editorial_pkg.run_id,
        editorial_pkg=sample_editorial_pkg,
        settings=mock_settings,
        options=opts_force,
        output_media_dir=output_media,
    )
    assert res3.cover_image.exists()


def test_synthesize_video_input_validations(
    tmp_path: Path,
    sample_editorial_pkg: EditorialPackage,
    mock_settings: Settings,
) -> None:
    """Test input validations: bad run_id, bad resolution, bad voice."""
    out_dir = tmp_path / "media"

    # Bad run_id
    with pytest.raises(MediaInputError, match="Invalid run_id"):
        synthesize_video(
            run_id="bad/run/id!",
            editorial_pkg=sample_editorial_pkg,
            settings=mock_settings,
            options=VideoMediaOptions(),
            output_media_dir=out_dir,
        )

    # Bad resolution
    with pytest.raises(MediaInputError, match="Unsupported resolution"):
        synthesize_video(
            run_id="run-valid",
            editorial_pkg=sample_editorial_pkg,
            settings=mock_settings,
            options=VideoMediaOptions(resolution="1920x1080"),  # Horizontal not allowed
            output_media_dir=out_dir,
        )

    # Bad voice
    with pytest.raises(MediaInputError, match="Invalid voice format"):
        synthesize_video(
            run_id="run-valid",
            editorial_pkg=sample_editorial_pkg,
            settings=mock_settings,
            options=VideoMediaOptions(voice="not-a-voice"),
            output_media_dir=out_dir,
        )


def test_resolution_720x1280_output(
    tmp_path: Path,
    sample_editorial_pkg: EditorialPackage,
    mock_settings: Settings,
) -> None:
    """Test resolution='720x1280' generates 720x1280 frames."""
    output_media = tmp_path / "output" / "media" / "video"
    opts = VideoMediaOptions(resolution="720x1280", preview_only=True)

    res = synthesize_video(
        run_id=sample_editorial_pkg.run_id,
        editorial_pkg=sample_editorial_pkg,
        settings=mock_settings,
        options=opts,
        output_media_dir=output_media,
    )
    assert res.resolution == "720x1280"
    for sc in res.scene_images:
        with Image.open(sc) as im:
            assert im.size == (720, 1280)


def test_export_editorial_bundle_includes_video(
    tmp_path: Path,
    sample_editorial_pkg: EditorialPackage,
) -> None:
    """Test export_editorial_bundle packages <run_id>.mp4 when present."""
    output_dir = tmp_path / "output"
    video_dir = output_dir / "media" / "video"
    video_dir.mkdir(parents=True)

    run_id = sample_editorial_pkg.run_id
    mp4_file = video_dir / f"{run_id}.mp4"
    mp4_file.write_bytes(b"MOCK_MP4_CONTENT")

    export_root = tmp_path / "export"
    res = export_editorial_bundle(
        package=sample_editorial_pkg,
        run_id=run_id,
        output_dir=output_dir,
        export_root=export_root,
    )

    assert res.has_video is True
    exported_mp4 = res.export_dir / f"{run_id}.mp4"
    assert exported_mp4.exists()
    assert exported_mp4.read_bytes() == b"MOCK_MP4_CONTENT"
    assert f"{run_id}.mp4" in res.files_created
