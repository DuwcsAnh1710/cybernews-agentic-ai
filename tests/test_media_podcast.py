"""Unit tests for cyberpulse.media.podcast module."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import pytest

from cyberpulse.config import Settings
from cyberpulse.media.podcast import (
    AudioAssembler,
    EdgeTTSBackend,
    MediaDependencyError,
    MediaInputError,
    MediaSynthesisError,
    PodcastMediaOptions,
    PurePythonMP3Assembler,
    SRTCue,
    SegmentSynthesisResult,
    TTSBackend,
    build_metadata,
    build_segment_srt_cues,
    format_srt,
    parse_id3v2_length,
    parse_mp3_frame_header,
    prepare_tts_text,
    resolve_speaker_voices,
    split_text_into_cues,
    synthesize_podcast,
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
    """Ensure tests are strictly hermetic: no external socket connections allowed."""
    import socket

    orig_connect = socket.socket.connect

    def _no_connect(sock: socket.socket, address: Any, *args: Any, **kwargs: Any) -> None:
        host = address[0] if isinstance(address, tuple) and len(address) > 0 else str(address)
        if host in ("127.0.0.1", "localhost", "::1"):
            # Allow local loopback used by Windows asyncio proactor self-pipe
            return orig_connect(sock, address, *args, **kwargs)
        raise RuntimeError(f"Hermetic violation: external network connection attempted to {address}")

    monkeypatch.setattr("socket.socket.connect", _no_connect)


class FakeTTSBackend:
    """Deterministic in-memory TTS backend generating mock MP3 frames."""

    def __init__(self, segment_durations: list[float] | None = None, fail_counts: int = 0) -> None:
        self.durations = segment_durations or [3.0, 4.0]
        self.fail_counts = fail_counts
        self.call_count = 0

    async def synthesize_segment(self, text: str, voice: str, output_path: Path) -> float:
        self.call_count += 1
        if self.call_count <= self.fail_counts:
            raise ConnectionError("Simulated TTS network timeout")

        dur = self.durations[(self.call_count - 1 - self.fail_counts) % len(self.durations)]
        # Construct valid 24kHz 48kbps MPEG-2 L3 frames (144 bytes per 24ms frame)
        num_frames = max(1, round(dur / 0.024))
        frame = b"\xff\xf3\x64\xc4" + b"\x00" * 140
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(frame * num_frames)
        return num_frames * 0.024


@pytest.fixture
def sample_editorial_pkg() -> EditorialPackage:
    """Create a minimal valid EditorialPackage for testing."""
    return EditorialPackage(
        run_id="test-run-20261102T100000Z",
        language="vi",
        digest=Digest(
            title="Điểm tin",
            intro="Tóm tắt an ninh mạng hôm nay.",
            sections=[
                DigestSection(
                    heading="Lỗ hổng",
                    body=CitedText(
                        text="Chi tiết lỗ hổng CVE được công bố.",
                        cited_article_ids=["art-1"],
                    ),
                )
            ],
        ),
        podcast=PodcastScript(
            title="Điểm tin an ninh mạng",
            segments=[
                PodcastSegment(
                    speaker="Minh",
                    line=CitedText(
                        text="Chào Lan và quý vị thính giả. Hôm nay chúng ta có tin nóng về lỗ hổng CVE.",
                        cited_article_ids=["art-1"],
                    ),
                ),
                PodcastSegment(
                    speaker="Lan",
                    line=CitedText(
                        text="Cảm ơn Minh. Các chuyên gia khuyến cáo cập nhật ngay bản vá mới nhất.",
                        cited_article_ids=["art-1"],
                    ),
                ),
            ],
        ),
        video=VideoScript(
            title="Video Bản tin",
            scenes=[
                VideoScene(
                    scene_no=1,
                    on_screen_text="Cảnh báo CVE",
                    narration=CitedText(
                        text="Cảnh báo an ninh mạng khẩn cấp.",
                        cited_article_ids=["art-1"],
                    ),
                    duration_sec=10.0,
                )
            ],
        ),
        captions=[
            Caption(
                platform="facebook",
                text="Tin nóng hôm nay về an ninh mạng.",
                hashtags=["#cyberpulse"],
                cited_article_ids=["art-1"],
            )
        ],
    )


# ==============================================================================
# 1. Voice Resolution & Overrides
# ==============================================================================


def test_resolve_speaker_voices_default() -> None:
    speakers = ["Minh", "Lan"]
    configured = ["Minh", "Lan"]
    voices = resolve_speaker_voices(speakers, configured)
    assert voices["Minh"] == "vi-VN-NamMinhNeural"
    assert voices["Lan"] == "vi-VN-HoaiMyNeural"


def test_resolve_speaker_voices_cli_override() -> None:
    speakers = ["Minh", "Lan"]
    configured = ["Minh", "Lan"]
    overrides = {"Minh": "vi-VN-HoaiMyNeural"}
    voices = resolve_speaker_voices(speakers, configured, overrides)
    assert voices["Minh"] == "vi-VN-HoaiMyNeural"
    assert voices["Lan"] == "vi-VN-HoaiMyNeural"


def test_resolve_speaker_voices_unknown_speaker_raises() -> None:
    speakers = ["Minh", "Nam"]
    configured = ["Minh", "Lan"]
    with pytest.raises(MediaInputError, match="Unknown speaker 'Nam'"):
        resolve_speaker_voices(speakers, configured)


def test_resolve_speaker_voices_invalid_voice_format_raises() -> None:
    speakers = ["Minh"]
    configured = ["Minh", "Lan"]
    with pytest.raises(MediaInputError, match="Invalid voice format"):
        resolve_speaker_voices(speakers, configured, {"Minh": "InvalidVoiceName"})


# ==============================================================================
# 2. Text Preparation & SRT Splitting
# ==============================================================================


def test_prepare_tts_text_preserves_content() -> None:
    raw = "  Lỗ hổng   CVE-2026-12345   nguy hiểm   cấp 9.8!   "
    clean = prepare_tts_text(raw)
    assert clean == "Lỗ hổng CVE-2026-12345 nguy hiểm cấp 9.8!"


def test_split_text_into_cues_respects_max_chars() -> None:
    text = (
        "Đây là một câu rất dài nhằm kiểm tra thuật toán chia nhỏ phụ đề thành nhiều đoạn. "
        "Mỗi đoạn không được vượt quá số ký tự quy định và phải ngắt đúng từ ngữ tự nhiên."
    )
    cues = split_text_into_cues(text, max_chars_per_line=40, max_lines_per_cue=2)
    assert len(cues) >= 2
    for cue in cues:
        assert len(cue) <= 80
        # Ensure no trailing or leading whitespace
        assert cue == cue.strip()


def test_build_segment_srt_cues_monotonic_and_bounds() -> None:
    text = "Tin tức số 1. Tin tức số 2. Tin tức số 3."
    cues, next_counter = build_segment_srt_cues(
        segment_index=0,
        speaker="Minh",
        text=text,
        start_sec=10.0,
        duration_sec=6.0,
        cue_counter_start=1,
    )
    assert len(cues) >= 1
    assert next_counter == len(cues) + 1
    assert cues[0].text.startswith("[Minh]")

    prev_end = 10000
    for cue in cues:
        assert cue.start_ms >= prev_end
        assert cue.end_ms > cue.start_ms
        prev_end = cue.end_ms

    assert cues[-1].end_ms <= 16000


def test_srt_speaker_marker_first_cue_only() -> None:
    text = "Câu thứ nhất rất dài để tạo ra cue một. Câu thứ hai tiếp tục rất dài để tạo ra cue hai."
    cues, _ = build_segment_srt_cues(
        segment_index=0,
        speaker="Lan",
        text=text,
        start_sec=0.0,
        duration_sec=5.0,
        cue_counter_start=1,
    )
    assert len(cues) >= 2
    assert cues[0].text.startswith("[Lan]")
    for cue in cues[1:]:
        assert not cue.text.startswith("[Lan]")


def test_format_srt_utf8_no_bom() -> None:
    cues = [
        SRTCue(index=1, start_ms=0, end_ms=2500, text="[Minh] Chào quý vị."),
        SRTCue(index=2, start_ms=2500, end_ms=5000, text="Hôm nay có tin nóng."),
    ]
    raw_srt = format_srt(cues)
    assert "1\n00:00:00,000 --> 00:00:02,500\n[Minh] Chào quý vị." in raw_srt
    assert "2\n00:00:02,500 --> 00:00:05,000\nHôm nay có tin nóng." in raw_srt
    encoded = raw_srt.encode("utf-8")
    assert not encoded.startswith(b"\xef\xbb\xbf")  # No BOM


# ==============================================================================
# 3. MP3 Parsing & Assembler
# ==============================================================================


def test_pure_python_mp3_assembler_header_and_duration(tmp_path: Path) -> None:
    assembler = PurePythonMP3Assembler()
    # Create 100 frames of 24ms each = 2.4 seconds
    frame = b"\xff\xf3\x64\xc4" + b"\x00" * 140
    test_mp3 = tmp_path / "test.mp3"
    test_mp3.write_bytes(frame * 100)

    duration = assembler.measure_duration(test_mp3)
    assert round(duration, 3) == 2.400


def test_pure_python_mp3_assembler_concat_with_pauses(tmp_path: Path) -> None:
    assembler = PurePythonMP3Assembler()
    frame = b"\xff\xf3\x64\xc4" + b"\x00" * 140

    seg1_path = tmp_path / "seg1.mp3"
    seg1_path.write_bytes(frame * 50)  # 1.2s

    seg2_path = tmp_path / "seg2.mp3"
    seg2_path.write_bytes(frame * 50)  # 1.2s

    segs = [
        SegmentSynthesisResult(
            index=0,
            speaker="Minh",
            voice="vi-VN-NamMinhNeural",
            char_count=10,
            word_count=2,
            text="Minh text",
            audio_path=seg1_path,
            duration_sec=1.2,
        ),
        SegmentSynthesisResult(
            index=1,
            speaker="Lan",
            voice="vi-VN-HoaiMyNeural",
            char_count=10,
            word_count=2,
            text="Lan text",
            audio_path=seg2_path,
            duration_sec=1.2,
        ),
    ]

    out_mp3 = tmp_path / "assembled.mp3"
    total_dur = assembler.assemble(
        segments=segs,
        output_path=out_mp3,
        pause_diff_speaker_ms=350,
        pause_same_speaker_ms=200,
    )

    # 1.2s + 0.360s pause (15 frames * 0.024) + 1.2s = 2.76s
    assert round(total_dur, 2) >= 2.70
    assert out_mp3.exists()


# ==============================================================================
# 4. End-to-End synthesize_podcast Orchestration
# ==============================================================================


def _make_test_settings() -> Settings:
    return Settings(
        project_name="CyberPulse AI",
        topics=["cybersecurity"],
        editorial={"podcast_speakers": ["Minh", "Lan"]},
    )


def test_synthesize_podcast_happy_path(
    sample_editorial_pkg: EditorialPackage,
    tmp_path: Path,
) -> None:
    options = PodcastMediaOptions()
    fake_backend = FakeTTSBackend(segment_durations=[2.4, 3.6])
    assembler = PurePythonMP3Assembler()
    fixed_time = datetime(2026, 11, 2, 10, 0, 0, tzinfo=timezone.utc)

    # Construct mock Settings
    settings = _make_test_settings()
    out_dir = tmp_path / "output" / "media" / "podcast"

    result = synthesize_podcast(
        run_id="test-run-20261102T100000Z",
        editorial_pkg=sample_editorial_pkg,
        settings=settings,
        options=options,
        output_media_dir=out_dir,
        tts_backend=fake_backend,
        assembler=assembler,
        clock_fn=lambda: fixed_time,
    )

    assert result.run_id == "test-run-20261102T100000Z"
    assert result.audio_path.exists()
    assert result.srt_path.exists()
    assert result.meta_path.exists()
    assert result.segment_count == 2
    assert result.total_duration_sec > 6.0

    meta_json = json.loads(result.meta_path.read_text(encoding="utf-8"))
    assert meta_json["run_id"] == "test-run-20261102T100000Z"
    assert meta_json["voices"]["Minh"] == "vi-VN-NamMinhNeural"
    assert meta_json["voices"]["Lan"] == "vi-VN-HoaiMyNeural"
    assert len(meta_json["segments"]) == 2


def test_synthesize_podcast_retry_transient_failure(
    sample_editorial_pkg: EditorialPackage,
    tmp_path: Path,
) -> None:
    options = PodcastMediaOptions()
    # Fails 2 times, succeeds on 3rd attempt
    fake_backend = FakeTTSBackend(segment_durations=[2.4], fail_counts=2)
    sleeps: list[float] = []
    settings = _make_test_settings()

    result = synthesize_podcast(
        run_id="test-retry-run",
        editorial_pkg=sample_editorial_pkg,
        settings=settings,
        options=options,
        output_media_dir=tmp_path / "out",
        tts_backend=fake_backend,
        sleep_fn=lambda s: sleeps.append(s),
    )
    assert result.segment_count == 2
    assert sleeps == [1.0, 2.0]


def test_synthesize_podcast_exhausted_retries_raises(
    sample_editorial_pkg: EditorialPackage,
    tmp_path: Path,
) -> None:
    options = PodcastMediaOptions()
    # Fails 3 times (exceeds max_attempts)
    fake_backend = FakeTTSBackend(segment_durations=[2.4], fail_counts=3)
    settings = _make_test_settings()
    out_dir = tmp_path / "out"

    with pytest.raises(MediaSynthesisError, match="after 3 attempts"):
        synthesize_podcast(
            run_id="test-fail-run",
            editorial_pkg=sample_editorial_pkg,
            settings=settings,
            options=options,
            output_media_dir=out_dir,
            tts_backend=fake_backend,
            sleep_fn=lambda s: None,
        )

    # Ensure no partial final files exist
    assert not (out_dir / "test-fail-run.mp3").exists()
    assert not (out_dir / "test-fail-run.srt").exists()
    assert not (out_dir / "test-fail-run.json").exists()


def test_synthesize_podcast_refuses_overwrite_without_force(
    sample_editorial_pkg: EditorialPackage,
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True)
    existing_file = out_dir / "test-run-existing.mp3"
    existing_file.write_text("existing")

    settings = _make_test_settings()
    options = PodcastMediaOptions(force=False)

    with pytest.raises(MediaInputError, match="Output files already exist"):
        synthesize_podcast(
            run_id="test-run-existing",
            editorial_pkg=sample_editorial_pkg,
            settings=settings,
            options=options,
            output_media_dir=out_dir,
            tts_backend=FakeTTSBackend(),
        )


def test_lazy_import_edge_tts_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulating edge_tts being uninstalled
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None if name == "edge_tts" else True)
    # EdgeTTSBackend raises MediaDependencyError with exit code 12
    with pytest.raises(MediaDependencyError) as exc_info:
        EdgeTTSBackend()
    assert exc_info.value.exit_code == 12
    assert "pip install .[media]" in str(exc_info.value)


@pytest.mark.anyio
async def test_edge_tts_synthesize_segment_timeout_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC5: EdgeTTSBackend times out after 25s (or explicit timeout) if socket stalls."""
    import edge_tts

    out_file = tmp_path / "timeout_test.mp3"

    class SlowCommunicate:
        def __init__(self, text: str, voice: str) -> None:
            pass

        async def save(self, path: str) -> None:
            # Simulate a stalled websocket
            await asyncio.sleep(2.0)

    monkeypatch.setattr(edge_tts, "Communicate", SlowCommunicate)

    backend = EdgeTTSBackend()
    with pytest.raises((asyncio.TimeoutError, TimeoutError)):
        await backend.synthesize_segment("Đoạn thử nghiệm timeout", "vi-VN-NamMinhNeural", out_file, timeout=0.02)


def test_synthesize_podcast_retries_on_timeout_error(
    sample_editorial_pkg: EditorialPackage,
    tmp_path: Path,
) -> None:
    """AC5: Stalled socket timeout triggers retry loop with backoff."""
    out_dir = tmp_path / "media_timeout"
    settings = _make_test_settings()
    options = PodcastMediaOptions()
    sleeps: list[float] = []

    class TimeoutTTSBackend:
        def __init__(self) -> None:
            self.calls = 0

        async def synthesize_segment(self, text: str, voice: str, output_path: Path) -> float:
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("WebSocket stalled on first attempt")
            # Create valid MP3 frame
            frame = b"\xff\xf3\x64\xc4" + b"\x00" * 140
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(frame * 100)
            return 2.4

    backend = TimeoutTTSBackend()
    result = synthesize_podcast(
        run_id="test-timeout-retry",
        editorial_pkg=sample_editorial_pkg,
        settings=settings,
        options=options,
        output_media_dir=out_dir,
        tts_backend=backend,
        sleep_fn=lambda s: sleeps.append(s),
    )

    assert result.audio_path.exists()
    assert sleeps == [1.0]  # First retry backoff
    assert backend.calls == 3  # 1st seg attempt 1 (failed), attempt 2 (succeeded), 2nd seg attempt 1 (succeeded)


def test_synthesize_podcast_progress_callback_and_stdout(
    sample_editorial_pkg: EditorialPackage,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC4: Real-time progress callback and live segment logging to stdout."""
    out_dir = tmp_path / "media_progress"
    settings = _make_test_settings()
    options = PodcastMediaOptions()
    cb_calls: list[tuple[int, int, str, str, float]] = []

    fake_backend = FakeTTSBackend(segment_durations=[3.0, 4.0])

    def _on_progress(current: int, total: int, speaker: str, voice: str, duration: float) -> None:
        cb_calls.append((current, total, speaker, voice, duration))

    result = synthesize_podcast(
        run_id="test-progress-run",
        editorial_pkg=sample_editorial_pkg,
        settings=settings,
        options=options,
        output_media_dir=out_dir,
        tts_backend=fake_backend,
        progress_callback=_on_progress,
    )

    assert result.audio_path.exists()
    assert len(cb_calls) == 2
    # Verify tuple: (current, total, speaker, voice, duration)
    assert cb_calls[0][0] == 1
    assert cb_calls[0][1] == 2
    assert cb_calls[0][2] == "Minh"
    assert cb_calls[0][3] == "vi-VN-NamMinhNeural"
    assert cb_calls[0][4] > 0.0

    assert cb_calls[1][0] == 2
    assert cb_calls[1][1] == 2
    assert cb_calls[1][2] == "Lan"
    assert cb_calls[1][3] == "vi-VN-HoaiMyNeural"
    assert cb_calls[1][4] > 0.0

    captured = capsys.readouterr()
    assert "[1/2] Minh (vi-VN-NamMinhNeural):" in captured.out
    assert "[2/2] Lan (vi-VN-HoaiMyNeural):" in captured.out
    assert "-> Xong trong" in captured.out
    assert "dur=" in captured.out
