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
    """Test rendering 1080x1920 and 720x1280 frames with Hallmark Handcrafted Editorial theme."""
    from cyberpulse.media_video import HALLMARK_PAPER

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
    # Background color check near top-left inside corner (Hallmark Warm Archival Paper #F6F2EA)
    pixel = img_1080.getpixel((10, 10))
    assert pixel == HALLMARK_PAPER

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


# ==============================================================================
# Podcast-to-Video Sub-60s Curation, Audio Slicing & HyperFrames Tests
# ==============================================================================


def test_format_editorial_headline_preserves_acronyms() -> None:
    """Test shouting ALL-CAPS headlines are converted to natural sentence case while keeping technical acronyms."""
    from cyberpulse.media_video import format_editorial_headline

    h1 = format_editorial_headline("[CISA KEV ALERT] • LỖ HỔNG XÁC THỰC CVE-2024-27834 TRÊN APPLE IOS!")
    assert "CVE-2024-27834" in h1
    assert "Apple" in h1
    assert "iOS" in h1
    assert "[CISA" not in h1
    assert "•" not in h1


def test_curate_podcast_highlights_under_60s(mock_settings: Settings) -> None:
    """Test multi-segment 2-MC podcast (~140s) is automatically trimmed under 60s keeping core stories."""
    from cyberpulse.media_video import curate_podcast_highlights

    pkg = EditorialPackage(
        run_id="run-podcast-curate-test",
        language="vi",
        digest=Digest(
            title="Điểm tin an ninh mạng",
            intro="Tóm tắt bản tin.",
            sections=[
                DigestSection(
                    heading="OpenSSH phát hành bản vá khẩn cấp trên Alpine Linux",
                    body=CitedText(text="Chi tiết lỗi OpenSSH.", cited_article_ids=["a1"]),
                ),
                DigestSection(
                    heading="Mô hình AI khai thác 948 lỗ hổng nhân Linux",
                    body=CitedText(text="Chi tiết 948 lỗ hổng kernel.", cited_article_ids=["a2"]),
                ),
                DigestSection(
                    heading="Chiến dịch gián điệp zero-click trên thiết bị di động",
                    body=CitedText(text="Chi tiết chiến dịch zero-click.", cited_article_ids=["a3"]),
                ),
            ],
        ),
        podcast=PodcastScript(
            title="Podcast Sáng",
            segments=[
                PodcastSegment(
                    speaker="Minh",
                    line=CitedText(
                        text="Xin chào các bạn nghe đài, chào mừng quay trở lại với bản tin. Mình là Minh và đồng hành cùng mình hôm nay là chị Lan.",
                        cited_article_ids=["a1", "a2", "a3"],
                    ),
                ),
                PodcastSegment(
                    speaker="Lan",
                    line=CitedText(
                        text="Chào Minh. Tin đầu tiên là OpenSSH vừa phát hành phiên bản 10.6p2 vá lỗ hổng nghiêm trọng trên Alpine Linux.",
                        cited_article_ids=["a1"],
                    ),
                ),
                PodcastSegment(
                    speaker="Minh",
                    line=CitedText(
                        text="Lỗ hổng này nghiêm trọng ra sao vậy chị Lan?",
                        cited_article_ids=["a1"],
                    ),
                ),
                PodcastSegment(
                    speaker="Lan",
                    line=CitedText(
                        text="Kẻ tấn công có thể dùng mật khẩu dài từ 257 đến 1024 byte để vượt qua xác thực SSH trên máy chủ.",
                        cited_article_ids=["a1"],
                    ),
                ),
                PodcastSegment(
                    speaker="Minh",
                    line=CitedText(
                        text="Tiếp theo là nghiên cứu từ OpenAI khi mô hình AI của họ tìm thấy và khai thác 948 lỗ hổng leo thang đặc quyền trong nhân Linux.",
                        cited_article_ids=["a2"],
                    ),
                ),
                PodcastSegment(
                    speaker="Lan",
                    line=CitedText(
                        text="Ngoài ra các nhà nghiên cứu vừa phát hiện chiến dịch tấn công zero-click nhắm vào người dùng iPhone và Android để đánh cắp dữ liệu.",
                        cited_article_ids=["a3"],
                    ),
                ),
                PodcastSegment(
                    speaker="Minh",
                    line=CitedText(
                        text="Cảm ơn chị Lan. Cảm ơn các bạn đã theo dõi podcast hôm nay, hẹn gặp lại các bạn!",
                        cited_article_ids=["a1", "a2", "a3"],
                    ),
                ),
            ],
        ),
        video=VideoScript(
            title="Video Điểm Tin",
            scenes=[
                VideoScene(
                    scene_no=1,
                    on_screen_text="OpenSSH Vá Lỗi Alpine Linux",
                    narration=CitedText(text="OpenSSH ra bản vá 10.6p2.", cited_article_ids=["a1"]),
                    duration_sec=12.0,
                )
            ],
        ),
        captions=[
            Caption(
                platform="tiktok",
                text="Điểm tin an ninh mạng",
                hashtags=["#CyberPulse"],
                cited_article_ids=["a1"],
            )
        ],
    )

    podcast_meta = {
        "segments": [
            {"index": 0, "speaker": "Minh", "start_sec": 0.0, "end_sec": 14.0, "duration_sec": 14.0},
            {"index": 1, "speaker": "Lan", "start_sec": 14.35, "end_sec": 30.35, "duration_sec": 16.0},
            {"index": 2, "speaker": "Minh", "start_sec": 30.7, "end_sec": 37.7, "duration_sec": 7.0},
            {"index": 3, "speaker": "Lan", "start_sec": 38.05, "end_sec": 56.05, "duration_sec": 18.0},
            {"index": 4, "speaker": "Minh", "start_sec": 56.4, "end_sec": 73.4, "duration_sec": 17.0},
            {"index": 5, "speaker": "Lan", "start_sec": 73.75, "end_sec": 90.75, "duration_sec": 17.0},
            {"index": 6, "speaker": "Minh", "start_sec": 91.1, "end_sec": 103.1, "duration_sec": 12.0},
        ]
    }

    clips = curate_podcast_highlights(
        editorial_pkg=pkg,
        settings=mock_settings,
        podcast_meta=podcast_meta,
        max_duration_sec=56.0,
    )

    assert len(clips) >= 2
    total_dur = sum(c.duration_sec for c in clips) + (len(clips) - 1) * 0.24
    assert total_dur < 60.0
    # Greeting (index 0) and sign-off (index 6) should be excluded
    selected_indices = [c.segment_index for c in clips]
    assert 0 not in selected_indices
    assert 6 not in selected_indices
    # Both speakers (Minh & Lan) should be represented
    speakers = {c.speaker for c in clips}
    assert "Minh" in speakers and "Lan" in speakers


def test_slice_mp3_bytes_and_energy_profile() -> None:
    """Test slicing valid MPEG frames by timestamp and extracting a voice energy profile."""
    from cyberpulse.media_video import extract_mp3_energy_profile, slice_mp3_bytes

    # 100 MPEG-2 Layer 3 frames (~2.4 seconds at 24ms/frame)
    single_frame = b"\xff\xf3\x64\xc4" + bytes((i * 17) % 256 for i in range(140))
    mock_mp3 = single_frame * 100

    sliced, dur = slice_mp3_bytes(mock_mp3, start_sec=0.48, end_sec=1.44)
    assert len(sliced) > 0
    assert 0.85 <= dur <= 1.05

    profile = extract_mp3_energy_profile(sliced, num_points=28)
    assert len(profile) == 28
    assert all(0.15 <= p <= 1.0 for p in profile)


def test_synthesize_video_auto_podcast_and_hyperframes(
    tmp_path: Path,
    sample_editorial_pkg: EditorialPackage,
    mock_settings: Settings,
) -> None:
    """Test synthesize_video automatically slices pre-existing podcast MP3 and exports HyperFrames project."""
    podcast_dir = tmp_path / "output" / "media" / "podcast"
    video_dir = tmp_path / "output" / "media" / "video"
    podcast_dir.mkdir(parents=True, exist_ok=True)

    run_id = sample_editorial_pkg.run_id
    # Create a mock 24-second MP3 (1000 frames * 0.024s)
    frame = b"\xff\xf3\x64\xc4" + b"\x2a" * 140
    (podcast_dir / f"{run_id}.mp3").write_bytes(frame * 1000)
    (podcast_dir / f"{run_id}.json").write_text(
        json.dumps({
            "run_id": run_id,
            "segments": [
                {
                    "index": 0,
                    "speaker": "Minh",
                    "voice": "vi-VN-NamMinhNeural",
                    "start_sec": 0.0,
                    "end_sec": 8.4,
                    "duration_sec": 8.4,
                }
            ],
        }),
        encoding="utf-8",
    )

    res = synthesize_video(
        run_id=run_id,
        editorial_pkg=sample_editorial_pkg,
        settings=mock_settings,
        options=VideoMediaOptions(force=True, prefer_ffmpeg=False),
        output_media_dir=video_dir,
        assembler=PurePythonVideoAssembler(),
    )

    assert res.video_path is not None and res.video_path.exists()
    assert res.total_duration_sec < 60.0
    assert res.audio_source == "podcast-sliced"
    assert res.hyperframes_dir is not None and res.hyperframes_dir.exists()
    assert (res.hyperframes_dir / "index.html").exists()
    assert (res.hyperframes_dir / "tokens.css").exists()
    assert (res.hyperframes_dir / "compositions" / "scene_01.html").exists()
    assert (res.hyperframes_dir / "TRANSLATION_NOTES.md").exists()

    tokens_content = (res.hyperframes_dir / "tokens.css").read_text(encoding="utf-8")
    assert tokens_content.startswith("/* Hallmark · v2.0.0")
    assert "--color-paper:" in tokens_content


def test_split_scene_into_beats_and_illustration_cache(tmp_path: Path) -> None:
    """Test beat splitting at sentence boundary and manifest-cached AI illustration generation."""
    from io import BytesIO
    from PIL import Image
    from cyberpulse.media_illustrations import (
        ensure_beat_illustrations,
        split_scene_into_beats,
    )

    long_text = (
        "OpenSSH vừa phát hành bản sửa lỗi khẩn cấp phiên bản 10.6p2 để vá lỗ hổng nghiêm trọng. "
        "Lỗ hổng này cho phép kẻ tấn công vượt qua bước xác thực trên các máy chủ Alpine Linux."
    )
    beats = split_scene_into_beats(
        scene_no=1,
        segment_index=1,
        headline="OpenSSH phát hành bản vá khẩn cấp",
        text=long_text,
        duration_sec=15.5,
    )
    assert len(beats) == 2
    assert beats[0].word_start == 0
    assert beats[1].word_start == beats[0].word_end

    class _FakeProvider:
        name = "fake-illust"

        def __init__(self) -> None:
            self.calls = 0

        def generate(self, prompt: str, seed: int) -> bytes | None:
            self.calls += 1
            buf = BytesIO()
            Image.new("RGB", (600, 400), color=(210, 175, 130)).save(buf, format="JPEG")
            return buf.getvalue()

    provider = _FakeProvider()
    assets_dir = tmp_path / "run_assets"

    resolved1, rep1 = ensure_beat_illustrations(beats, assets_dir=assets_dir, providers=[provider])
    assert rep1.generated == 2
    assert rep1.reused == 0
    assert provider.calls == 2
    assert all(b.image_path is not None and b.image_path.exists() for b in resolved1)

    # Second call with identical text should reuse the manifest cache without calling provider
    resolved2, rep2 = ensure_beat_illustrations(beats, assets_dir=assets_dir, providers=[provider])
    assert rep2.generated == 0
    assert rep2.reused == 2
    assert provider.calls == 2
    assert [b.image_path for b in resolved2] == [b.image_path for b in resolved1]


def test_align_words_and_motion_renderer_karaoke(tmp_path: Path) -> None:
    """Test word-level alignment with pause anchors and frame-accurate karaoke highlight rendering."""
    from PIL import Image
    from cyberpulse.media_motion import (
        PlateBeat,
        SceneMotionRenderer,
        SceneMotionSpec,
        align_words,
    )

    text = "CISA vừa cảnh báo lỗ hổng CVE-2024-27834 trên hệ điều hành Apple. Hãy cập nhật ngay hôm nay."
    # Envelope with a clear pause around 3.2s - 3.5s
    env = [0.6 if not (320 <= i <= 350) else 0.01 for i in range(600)]
    words = align_words(text, duration=6.0, envelope=env)
    assert len(words) == len(text.split())
    assert words[0].start >= 0.0
    assert words[-1].end <= 6.0
    for a, b in zip(words, words[1:]):
        assert a.start <= a.end <= b.start + 1e-3

    img_path = tmp_path / "beat1.jpg"
    Image.new("RGB", (1200, 800), color=(205, 165, 120)).save(img_path, "JPEG")

    spec = SceneMotionSpec(
        scene_no=1,
        total_scenes=3,
        run_id="run-20261106T022500Z-3f403298",
        headline="CISA cảnh báo lỗ hổng trên hệ điều hành Apple",
        category_label="Hồ sơ lỗ hổng và bản vá khẩn",
        speaker="Lan",
        speaker_role="Biên tập viên chuyên mục",
        narration=text,
        duration_sec=6.0,
        words=words,
        beats=[PlateBeat(img_path, 0.0, 6.0, "Minh họa AI theo lời thoại")],
        envelope=env,
        start_sec=0.0,
    )
    renderer = SceneMotionRenderer(spec, width=540, height=960)
    f_early = renderer.render(0.4)
    f_mid = renderer.render(3.0)
    assert f_early.size == (540, 960)
    assert f_mid.size == (540, 960)
    assert f_early.tobytes() != f_mid.tobytes()


