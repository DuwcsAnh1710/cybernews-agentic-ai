"""CyberPulse Podcast Media Synthesis Module.

Synthesizes multi-speaker podcast audio via Edge-TTS, synchronizes
subtitles into SRT format, and generates technical metadata.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Protocol

from cyberpulse.config import Settings
from cyberpulse.models import EditorialPackage

logger = logging.getLogger("cyberpulse.media.podcast")

# Supported Edge-TTS voice name pattern: e.g. vi-VN-NamMinhNeural, vi-VN-HoaiMyNeural
VOICE_NAME_REGEX = re.compile(r"^[a-z]{2,3}-[A-Z]{2,3}-[A-Za-z0-9]+Neural$")
RUN_ID_REGEX = re.compile(r"^[A-Za-z0-9._-]+$")


# ==============================================================================
# Exceptions Mapped to Exit Codes
# ==============================================================================


class MediaError(Exception):
    """Base exception for media synthesis."""

    exit_code: int = 11


class MediaDependencyError(MediaError):
    """Missing external dependency (edge-tts or ffmpeg/ffprobe)."""

    exit_code: int = 12


class MediaSynthesisError(MediaError):
    """TTS generation or audio assembly failure."""

    exit_code: int = 13


class MediaInputError(MediaError):
    """Invalid input, unknown speaker, bad run_id, or output collision."""

    exit_code: int = 7


class MediaValidationError(MediaError):
    """Validation report missing or failed without --allow-unvalidated."""

    exit_code: int = 8


# ==============================================================================
# Dataclasses
# ==============================================================================


@dataclass(frozen=True)
class PodcastMediaOptions:
    """Options for podcast media synthesis."""

    voice_overrides: dict[str, str] = field(default_factory=dict)
    pause_diff_speaker_ms: int = 350
    pause_same_speaker_ms: int = 200
    force: bool = False
    allow_unvalidated: bool = False


@dataclass(frozen=True)
class SegmentSynthesisResult:
    """Result of synthesizing a single podcast segment."""

    index: int
    speaker: str
    voice: str
    char_count: int
    word_count: int
    text: str
    audio_path: Path
    duration_sec: float


@dataclass(frozen=True)
class SRTCue:
    """A single subtitle cue."""

    index: int
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class PodcastMediaResult:
    """Result of full podcast media synthesis."""

    run_id: str
    audio_path: Path
    srt_path: Path
    meta_path: Path
    total_duration_sec: float
    segment_count: int
    voices: dict[str, str]


# ==============================================================================
# Protocols
# ==============================================================================


class TTSBackend(Protocol):
    """Protocol for text-to-speech synthesis backends."""

    async def synthesize_segment(
        self,
        text: str,
        voice: str,
        output_path: Path,
    ) -> float:
        """Synthesize text to audio file. Return measured duration in seconds."""
        ...


class AudioAssembler(Protocol):
    """Protocol for audio assembly and duration measurement."""

    def assemble(
        self,
        segments: list[SegmentSynthesisResult],
        output_path: Path,
        pause_diff_speaker_ms: int,
        pause_same_speaker_ms: int,
    ) -> float:
        """Assemble audio segments into single MP3 with pauses. Return measured duration in seconds."""
        ...

    def measure_duration(self, audio_path: Path) -> float:
        """Measure audio duration in seconds."""
        ...


# ==============================================================================
# Pure-Python MP3 Frame Parser & Assembler
# ==============================================================================

MP3_BITRATES = {
    # (version_id, layer_id): list of bitrates in kbps for index 1..14
    (3, 1): [32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],  # MPEG-1 Layer 3
    (2, 1): [8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],  # MPEG-2 Layer 3
    (0, 1): [8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],  # MPEG-2.5 Layer 3
}

MP3_SAMPLE_RATES = {
    3: [44100, 48000, 32000],  # MPEG-1
    2: [22050, 24000, 16000],  # MPEG-2
    0: [11025, 12000, 8000],  # MPEG-2.5
}


def parse_id3v2_length(data: bytes) -> int:
    """Return total bytes of ID3v2 tag at the start of data, or 0 if none."""
    if len(data) >= 10 and data[:3] == b"ID3":
        size = (
            ((data[6] & 0x7F) << 21)
            | ((data[7] & 0x7F) << 14)
            | ((data[8] & 0x7F) << 7)
            | (data[9] & 0x7F)
        )
        footer = 10 if (data[5] & 0x10) else 0
        return 10 + size + footer
    return 0


def parse_mp3_frame_header(data: bytes, offset: int = 0) -> dict[str, Any] | None:
    """Parse MPEG audio frame header at offset."""
    if len(data) < offset + 4:
        return None
    b0, b1, b2, b3 = data[offset : offset + 4]
    if b0 != 0xFF or (b1 & 0xE0) != 0xE0:
        return None

    version_id = (b1 >> 3) & 0x03
    layer_id = (b1 >> 1) & 0x03
    if version_id == 1 or layer_id == 0:  # Reserved
        return None

    bitrate_idx = (b2 >> 4) & 0x0F
    sample_rate_idx = (b2 >> 2) & 0x03
    padding = (b2 >> 1) & 0x01
    channel_mode = (b3 >> 6) & 0x03

    if bitrate_idx == 0 or bitrate_idx == 15 or sample_rate_idx == 3:
        return None

    rates = MP3_SAMPLE_RATES.get(version_id)
    if not rates or sample_rate_idx >= len(rates):
        return None
    sample_rate = rates[sample_rate_idx]

    # Layer 3
    if layer_id == 1:
        bitrate_list = MP3_BITRATES.get((version_id, layer_id))
        if not bitrate_list or bitrate_idx > len(bitrate_list):
            return None
        bitrate_kbps = bitrate_list[bitrate_idx - 1]
        samples_per_frame = 1152 if version_id == 3 else 576
        coef = 144 if version_id == 3 else 72
        frame_len = (coef * bitrate_kbps * 1000) // sample_rate + padding
        duration = samples_per_frame / sample_rate
        return {
            "version_id": version_id,
            "layer_id": layer_id,
            "bitrate_kbps": bitrate_kbps,
            "sample_rate": sample_rate,
            "padding": padding,
            "samples_per_frame": samples_per_frame,
            "frame_len": frame_len,
            "duration": duration,
            "channel_mode": channel_mode,
            "header_bytes": data[offset : offset + 4],
        }

    return None


class PurePythonMP3Assembler:
    """Pure-Python MP3 duration measurement, silent frame synthesis, and concatenation."""

    def measure_duration(self, audio_path: Path) -> float:
        """Measure MP3 duration by scanning all valid frames or Xing header."""
        if not audio_path.exists():
            raise MediaSynthesisError(f"Audio file not found: '{audio_path}'")

        data = audio_path.read_bytes()
        if not data:
            return 0.0

        id3_len = parse_id3v2_length(data)
        offset = id3_len

        # Strip ID3v1 tag at end if present
        end_offset = len(data)
        if end_offset >= 128 and data[end_offset - 128 : end_offset - 125] == b"TAG":
            end_offset -= 128

        total_duration = 0.0
        first_frame = True

        while offset < end_offset:
            # Sync to next frame
            while offset < end_offset - 3 and not (
                data[offset] == 0xFF and (data[offset + 1] & 0xE0) == 0xE0
            ):
                offset += 1

            if offset >= end_offset - 3:
                break

            header = parse_mp3_frame_header(data, offset)
            if not header:
                offset += 1
                continue

            frame_len = header["frame_len"]
            if frame_len <= 0 or offset + frame_len > len(data):
                offset += 1
                continue

            # Check Xing/Info header in first frame
            if first_frame:
                first_frame = False
                side_info_len = (
                    (17 if header["channel_mode"] == 3 else 32)
                    if header["version_id"] == 3
                    else (9 if header["channel_mode"] == 3 else 17)
                )
                xing_offset = offset + 4 + side_info_len
                if xing_offset + 8 <= len(data):
                    tag = data[xing_offset : xing_offset + 4]
                    if tag in (b"Xing", b"Info"):
                        flags = int.from_bytes(data[xing_offset + 4 : xing_offset + 8], "big")
                        if (flags & 0x01) and xing_offset + 12 <= len(data):
                            num_frames = int.from_bytes(
                                data[xing_offset + 8 : xing_offset + 12], "big"
                            )
                            if num_frames > 0:
                                return num_frames * header["duration"]

            total_duration += header["duration"]
            offset += frame_len

        return total_duration

    def _extract_audio_frames(self, data: bytes) -> tuple[bytes, dict[str, Any] | None]:
        """Strip ID3v2 and ID3v1 tags, returning raw audio frames and first frame info."""
        if not data:
            return b"", None

        id3_len = parse_id3v2_length(data)
        offset = id3_len

        end_offset = len(data)
        if end_offset >= 128 and data[end_offset - 128 : end_offset - 125] == b"TAG":
            end_offset -= 128

        # Find first valid frame
        first_info = None
        while offset < end_offset - 3:
            if data[offset] == 0xFF and (data[offset + 1] & 0xE0) == 0xE0:
                header = parse_mp3_frame_header(data, offset)
                if header:
                    first_info = header
                    break
            offset += 1

        if not first_info:
            return data[id3_len:end_offset], None

        return data[offset:end_offset], first_info

    def _create_silent_frames(self, template_header: dict[str, Any], duration_sec: float) -> bytes:
        """Create silent MP3 frames matching the template header."""
        frame_duration = template_header["duration"]
        num_frames = max(1, round(duration_sec / frame_duration))

        # Standard unpadded frame header
        b0, b1, b2, b3 = template_header["header_bytes"]
        b2_unpadded = b2 & 0xFD  # clear padding bit
        unpadded_header = bytes([b0, b1, b2_unpadded, b3])

        unpadded_info = parse_mp3_frame_header(unpadded_header, 0)
        frame_len = unpadded_info["frame_len"] if unpadded_info else template_header["frame_len"]

        # In MPEG Layer 3, zeroes in side info & main data represent zero audio energy (silence)
        silent_frame = unpadded_header + b"\x00" * (frame_len - 4)
        return silent_frame * num_frames

    def assemble(
        self,
        segments: list[SegmentSynthesisResult],
        output_path: Path,
        pause_diff_speaker_ms: int,
        pause_same_speaker_ms: int,
    ) -> float:
        """Assemble audio segments into one MP3 with silent pause frames."""
        if not segments:
            output_path.write_bytes(b"")
            return 0.0

        output_path.parent.mkdir(parents=True, exist_ok=True)
        chunks: list[bytes] = []
        last_frame_info: dict[str, Any] | None = None

        for i, seg in enumerate(segments):
            seg_bytes = seg.audio_path.read_bytes()
            frames, info = self._extract_audio_frames(seg_bytes)
            if info:
                last_frame_info = info

            # Add pause before this segment if not the first segment
            if i > 0 and last_frame_info:
                prev_seg = segments[i - 1]
                pause_ms = (
                    pause_diff_speaker_ms
                    if seg.speaker != prev_seg.speaker
                    else pause_same_speaker_ms
                )
                if pause_ms > 0:
                    pause_sec = pause_ms / 1000.0
                    silence = self._create_silent_frames(last_frame_info, pause_sec)
                    chunks.append(silence)

            chunks.append(frames)

        assembled_data = b"".join(chunks)
        output_path.write_bytes(assembled_data)

        # Measure assembled duration
        duration = self.measure_duration(output_path)
        return duration


# ==============================================================================
# FFmpeg Audio Assembler (Alternative when FFmpeg is on PATH)
# ==============================================================================


class FFmpegAssembler:
    """FFmpeg and ffprobe-based audio assembler and duration measurement."""

    def __init__(self) -> None:
        self.ffmpeg_bin = shutil.which("ffmpeg")
        self.ffprobe_bin = shutil.which("ffprobe")
        if not self.ffmpeg_bin or not self.ffprobe_bin:
            raise MediaDependencyError(
                "ffmpeg and ffprobe must be installed on PATH to use FFmpegAssembler."
            )

    def measure_duration(self, audio_path: Path) -> float:
        """Measure audio duration via ffprobe."""
        if not audio_path.exists():
            raise MediaSynthesisError(f"Audio file not found: '{audio_path}'")

        cmd = [
            str(self.ffprobe_bin),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return float(res.stdout.strip())
        except Exception as exc:
            raise MediaSynthesisError(f"Failed to measure duration with ffprobe: {exc}") from exc

    def assemble(
        self,
        segments: list[SegmentSynthesisResult],
        output_path: Path,
        pause_diff_speaker_ms: int,
        pause_same_speaker_ms: int,
    ) -> float:
        """Assemble audio segments into one MP3 with pauses via FFmpeg."""
        if not segments:
            output_path.write_bytes(b"")
            return 0.0

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="cyberpulse_ffmpeg_") as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            concat_list = tmp_dir / "concat.txt"
            lines: list[str] = []

            for i, seg in enumerate(segments):
                if i > 0:
                    prev_seg = segments[i - 1]
                    pause_ms = (
                        pause_diff_speaker_ms
                        if seg.speaker != prev_seg.speaker
                        else pause_same_speaker_ms
                    )
                    if pause_ms > 0:
                        pause_sec = pause_ms / 1000.0
                        pause_file = tmp_dir / f"pause_{i}.mp3"
                        cmd_silence = [
                            str(self.ffmpeg_bin),
                            "-y",
                            "-f",
                            "lavfi",
                            "-i",
                            "anullsrc=r=24000:cl=mono",
                            "-t",
                            f"{pause_sec:.3f}",
                            "-c:a",
                            "libmp3lame",
                            "-b:a",
                            "48k",
                            str(pause_file),
                        ]
                        subprocess.run(cmd_silence, capture_output=True, check=True)
                        lines.append(f"file '{pause_file.resolve().as_posix()}'")

                lines.append(f"file '{seg.audio_path.resolve().as_posix()}'")

            concat_list.write_text("\n".join(lines), encoding="utf-8")

            cmd_concat = [
                str(self.ffmpeg_bin),
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                "-c",
                "copy",
                str(output_path),
            ]
            try:
                subprocess.run(cmd_concat, capture_output=True, check=True)
            except subprocess.CalledProcessError:
                # Fallback to re-encoding filter concat if stream copy fails
                inputs: list[str] = []
                filter_parts: list[str] = []
                idx = 0
                for line in lines:
                    fpath = line.replace("file '", "").rstrip("'")
                    inputs.extend(["-i", fpath])
                    filter_parts.append(f"[{idx}:a]")
                    idx += 1
                filter_complex = f"{''.join(filter_parts)}concat=n={idx}:v=0:a=1[outa]"
                cmd_reencode = [
                    str(self.ffmpeg_bin),
                    "-y",
                    *inputs,
                    "-filter_complex",
                    filter_complex,
                    "-map",
                    "[outa]",
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "48k",
                    str(output_path),
                ]
                subprocess.run(cmd_reencode, capture_output=True, check=True)

        return self.measure_duration(output_path)


def get_default_assembler(prefer_ffmpeg: bool = False) -> AudioAssembler:
    """Return default assembler (PurePythonMP3Assembler by default, FFmpegAssembler if preferred & available)."""
    if prefer_ffmpeg and shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return FFmpegAssembler()
    return PurePythonMP3Assembler()


# ==============================================================================
# Edge-TTS Backend
# ==============================================================================


class EdgeTTSBackend:
    """Edge-TTS synthesizer with lazy import and duration measurement."""

    def __init__(self, assembler: AudioAssembler | None = None) -> None:
        self.assembler = assembler or PurePythonMP3Assembler()
        # Verify edge_tts importability lazily
        try:
            import edge_tts  # noqa: F401
        except ImportError as exc:
            raise MediaDependencyError(
                "edge-tts is required for podcast audio synthesis. "
                "Install with: pip install .[media]"
            ) from exc

    async def synthesize_segment(
        self,
        text: str,
        voice: str,
        output_path: Path,
    ) -> float:
        """Synthesize text using edge-tts. Return measured duration in seconds."""
        import edge_tts

        output_path.parent.mkdir(parents=True, exist_ok=True)
        communicate = edge_tts.Communicate(text=text, voice=voice)
        await communicate.save(str(output_path))

        duration = self.assembler.measure_duration(output_path)
        return duration


# ==============================================================================
# Pure Helper Functions
# ==============================================================================


def resolve_speaker_voices(
    speakers_in_script: list[str],
    configured_speakers: list[str],
    cli_overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    """Map speaker names to Vietnamese TTS voices.

    Defaults:
    - 1st configured speaker -> vi-VN-NamMinhNeural
    - 2nd configured speaker -> vi-VN-HoaiMyNeural
    Overrides from CLI take highest precedence.
    Unknown speakers raise MediaInputError (exit 7).
    """
    default_voices = ["vi-VN-NamMinhNeural", "vi-VN-HoaiMyNeural"]
    voice_map: dict[str, str] = {}

    for idx, spk in enumerate(configured_speakers):
        voice_map[spk] = default_voices[idx % len(default_voices)]

    if cli_overrides:
        for spk, voice in cli_overrides.items():
            if not VOICE_NAME_REGEX.match(voice):
                raise MediaInputError(
                    f"Invalid voice format '{voice}'. Expected pattern: 'xx-XX-NameNeural'."
                )
            voice_map[spk] = voice

    for spk in speakers_in_script:
        if spk not in voice_map:
            raise MediaInputError(
                f"Unknown speaker '{spk}' in podcast script. Configured speakers: {configured_speakers}."
            )

    return voice_map


def prepare_tts_text(text: str) -> str:
    """Normalize whitespace without altering words, numbers, or diacritics."""
    return " ".join(text.strip().split())


def split_text_into_cues(
    text: str,
    max_chars_per_line: int = 42,
    max_lines_per_cue: int = 2,
) -> list[str]:
    """Split segment text into subtitle cues at natural linguistic boundaries.

    Hierarchy:
    1. Sentence boundaries (. ! ?)
    2. Clause boundaries (, ; : -)
    3. Word boundaries
    Never splits mid-word.
    """
    clean_text = prepare_tts_text(text)
    if not clean_text:
        return []

    max_cue_chars = max_chars_per_line * max_lines_per_cue

    if len(clean_text) <= max_cue_chars:
        return [clean_text]

    # Split into sentence candidates
    sentence_delimiters = re.compile(r"(?<=[.!?])\s+")
    sentences = sentence_delimiters.split(clean_text)

    raw_chunks: list[str] = []
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if len(s) <= max_cue_chars:
            raw_chunks.append(s)
        else:
            # Split sentence by clause boundaries
            clause_delimiters = re.compile(r"(?<=[,;:])\s+|(?<=\s-\s)")
            clauses = clause_delimiters.split(s)
            for c in clauses:
                c = c.strip()
                if not c:
                    continue
                if len(c) <= max_cue_chars:
                    raw_chunks.append(c)
                else:
                    # Split clause by words
                    words = c.split()
                    curr: list[str] = []
                    curr_len = 0
                    for w in words:
                        w_len = len(w) + (1 if curr else 0)
                        if curr_len + w_len <= max_cue_chars:
                            curr.append(w)
                            curr_len += w_len
                        else:
                            if curr:
                                raw_chunks.append(" ".join(curr))
                            curr = [w]
                            curr_len = len(w)
                    if curr:
                        raw_chunks.append(" ".join(curr))

    # Merge small chunks up to max_cue_chars
    cues: list[str] = []
    buf: list[str] = []
    buf_len = 0

    for chunk in raw_chunks:
        add_len = len(chunk) + (1 if buf else 0)
        if buf_len + add_len <= max_cue_chars:
            buf.append(chunk)
            buf_len += add_len
        else:
            if buf:
                cues.append(" ".join(buf))
            buf = [chunk]
            buf_len = len(chunk)

    if buf:
        cues.append(" ".join(buf))

    return cues or [clean_text]


def format_timestamp(ms: int) -> str:
    """Format milliseconds into SRT timestamp: HH:MM:SS,mmm."""
    if ms < 0:
        ms = 0
    total_seconds, milliseconds = divmod(ms, 1000)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def build_segment_srt_cues(
    segment_index: int,
    speaker: str,
    text: str,
    start_sec: float,
    duration_sec: float,
    cue_counter_start: int,
) -> tuple[list[SRTCue], int]:
    """Build monotonic, non-overlapping SRT cues for a segment.

    Cues are distributed proportionally to character count across the measured duration.
    Speaker marker [Speaker] is placed on the first cue of the segment.
    """
    cue_texts = split_text_into_cues(text)
    if not cue_texts:
        return [], cue_counter_start

    total_chars = sum(len(c) for c in cue_texts)
    start_ms = round(start_sec * 1000)
    total_dur_ms = max(round(duration_sec * 1000), 100)
    end_bound_ms = start_ms + total_dur_ms

    cues: list[SRTCue] = []
    curr_start_ms = start_ms
    counter = cue_counter_start

    for i, c_text in enumerate(cue_texts):
        # Attach speaker marker on first cue
        display_text = f"[{speaker}] {c_text}" if i == 0 else c_text

        # Proportionate duration
        ratio = len(c_text) / total_chars if total_chars > 0 else (1.0 / len(cue_texts))
        dur_ms = round(total_dur_ms * ratio)

        if i == len(cue_texts) - 1:
            curr_end_ms = end_bound_ms
        else:
            curr_end_ms = min(curr_start_ms + max(dur_ms, 50), end_bound_ms - (len(cue_texts) - 1 - i) * 50)

        if curr_end_ms <= curr_start_ms:
            curr_end_ms = curr_start_ms + 50

        cues.append(
            SRTCue(
                index=counter,
                start_ms=curr_start_ms,
                end_ms=curr_end_ms,
                text=display_text,
            )
        )
        counter += 1
        curr_start_ms = curr_end_ms

    return cues, counter


def format_srt(cues: list[SRTCue]) -> str:
    """Format list of SRTCue objects into standard SRT subtitle format."""
    lines: list[str] = []
    for cue in cues:
        start_ts = format_timestamp(cue.start_ms)
        end_ts = format_timestamp(cue.end_ms)
        lines.append(str(cue.index))
        lines.append(f"{start_ts} --> {end_ts}")
        lines.append(cue.text)
        lines.append("")  # Blank separator
    return "\n".join(lines)


def build_metadata(
    run_id: str,
    backend_info: dict[str, str],
    assembler_info: dict[str, str],
    options: PodcastMediaOptions,
    voices: dict[str, str],
    segments: list[SegmentSynthesisResult],
    segment_timings: list[dict[str, Any]],
    total_duration_sec: float,
    created_at: datetime,
    is_unvalidated: bool,
) -> dict[str, Any]:
    """Construct technical metadata dictionary."""
    return {
        "run_id": run_id,
        "backend": backend_info,
        "assembler": assembler_info,
        "options": {
            "pause_diff_speaker_ms": options.pause_diff_speaker_ms,
            "pause_same_speaker_ms": options.pause_same_speaker_ms,
            "allow_unvalidated": options.allow_unvalidated,
            "force": options.force,
        },
        "voices": voices,
        "segments": segment_timings,
        "total_duration_sec": round(total_duration_sec, 3),
        "files": {
            "audio": f"{run_id}.mp3",
            "subtitles": f"{run_id}.srt",
            "metadata": f"{run_id}.json",
        },
        "created_at": created_at.isoformat(),
        "note": (
            "WARNING: Synthesized from unvalidated editorial package."
            if is_unvalidated
            else None
        ),
    }


# ==============================================================================
# Orchestration Function
# ==============================================================================


def synthesize_podcast(
    run_id: str,
    editorial_pkg: EditorialPackage,
    settings: Settings,
    options: PodcastMediaOptions,
    output_media_dir: Path,
    tts_backend: TTSBackend | None = None,
    assembler: AudioAssembler | None = None,
    clock_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    sleep_fn: Callable[[float], None] = time.sleep,
) -> PodcastMediaResult:
    """Synthesize multi-speaker podcast audio, pauses, SRT subtitles, and metadata.

    Atomic guarantees:
    - Writes all files inside a run-specific temp directory first.
    - Moves files to output_media_dir upon full completion.
    - Cleans up partial files and temp directory on any error or interrupt.
    """
    if not RUN_ID_REGEX.match(run_id):
        raise MediaInputError(
            f"Invalid run_id '{run_id}'. Must contain only [A-Za-z0-9._-]."
        )

    # Resolve Assembler & Backend
    chosen_assembler = assembler or PurePythonMP3Assembler()
    chosen_backend = tts_backend or EdgeTTSBackend(assembler=chosen_assembler)

    # Resolve Voices
    script_speakers = [s.speaker for s in editorial_pkg.podcast.segments]
    configured_speakers = settings.editorial.podcast_speakers
    voices = resolve_speaker_voices(
        speakers_in_script=script_speakers,
        configured_speakers=configured_speakers,
        cli_overrides=options.voice_overrides,
    )

    output_media_dir.mkdir(parents=True, exist_ok=True)
    final_audio = output_media_dir / f"{run_id}.mp3"
    final_srt = output_media_dir / f"{run_id}.srt"
    final_meta = output_media_dir / f"{run_id}.json"

    # Overwrite check (D4)
    if not options.force:
        existing = [p for p in (final_audio, final_srt, final_meta) if p.exists()]
        if existing:
            raise MediaInputError(
                f"Output files already exist for run '{run_id}'. Use --force to overwrite."
            )

    # Atomic temporary directory
    temp_dir_obj = tempfile.TemporaryDirectory(prefix=f"cyberpulse_podcast_{run_id}_")
    temp_dir = Path(temp_dir_obj.name)

    created_finals: list[Path] = []
    try:
        segment_results: list[SegmentSynthesisResult] = []

        # 1. Synthesize segments sequentially with bounded retries
        for idx, seg in enumerate(editorial_pkg.podcast.segments):
            speaker = seg.speaker
            voice = voices[speaker]
            text = prepare_tts_text(seg.line.text)
            seg_audio_path = temp_dir / f"seg_{idx:03d}.mp3"

            attempts = 0
            max_attempts = 3
            backoff_delays = [1.0, 2.0, 4.0]
            measured_dur = 0.0

            while attempts < max_attempts:
                attempts += 1
                try:
                    measured_dur = asyncio.run(
                        chosen_backend.synthesize_segment(
                            text=text,
                            voice=voice,
                            output_path=seg_audio_path,
                        )
                    )
                    break
                except Exception as exc:
                    if attempts >= max_attempts:
                        raise MediaSynthesisError(
                            f"Synthesis failed for segment {idx} ('{speaker}') after {max_attempts} attempts: {exc}"
                        ) from exc
                    delay = backoff_delays[attempts - 1]
                    logger.warning(
                        "Segment %d synthesis failed (attempt %d/%d). Retrying in %.1fs: %s",
                        idx,
                        attempts,
                        max_attempts,
                        delay,
                        exc,
                    )
                    sleep_fn(delay)

            segment_results.append(
                SegmentSynthesisResult(
                    index=idx,
                    speaker=speaker,
                    voice=voice,
                    char_count=len(text),
                    word_count=len(text.split()),
                    text=text,
                    audio_path=seg_audio_path,
                    duration_sec=measured_dur,
                )
            )

        # 2. Assemble audio with pauses
        temp_audio = temp_dir / f"{run_id}.mp3"
        total_audio_duration = chosen_assembler.assemble(
            segments=segment_results,
            output_path=temp_audio,
            pause_diff_speaker_ms=options.pause_diff_speaker_ms,
            pause_same_speaker_ms=options.pause_same_speaker_ms,
        )

        # 3. Build Timeline & SRT Cues
        srt_cues: list[SRTCue] = []
        segment_timings: list[dict[str, Any]] = []
        cue_counter = 1
        curr_timeline_sec = 0.0

        for i, res in enumerate(segment_results):
            seg_start_sec = curr_timeline_sec
            seg_end_sec = seg_start_sec + res.duration_sec

            cues, cue_counter = build_segment_srt_cues(
                segment_index=i,
                speaker=res.speaker,
                text=res.text,
                start_sec=seg_start_sec,
                duration_sec=res.duration_sec,
                cue_counter_start=cue_counter,
            )
            srt_cues.extend(cues)

            segment_timings.append(
                {
                    "index": res.index,
                    "speaker": res.speaker,
                    "voice": res.voice,
                    "char_count": res.char_count,
                    "word_count": res.word_count,
                    "start_sec": round(seg_start_sec, 3),
                    "end_sec": round(seg_end_sec, 3),
                    "duration_sec": round(res.duration_sec, 3),
                }
            )

            # Advance timeline with pause if not last segment
            if i < len(segment_results) - 1:
                next_speaker = segment_results[i + 1].speaker
                pause_ms = (
                    options.pause_diff_speaker_ms
                    if res.speaker != next_speaker
                    else options.pause_same_speaker_ms
                )
                curr_timeline_sec = seg_end_sec + (pause_ms / 1000.0)
            else:
                curr_timeline_sec = seg_end_sec

        temp_srt = temp_dir / f"{run_id}.srt"
        temp_srt.write_text(format_srt(srt_cues), encoding="utf-8")

        # 4. Technical Metadata
        backend_name = (
            "edge-tts"
            if isinstance(chosen_backend, EdgeTTSBackend)
            else getattr(chosen_backend, "name", chosen_backend.__class__.__name__)
        )
        assembler_name = (
            "pure-python-mp3"
            if isinstance(chosen_assembler, PurePythonMP3Assembler)
            else getattr(chosen_assembler, "name", chosen_assembler.__class__.__name__)
        )

        meta_dict = build_metadata(
            run_id=run_id,
            backend_info={"name": backend_name, "version": "active"},
            assembler_info={"name": assembler_name, "version": "active"},
            options=options,
            voices=voices,
            segments=segment_results,
            segment_timings=segment_timings,
            total_duration_sec=total_audio_duration or curr_timeline_sec,
            created_at=clock_fn(),
            is_unvalidated=options.allow_unvalidated,
        )
        temp_meta = temp_dir / f"{run_id}.json"
        temp_meta.write_text(json.dumps(meta_dict, indent=2, ensure_ascii=False), encoding="utf-8")

        # 5. Move files to final location atomically (.json last as completion marker)
        shutil.move(str(temp_audio), str(final_audio))
        created_finals.append(final_audio)

        shutil.move(str(temp_srt), str(final_srt))
        created_finals.append(final_srt)

        shutil.move(str(temp_meta), str(final_meta))
        created_finals.append(final_meta)

        return PodcastMediaResult(
            run_id=run_id,
            audio_path=final_audio,
            srt_path=final_srt,
            meta_path=final_meta,
            total_duration_sec=total_audio_duration or curr_timeline_sec,
            segment_count=len(segment_results),
            voices=voices,
        )

    except (Exception, KeyboardInterrupt):
        # On failure, clean up any partially moved final files
        for f in created_finals:
            if f.exists():
                try:
                    f.unlink()
                except OSError:
                    pass
        raise
    finally:
        try:
            temp_dir_obj.cleanup()
        except OSError:
            pass
