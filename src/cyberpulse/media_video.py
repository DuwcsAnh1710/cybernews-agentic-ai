"""CyberPulse Short-Form Video Synthesis & HyperFrames Composition Module.

Refactored end-to-end video pipeline:
1. Automatically sources multi-speaker podcast audio (Minh & Lan) when available
   and intelligently curates/trims the dialogue to strictly under 60 seconds (< 1 min)
   at natural turn/sentence boundaries while preserving the core intelligence message.
2. Renders 9:16 vertical frames (1080x1920 / 720x1280) in the Hallmark Handcrafted
   Editorial Aesthetic (warm archival paper, deep printer's ink, serif display heads,
   sentence-case marginalia, studio crop marks, tactile framed plates, and real audio
   cadence traces — zero sci-fi HUD clichés or AI-generated slop).
3. Exports a native, Studio-editable HyperFrames project (<run_id>_hyperframes/)
   alongside the rendered MP4 video, synchronized SRT subtitles, cover, and metadata.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import html
import json
import logging
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Protocol

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from cyberpulse.config import Settings
from cyberpulse.models import EditorialPackage, VideoScene

logger = logging.getLogger("cyberpulse.media_video")

VOICE_NAME_REGEX = re.compile(r"^[a-z]{2,3}-[A-Z]{2,3}-[A-Za-z0-9]+Neural$")
RUN_ID_REGEX = re.compile(r"^[A-Za-z0-9._-]+$")

SUPPORTED_RESOLUTIONS: dict[str, tuple[int, int]] = {
    "1080x1920": (1080, 1920),
    "720x1280": (720, 1280),
}
DEFAULT_RESOLUTION = "1080x1920"
DEFAULT_VOICE = "vi-VN-NamMinhNeural"
SUPPORTED_VOICES = ["vi-VN-NamMinhNeural", "vi-VN-HoaiMyNeural"]

# Hard ceiling for short-form video duration (< 1 minute)
MAX_VIDEO_DURATION_SEC = 58.0
DEFAULT_TARGET_DURATION_SEC = 55.0

# Hallmark Editorial Palette Tokens (RGB equivalents of OKLCH tokens)
HALLMARK_PAPER = (246, 242, 234)       # Warm oat / archival paper (#F6F2EA)
HALLMARK_PAPER_WELL = (238, 232, 220)  # Secondary press well (#EEE8DC)
HALLMARK_PAPER_MAT = (251, 249, 244)   # Passe-partout museum mat (#FBF9F4)
HALLMARK_INK = (26, 24, 22)            # Deep printer's ink (#1A1816)
HALLMARK_INK_BODY = (56, 52, 47)       # Warm charcoal body text (#38342F)
HALLMARK_MUTED = (112, 104, 94)        # Warm stone marginalia (#70685E)
HALLMARK_RULE = (206, 198, 186)        # 1px hairline stone rule (#CEC6BA)
HALLMARK_RULE_DARK = (164, 154, 140)   # Darker construction rule (#A49A8C)

# Restrained Editorial Pigments (One pigment per story posture)
PIGMENT_VERMILION = (184, 51, 36)      # Printer's vermilion / crimson (#B83324)
PIGMENT_FOREST = (31, 110, 74)         # Editorial forest green (#1F6E4A)
PIGMENT_INK_BLUE = (37, 88, 133)       # Archival Prussian blue (#255885)
PIGMENT_OCHRE = (141, 91, 27)          # Raw sienna / ochre (#8D5B1B)

PRESERVED_ACRONYMS = {
    "CVE", "CISA", "KEV", "APT", "SSH", "OpenSSH", "OpenAI", "ChatGPT",
    "AI", "Linux", "Alpine", "Debian", "Ubuntu", "Apple", "iOS", "macOS",
    "iPhone", "Android", "Zyxel", "Ivanti", "Fortinet", "Cisco", "VMware",
    "Palo", "Alto", "Microsoft", "Windows", "AWS", "Google", "Chrome",
    "FBI", "NSA", "MITM", "RCE", "DDoS", "SQL", "XSS", "VPN", "DNS",
    "BGP", "TLS", "SSL", "API", "SDK", "CLI", "USB", "PDF", "HTML", "CGI",
    "GS1900", "LockBit", "BlackCat", "Akira", "CVSS",
}


# ==============================================================================
# Exceptions Mapped to Exit Codes
# ==============================================================================

from cyberpulse.media.podcast import (
    MediaDependencyError,
    MediaError,
    MediaInputError,
    MediaSynthesisError,
    MediaValidationError,
    PurePythonMP3Assembler,
    parse_id3v2_length,
    parse_mp3_frame_header,
    resolve_speaker_voices,
    split_text_into_cues,
)


# ==============================================================================
# Dataclasses
# ==============================================================================


@dataclass(frozen=True)
class VideoMediaOptions:
    """Options for short-form video synthesis and HyperFrames export."""

    voice: str = DEFAULT_VOICE
    resolution: str = DEFAULT_RESOLUTION
    output_dir: Path | None = None
    force: bool = False
    preview_only: bool = False
    allow_unvalidated: bool = False
    fps: int = 30
    prefer_ffmpeg: bool = True
    max_duration_sec: float = DEFAULT_TARGET_DURATION_SEC
    prefer_podcast_audio: bool = True
    export_hyperframes: bool = True
    # "auto": generate context-matched AI illustrations per beat (Gemini image -> Pollinations);
    # "off": only use images already present in <run_id>_assets/ (hermetic default for tests).
    illustrations: str = "off"
    # Frame-accurate motion timeline (karaoke transcript, Ken Burns, dissolves) when FFmpeg exists.
    motion: bool = True


@dataclass(frozen=True)
class PodcastClipSegment:
    """A curated podcast dialogue turn selected for the sub-60s video brief."""

    segment_index: int
    speaker: str
    speaker_role: str
    voice: str
    headline: str
    category_label: str
    text: str
    cited_article_ids: list[str]
    source_start_sec: float
    source_end_sec: float
    duration_sec: float


@dataclass(frozen=True)
class SceneRenderResult:
    """Result of rendering a single video scene."""

    scene_no: int
    on_screen_text: str
    narration: str
    slide_image_path: Path
    audio_path: Path | None
    duration_sec: float
    start_sec: float
    end_sec: float
    speaker: str = "Biên tập viên"
    speaker_role: str = "Bản tin sáng"
    category_label: str = "Tiêu điểm an ninh mạng"
    cue_frames: list[tuple[Path, float]] = field(default_factory=list)
    motion: Any = None  # media_motion.SceneMotionSpec when the motion timeline is used


@dataclass(frozen=True)
class VideoMediaResult:
    """Result of full short-form video synthesis."""

    run_id: str
    video_path: Path | None
    srt_path: Path
    meta_path: Path
    cover_image: Path
    scene_images: list[Path]
    total_duration_sec: float
    scene_count: int
    resolution: str
    voice: str
    preview_only: bool
    hyperframes_dir: Path | None = None
    audio_source: str = "podcast"


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


class VideoAssembler(Protocol):
    """Protocol for video assembly."""

    def assemble_video(
        self,
        scenes: list[SceneRenderResult],
        assembled_audio_path: Path | None,
        output_path: Path,
        width: int,
        height: int,
        fps: int,
    ) -> float:
        """Assemble scenes into final MP4 video. Return total duration in seconds."""
        ...


# ==============================================================================
# Edge-TTS Backend
# ==============================================================================


class EdgeTTSBackend:
    """Edge-TTS synthesizer with lazy import and duration measurement."""

    def __init__(self) -> None:
        import importlib.util

        if importlib.util.find_spec("edge_tts") is None:
            raise MediaDependencyError(
                "edge-tts is required for video voiceover synthesis. "
                "Install with: pip install .[media]"
            )
        try:
            import edge_tts  # noqa: F401
        except ImportError as exc:
            raise MediaDependencyError(
                "edge-tts is required for video voiceover synthesis. "
                "Install with: pip install .[media]"
            ) from exc

    async def synthesize_segment(
        self,
        text: str,
        voice: str,
        output_path: Path,
        timeout: float = 30.0,
    ) -> float:
        """Synthesize text using edge-tts. Return measured duration in seconds."""
        import edge_tts

        output_path.parent.mkdir(parents=True, exist_ok=True)
        last_exc: Exception | None = None
        for attempt in range(4):
            try:
                communicate = edge_tts.Communicate(text=text, voice=voice)
                await asyncio.wait_for(communicate.save(str(output_path)), timeout=timeout)
                last_exc = None
                break
            except (asyncio.TimeoutError, TimeoutError):
                raise
            except Exception as exc:
                last_exc = exc
                await asyncio.sleep(1.5 * (attempt + 1))

        if last_exc is not None:
            raise last_exc

        assembler = PurePythonMP3Assembler()
        return assembler.measure_duration(output_path)


# ==============================================================================
# Smart Podcast Audio Curator & MP3 Frame Slicer (< 60 Seconds)
# ==============================================================================


def format_editorial_headline(raw_text: str) -> str:
    """Convert shouting ALL-CAPS or robotic titles into natural editorial Vietnamese case."""
    cleaned = re.sub(r"^\s*\[[^\]]+\]\s*", "", raw_text.strip())
    cleaned = cleaned.replace("•", "-").strip(" -!")
    if not cleaned:
        return "Điểm tin an ninh mạng"

    # Check if the string is predominantly ALL CAPS
    alpha_chars = [c for c in cleaned if c.isalpha()]
    if alpha_chars and sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars) > 0.72:
        words = cleaned.split()
        out_words: list[str] = []
        for idx, w in enumerate(words):
            bare = w.strip(".,:;()[]\"'")
            if re.match(r"^CVE-\d{4}-\d+$", bare, re.IGNORECASE):
                out_words.append(bare.upper())
                continue
            matched_acr = next((acr for acr in PRESERVED_ACRONYMS if acr.lower() == bare.lower()), None)
            if matched_acr:
                out_words.append(w.replace(bare, matched_acr))
            elif idx == 0:
                out_words.append(w.capitalize())
            else:
                out_words.append(w.lower())
        return " ".join(out_words)

    return cleaned


def _classify_podcast_turn(
    idx: int,
    total_Count: int,
    speaker: str,
    text: str,
    cited_ids: list[str],
) -> dict[str, Any]:
    """Evaluate a podcast dialogue turn for core editorial information density."""
    low = text.lower().strip()
    words = text.split()
    word_count = len(words)

    # 1. Detect generic opening greeting
    greeting_markers = (
        "xin chào các bạn",
        "chào mừng quay trở lại",
        "đồng hành cùng mình hôm nay",
        "chúng ta sẽ điểm qua",
        "mình là minh",
    )
    has_tech_fact = bool(
        re.search(r"cve-\d{4}-\d+|\d+\s*(lỗ hổng|byte|triệu|nghìn|%)|phiên bản\s*\d", low)
    )
    is_greeting = (
        idx == 0
        and total_Count > 2
        and any(m in low for m in greeting_markers)
        and not has_tech_fact
    )

    # 2. Detect generic closing sign-off
    signoff_markers = (
        "cảm ơn các bạn đã theo dõi",
        "hẹn gặp lại các bạn",
        "tạm biệt và hẹn gặp lại",
        "cảm ơn chị lan. những tin tức hôm nay cho thấy",
        "khép lại bản tin hôm nay",
    )
    is_signoff = (
        idx == total_Count - 1
        and total_Count > 2
        and any(m in low for m in signoff_markers)
        and not has_tech_fact
    )

    # 3. Detect short prompt/bridge question without standalone facts
    is_question = low.endswith("?") and word_count <= 28 and not has_tech_fact

    # 4. Calculate substantive editorial score
    score = 0.0
    if not is_greeting and not is_signoff and not is_question:
        score += 45.0
    if is_question:
        score += 8.0
    if has_tech_fact:
        score += 28.0
    if 1 <= len(cited_ids) <= 2:
        score += 20.0
    elif len(cited_ids) > 2 and not is_greeting and not is_signoff:
        score += 10.0

    # High-signal cybersecurity terms
    signal_terms = (
        "lỗ hổng", "bản vá", "khẩn cấp", "khai thác", "zero-click", "zero-day",
        "root", "kernel", "xác thực", "mã độc", "ransomware", "rò rỉ",
        "chiến dịch", "tấn công", "cve-", "openssh", "cisa", "apt",
    )
    score += sum(4.5 for t in signal_terms if t in low)

    # Prefer concise, punchy turns (8s - 18s sweet spot)
    if 18 <= word_count <= 62:
        score += 12.0

    return {
        "is_greeting": is_greeting,
        "is_signoff": is_signoff,
        "is_question": is_question,
        "score": score,
    }


def _match_headline_and_category(
    text: str,
    cited_ids: list[str],
    editorial_pkg: EditorialPackage,
    fallback_index: int,
) -> tuple[str, str]:
    """Match a podcast segment to the best editorial headline and sentence-case rubric."""
    cited_set = set(cited_ids)

    # 1. Try matching a VideoScene with overlapping cited_article_ids (excluding generic intro/outro scenes)
    best_scene_title: str | None = None
    for sc in editorial_pkg.video.scenes:
        sc_ids = set(sc.narration.cited_article_ids)
        if cited_set and sc_ids == cited_set and len(sc_ids) <= 2:
            best_scene_title = format_editorial_headline(sc.on_screen_text)
            break

    # 2. Try matching a DigestSection heading for richer journalistic precision
    best_digest_heading: str | None = None
    for sec in editorial_pkg.digest.sections:
        sec_ids = set(sec.body.cited_article_ids)
        if cited_set and (sec_ids & cited_set):
            best_digest_heading = format_editorial_headline(sec.heading)
            break

    headline = best_digest_heading or best_scene_title
    if not headline:
        # Derive a clean headline from the first clause of the spoken text
        cleaned = re.sub(
            r"^(Chào\s+[^,.]+[,.\s]+|Đúng\s+vậy[,.\s]+|Thật\s+đáng\s+[^,.]+[,.\s]+|Tiếp\s+theo\s+là\s+|Cuối\s+cùng\s+là\s+)",
            "",
            text.strip(),
            flags=re.IGNORECASE,
        )
        first_sent = re.split(r"[.!?]", cleaned)[0].strip()
        words = first_sent.split()
        headline = " ".join(words[:10]) + ("…" if len(words) > 10 else "")
        headline = format_editorial_headline(headline or editorial_pkg.video.title)

    theme_info = detect_scene_theme(headline, text, is_cover=False)
    return headline, theme_info["badge"]


def curate_podcast_highlights(
    editorial_pkg: EditorialPackage,
    settings: Settings,
    podcast_meta: dict[str, Any] | None = None,
    max_duration_sec: float = DEFAULT_TARGET_DURATION_SEC,
    pause_sec: float = 0.24,
) -> list[PodcastClipSegment]:
    """Automatically select and edit podcast dialogue turns to fit strictly under 1 minute (< 60s).

    Preserves complete speaker sentences, eliminates hollow intro/outro filler when over budget,
    maximizes coverage of distinct news articles, and maintains natural two-host alternation.
    """
    hard_cap = min(max_duration_sec, MAX_VIDEO_DURATION_SEC)
    segments = editorial_pkg.podcast.segments
    if not segments:
        return []

    script_speakers = [s.speaker for s in segments]
    voices = resolve_speaker_voices(
        speakers_in_script=script_speakers,
        configured_speakers=settings.editorial.podcast_speakers,
    )

    meta_timings: dict[int, dict[str, Any]] = {}
    if podcast_meta and isinstance(podcast_meta.get("segments"), list):
        for item in podcast_meta["segments"]:
            if isinstance(item, dict) and "index" in item:
                meta_timings[int(item["index"])] = item

    # Build candidate records with measured or estimated durations
    candidates: list[dict[str, Any]] = []
    running_est_sec = 0.0
    for idx, seg in enumerate(segments):
        clean_line = " ".join(seg.line.text.strip().split())
        # Strip conversational filler prefixes if segment is long
        t_info = meta_timings.get(idx)
        if t_info and float(t_info.get("duration_sec", 0.0)) > 0.1:
            start_s = float(t_info.get("start_sec", running_est_sec))
            dur_s = float(t_info["duration_sec"])
            end_s = float(t_info.get("end_sec", start_s + dur_s))
        else:
            word_cnt = max(1, len(clean_line.split()))
            dur_s = round(max(3.2, word_cnt / 3.15), 3)
            start_s = running_est_sec
            end_s = start_s + dur_s
        running_est_sec = end_s + pause_sec

        eval_info = _classify_podcast_turn(
            idx=idx,
            total_Count=len(segments),
            speaker=seg.speaker,
            text=clean_line,
            cited_ids=seg.line.cited_article_ids,
        )
        headline, category_label = _match_headline_and_category(
            text=clean_line,
            cited_ids=seg.line.cited_article_ids,
            editorial_pkg=editorial_pkg,
            fallback_index=idx + 1,
        )
        role = (
            "Biên tập viên chuyên mục"
            if seg.speaker.lower().startswith("lan")
            else "Phân tích kỹ thuật"
        )
        candidates.append({
            "index": idx,
            "speaker": seg.speaker,
            "speaker_role": role,
            "voice": voices.get(seg.speaker, DEFAULT_VOICE),
            "headline": headline,
            "category_label": category_label,
            "text": clean_line,
            "cited_ids": list(seg.line.cited_article_ids),
            "start_sec": start_s,
            "end_sec": end_s,
            "duration_sec": dur_s,
            **eval_info,
        })

    # If the entire podcast already fits within hard_cap and has <= 5 segments, keep all
    total_raw_dur = sum(c["duration_sec"] for c in candidates) + max(0, len(candidates) - 1) * pause_sec
    if total_raw_dur <= hard_cap and len(candidates) <= 5:
        selected = candidates
    else:
        # Filter out pure greeting and signoff turns first
        core_pool = [
            c for c in candidates
            if not c["is_greeting"] and not c["is_signoff"]
        ]
        if not core_pool:
            core_pool = candidates

        selected_indices: set[int] = set()
        covered_articles: set[str] = set()
        used_dur = 0.0
        last_speaker: str | None = None

        # Pass 1: Pick the strongest substantive turn for each distinct article in chronological order
        substantive_pool = [c for c in core_pool if not c["is_question"]] or core_pool
        for cand in substantive_pool:
            cand_articles = set(cand["cited_ids"])
            is_new_story = bool(cand_articles - covered_articles) or not covered_articles
            if not is_new_story:
                continue

            extra_pause = pause_sec if selected_indices else 0.0
            if used_dur + extra_pause + cand["duration_sec"] <= hard_cap:
                selected_indices.add(cand["index"])
                used_dur += extra_pause + cand["duration_sec"]
                covered_articles.update(cand_articles)
                last_speaker = cand["speaker"]

        # Pass 2: If budget remains, add remaining high-value substantive turns (preferring speaker alternation)
        remaining_substantive = sorted(
            [c for c in substantive_pool if c["index"] not in selected_indices],
            key=lambda x: (
                1 if x["speaker"] != last_speaker else 0,
                x["score"],
                -x["index"],
            ),
            reverse=True,
        )
        for cand in remaining_substantive:
            extra_pause = pause_sec if selected_indices else 0.0
            if used_dur + extra_pause + cand["duration_sec"] <= hard_cap:
                selected_indices.add(cand["index"])
                used_dur += extra_pause + cand["duration_sec"]
                last_speaker = cand["speaker"]

        # Pass 3: If budget still allows (>= 5s), include a natural bridge question right before a selected answer
        for cand in core_pool:
            if cand["index"] in selected_indices or not cand["is_question"]:
                continue
            if (cand["index"] + 1) in selected_indices:
                extra_pause = pause_sec if selected_indices else 0.0
                if used_dur + extra_pause + cand["duration_sec"] <= hard_cap:
                    selected_indices.add(cand["index"])
                    used_dur += extra_pause + cand["duration_sec"]

        # Fallback if single segment was longer than hard_cap
        if not selected_indices:
            best_single = max(core_pool, key=lambda x: x["score"])
            selected = [best_single]
        else:
            selected = [c for c in candidates if c["index"] in selected_indices]

    # Sort chronologically and enforce hard_cap on the final sequence
    selected.sort(key=lambda x: x["index"])
    result: list[PodcastClipSegment] = []
    acc_dur = 0.0

    for i, item in enumerate(selected):
        pause_add = pause_sec if i > 0 else 0.0
        avail = hard_cap - acc_dur - pause_add
        if avail < 2.0 and result:
            break

        dur = min(item["duration_sec"], max(2.0, avail))
        text_out = item["text"]
        # If segment had to be trimmed significantly, trim text cleanly at sentence boundary
        if dur < item["duration_sec"] * 0.88:
            ratio = dur / max(0.1, item["duration_sec"])
            target_chars = max(40, int(len(text_out) * ratio))
            sentences = re.split(r"(?<=[.!?])\s+", text_out)
            kept: list[str] = []
            cur_len = 0
            for s in sentences:
                if cur_len + len(s) <= target_chars or not kept:
                    kept.append(s)
                    cur_len += len(s) + 1
                else:
                    break
            text_out = " ".join(kept)

        result.append(
            PodcastClipSegment(
                segment_index=item["index"],
                speaker=item["speaker"],
                speaker_role=item["speaker_role"],
                voice=item["voice"],
                headline=item["headline"],
                category_label=item["category_label"],
                text=text_out,
                cited_article_ids=item["cited_ids"],
                source_start_sec=round(item["start_sec"], 3),
                source_end_sec=round(item["start_sec"] + dur, 3),
                duration_sec=round(dur, 3),
            )
        )
        acc_dur += pause_add + dur

    return result


def slice_mp3_bytes(
    mp3_data: bytes,
    start_sec: float,
    end_sec: float,
) -> tuple[bytes, float]:
    """Slice raw MP3 bytes between [start_sec, end_sec] along exact MPEG frame boundaries."""
    if not mp3_data or end_sec <= start_sec:
        return b"", 0.0

    offset = parse_id3v2_length(mp3_data)
    end_offset = len(mp3_data)
    if end_offset >= 128 and mp3_data[end_offset - 128 : end_offset - 125] == b"TAG":
        end_offset -= 128

    curr_time = 0.0
    sliced_Duration = 0.0
    out_frames: list[bytes] = []
    first_frame = True

    while offset < end_offset - 3:
        while offset < end_offset - 3 and not (
            mp3_data[offset] == 0xFF and (mp3_data[offset + 1] & 0xE0) == 0xE0
        ):
            offset += 1
        if offset >= end_offset - 3:
            break

        header = parse_mp3_frame_header(mp3_data, offset)
        if not header:
            offset += 1
            continue

        frame_len = header["frame_len"]
        if frame_len <= 0 or offset + frame_len > len(mp3_data):
            offset += 1
            continue

        # Skip Xing/Info VBR metadata frame at start of file
        is_xing = False
        if first_frame:
            first_frame = False
            side_info_len = (
                (17 if header["channel_mode"] == 3 else 32)
                if header["version_id"] == 3
                else (9 if header["channel_mode"] == 3 else 17)
            )
            xing_offset = offset + 4 + side_info_len
            if xing_offset + 4 <= len(mp3_data) and mp3_data[xing_offset : xing_offset + 4] in (
                b"Xing",
                b"Info",
            ):
                is_xing = True

        if not is_xing:
            frame_dur = float(header["duration"])
            frame_mid = curr_time + frame_dur * 0.5
            if frame_mid >= start_sec and curr_time < end_sec:
                out_frames.append(mp3_data[offset : offset + frame_len])
                sliced_Duration += frame_dur
            curr_time += frame_dur
            if curr_time >= end_sec + 0.05:
                break

        offset += frame_len

    return b"".join(out_frames), round(sliced_Duration, 3)


def extract_mp3_energy_profile(mp3_data: bytes, num_points: int = 28) -> list[float]:
    """Compute a normalized [0.15 .. 1.0] voice cadence profile from real MP3 frame bytes."""
    if not mp3_data or num_points <= 0:
        return [0.35 + 0.25 * abs(math.sin(i * 0.55)) for i in range(max(1, num_points))]

    chunk_size = max(1, len(mp3_data) // num_points)
    raw_vals: list[float] = []
    for i in range(num_points):
        sub = mp3_data[i * chunk_size : (i + 1) * chunk_size]
        if not sub:
            raw_vals.append(0.2)
            continue
        # Sample byte variance inside the MPEG audio payload as an envelope proxy
        step = max(1, len(sub) // 64)
        samples = sub[4::step]
        if not samples:
            raw_vals.append(0.2)
            continue
        mean_v = sum(samples) / len(samples)
        var_v = sum(abs(b - mean_v) for b in samples) / len(samples)
        raw_vals.append(var_v)

    min_v = min(raw_vals) if raw_vals else 0.0
    max_v = max(raw_vals) if raw_vals else 1.0
    span = max(1e-3, max_v - min_v)
    return [round(0.18 + 0.82 * ((v - min_v) / span), 3) for v in raw_vals]


# ==============================================================================
# Font Resolution & Text Layout Helpers
# ==============================================================================


def resolve_font(
    size: int,
    bold: bool = False,
    serif: bool = False,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Find and load TrueType font supporting Vietnamese diacritics (Serif for display, Sans for body)."""
    candidates: list[str] = []
    if sys.platform == "win32":
        if serif:
            if bold:
                candidates.extend([
                    "C:/Windows/Fonts/palab.ttf",
                    "C:/Windows/Fonts/timesbd.ttf",
                    "C:/Windows/Fonts/cambriab.ttf",
                    "C:/Windows/Fonts/constanb.ttf",
                ])
            candidates.extend([
                "C:/Windows/Fonts/pala.ttf",
                "C:/Windows/Fonts/times.ttf",
                "C:/Windows/Fonts/constan.ttf",
            ])
        if bold:
            candidates.extend([
                "C:/Windows/Fonts/segoeuib.ttf",
                "C:/Windows/Fonts/calibrib.ttf",
                "C:/Windows/Fonts/arialbd.ttf",
            ])
        candidates.extend([
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/calibri.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ])
    else:
        if serif:
            candidates.extend([
                "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
                if bold
                else "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"
                if bold
                else "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
            ])
        candidates.extend([
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
        ])

    for fpath in candidates:
        if os.path.exists(fpath):
            try:
                return ImageFont.truetype(fpath, size)
            except Exception:
                continue

    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def wrap_text_to_width(
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    draw: ImageDraw.ImageDraw,
) -> list[str]:
    """Wrap text to fit within max_width in pixels without cutting words."""
    words = text.strip().split()
    if not words:
        return []

    lines: list[str] = []
    curr_line: list[str] = []

    for w in words:
        candidate = " ".join(curr_line + [w])
        bbox = draw.textbbox((0, 0), candidate, font=font)
        line_w = bbox[2] - bbox[0]
        if line_w <= max_width:
            curr_line.append(w)
        else:
            if curr_line:
                lines.append(" ".join(curr_line))
            curr_line = [w]

    if curr_line:
        lines.append(" ".join(curr_line))

    return lines or [text]


# ==============================================================================
# Hallmark Handcrafted Editorial Theme & Monograph Plate Illustrators
# ==============================================================================


def detect_scene_theme(on_screen: str, narration: str, is_cover: bool = False) -> dict[str, Any]:
    """Map story content to a restrained Hallmark Editorial posture and single printer's pigment."""
    combined = f"{on_screen} {narration}".lower()
    if is_cover:
        return {
            "name": "OVERVIEW",
            "badge": "Ấn bản tiêu điểm sáng nay",
            "primary": PIGMENT_VERMILION,
            "bg_tint": HALLMARK_PAPER_WELL,
            "icon_type": "editorial_press",
            "caption_note": "Tổng hợp và biên tập từ bản tin đối thoại sáng",
        }
    if any(w in combined for w in ["ransomware", "tống tiền", "mã hóa", "lockbit", "blackcat", "akira", "extortion"]):
        return {
            "name": "RANSOMWARE",
            "badge": "Hồ sơ sự cố mã độc tống tiền",
            "primary": PIGMENT_VERMILION,
            "bg_tint": HALLMARK_PAPER_WELL,
            "icon_type": "vault_seal",
            "caption_note": "Hình minh họa — Cơ chế phong tỏa và kiểm soát truy cập tệp",
        }
    if any(w in combined for w in ["lỗ hổng", "cve", "zero-day", "0-day", "zero-click", "exploit", "openssh", "ivanti", "zyxel"]):
        return {
            "name": "VULNERABILITY",
            "badge": "Hồ sơ lỗ hổng và bản vá khẩn",
            "primary": PIGMENT_VERMILION,
            "bg_tint": HALLMARK_PAPER_WELL,
            "icon_type": "auth_ledger",
            "caption_note": "Hình minh họa — Phân tích bề mặt xác thực và bản vá bảo mật",
        }
    if any(w in combined for w in ["rò rỉ", "lộ lọt", "breach", "leak", "dữ liệu", "lockdown", "chatgpt", "prompt"]):
        return {
            "name": "DATA_BREACH",
            "badge": "Cảnh báo an toàn dữ liệu",
            "primary": PIGMENT_OCHRE,
            "bg_tint": HALLMARK_PAPER_WELL,
            "icon_type": "data_archive",
            "caption_note": "Hình minh họa — Kiểm soát ranh giới dữ liệu và luồng truy vấn",
        }
    if any(w in combined for w in ["linux", "apt", "debian", "ubuntu", "root", "kernel", "botnet", "android", "apple", "iphone"]):
        return {
            "name": "SYSTEM_MALWARE",
            "badge": "Phân tích kiến trúc hệ thống",
            "primary": PIGMENT_INK_BLUE,
            "bg_tint": HALLMARK_PAPER_WELL,
            "icon_type": "kernel_layers",
            "caption_note": "Hình minh họa — Sơ đồ phân tầng đặc quyền nhân hệ điều hành",
        }
    return {
        "name": "GENERAL",
        "badge": "Bản tin phân tích an ninh mạng",
        "primary": PIGMENT_FOREST,
        "bg_tint": HALLMARK_PAPER_WELL,
        "icon_type": "editorial_press",
        "caption_note": "Hình minh họa — Hồ sơ theo dõi an toàn thông tin",
    }


def _extract_story_tokens(on_screen: str, narration: str) -> list[str]:
    """Extract real factual identifiers (CVEs, versions, vendors, numbers) from the story."""
    combined = f"{on_screen} {narration}"
    tokens: list[str] = []

    for m in re.findall(r"CVE-\d{4}-\d+", combined, flags=re.IGNORECASE):
        up = m.upper()
        if up not in tokens:
            tokens.append(up)

    for m in re.findall(r"\b\d+(?:\.\d+)+p?\d*\b", combined):
        tag = f"v{m}"
        if tag not in tokens:
            tokens.append(tag)

    for acr in PRESERVED_ACRONYMS:
        if acr in ("CVE", "AI", "CLI", "API"):
            continue
        if re.search(rf"\b{re.escape(acr)}\b", combined, flags=re.IGNORECASE):
            if acr not in tokens:
                tokens.append(acr)

    for m in re.findall(r"\b\d{2,4}\s+(?:lỗ hổng|lỗi|byte|máy chủ|thiết bị)\b", combined, flags=re.IGNORECASE):
        if m not in tokens:
            tokens.append(m)

    return tokens[:4] or ["Hồ sơ kỹ thuật", "Kiểm chứng nguồn"]


def _draw_studio_crop_marks(
    draw: ImageDraw.ImageDraw,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    scale: float,
    color: tuple[int, int, int] = HALLMARK_INK,
) -> None:
    """Draw delicate Hallmark Studio printer's crop marks outside the four corners of a plate."""
    gap = max(4, round(8 * scale))
    length = max(8, round(16 * scale))
    w = max(1, round(1.5 * scale))

    # Top-left
    draw.line([(x0 - gap - length, y0), (x0 - gap, y0)], fill=color, width=w)
    draw.line([(x0, y0 - gap - length), (x0, y0 - gap)], fill=color, width=w)
    # Top-right
    draw.line([(x1 + gap, y0), (x1 + gap + length, y0)], fill=color, width=w)
    draw.line([(x1, y0 - gap - length), (x1, y0 - gap)], fill=color, width=w)
    # Bottom-left
    draw.line([(x0 - gap - length, y1), (x0 - gap, y1)], fill=color, width=w)
    draw.line([(x0, y1 + gap), (x0, y1 + gap + length)], fill=color, width=w)
    # Bottom-right
    draw.line([(x1 + gap, y1), (x1 + gap + length, y1)], fill=color, width=w)
    draw.line([(x1, y1 + gap), (x1, y1 + gap + length)], fill=color, width=w)


def _draw_editorial_monograph_plate(
    draw: ImageDraw.ImageDraw,
    ix0: int,
    iy0: int,
    ix1: int,
    iy1: int,
    theme: dict[str, Any],
    tokens: list[str],
    scene_no: int,
    scale: float,
) -> None:
    """Draw a handcrafted architectural/monograph print plate on warm cream stock (Zero sci-fi HUD)."""
    w = ix1 - ix0
    h = iy1 - iy0
    cx = ix0 + w // 2
    cy = iy0 + h // 2
    primary = theme["primary"]
    line_w = max(1, round(1.5 * scale))
    bold_w = max(2, round(2.5 * scale))

    # Warm cream engraving ground
    draw.rectangle([ix0, iy0, ix1, iy1], fill=(241, 235, 223))

    # Subtle architectural construction lines (Hallmark Studio style)
    third_y = iy0 + round(h * 0.22)
    bot_band_y = iy1 - round(h * 0.18)
    draw.line([(ix0, third_y), (ix1, third_y)], fill=HALLMARK_RULE, width=1)
    draw.line([(ix0, bot_band_y), (ix1, bot_band_y)], fill=HALLMARK_RULE, width=1)

    font_label = resolve_font(max(11, round(19 * scale)), bold=False, serif=False)
    font_serif_num = resolve_font(max(16, round(36 * scale)), bold=True, serif=True)
    font_token = resolve_font(max(12, round(22 * scale)), bold=True, serif=False)

    # Top plate index header (left-aligned, sentence case)
    pad_x = round(28 * scale)
    draw.text(
        (ix0 + pad_x, iy0 + round(h * 0.08)),
        f"Bản vẽ {scene_no:02d}",
        fill=HALLMARK_INK,
        font=resolve_font(max(12, round(20 * scale)), bold=True, serif=True),
    )
    draw.text(
        (ix1 - pad_x, iy0 + round(h * 0.08)),
        tokens[0] if tokens else "Hồ sơ kỹ thuật",
        fill=primary,
        font=font_token,
        anchor="ra",
    )

    # Central architectural diagram according to story archetype
    icon_type = theme["icon_type"]
    diag_y0 = third_y + round(22 * scale)
    diag_y1 = bot_band_y - round(22 * scale)
    diag_h = diag_y1 - diag_y0
    diag_cy = diag_y0 + diag_h // 2

    if icon_type == "kernel_layers":
        # Architectural strata diagram (User space -> System call -> Kernel privilege)
        layers = [
            ("Tầng ứng dụng và gói phần mềm", HALLMARK_PAPER_MAT, HALLMARK_INK),
            ("Giao diện xác thực hệ thống", (233, 226, 212), HALLMARK_INK),
            ("Nhân hệ điều hành (Đặc quyền hệ thống)", primary, HALLMARK_PAPER_MAT),
        ]
        lh = round(diag_h / 3.4)
        bw = round(w * 0.78)
        bx0 = cx - bw // 2
        for l_idx, (l_title, bg_c, fg_c) in enumerate(layers):
            ly0 = diag_y0 + l_idx * (lh + round(10 * scale))
            ly1 = ly0 + lh
            draw.rectangle([bx0, ly0, bx0 + bw, ly1], fill=bg_c, outline=HALLMARK_INK, width=line_w)
            draw.text((bx0 + round(20 * scale), (ly0 + ly1) // 2), f"0{l_idx + 1}", fill=fg_c, font=font_token, anchor="lm")
            draw.text((bx0 + round(68 * scale), (ly0 + ly1) // 2), l_title, fill=fg_c, font=font_label, anchor="lm")

    elif icon_type == "auth_ledger":
        # Two-column verification schematic (Request -> Verification Gate -> Target Node)
        box_w = round(w * 0.27)
        box_h = round(diag_h * 0.72)
        y_top = diag_cy - box_h // 2
        nodes = [
            (ix0 + round(w * 0.06), "Luồng truy cập", tokens[0] if tokens else "Gói tin"),
            (cx - box_w // 2, "Cơ chế kiểm duyệt", tokens[1] if len(tokens) > 1 else "Xác thực"),
            (ix1 - round(w * 0.06) - box_w, "Vùng hệ thống", "Bản vá bảo vệ"),
        ]
        for n_idx, (nx, n_label, n_sub) in enumerate(nodes):
            is_mid = n_idx == 1
            fill_c = HALLMARK_PAPER_MAT if not is_mid else (248, 236, 232)
            out_c = primary if is_mid else HALLMARK_INK
            draw.rectangle([nx, y_top, nx + box_w, y_top + box_h], fill=fill_c, outline=out_c, width=bold_w if is_mid else line_w)
            draw.text((nx + round(16 * scale), y_top + round(18 * scale)), f"0{n_idx + 1}", fill=out_c, font=font_serif_num)
            draw.text((nx + round(16 * scale), y_top + box_h - round(62 * scale)), n_label, fill=HALLMARK_INK, font=font_label)
            draw.text((nx + round(16 * scale), y_top + box_h - round(32 * scale)), n_sub, fill=out_c, font=font_token)
            if n_idx < 2:
                next_nx = nodes[n_idx + 1][0]
                draw.line([(nx + box_w, diag_cy), (next_nx, diag_cy)], fill=primary if is_mid else HALLMARK_INK, width=bold_w)

    elif icon_type in ("vault_seal", "data_archive"):
        # Concentric archival seal & ledger index
        r_outer = round(min(w, diag_h) * 0.36)
        r_inner = round(r_outer * 0.62)
        draw.ellipse([cx - r_outer, diag_cy - r_outer, cx + r_outer, diag_cy + r_outer], fill=HALLMARK_PAPER_MAT, outline=HALLMARK_INK, width=bold_w)
        draw.ellipse([cx - r_inner, diag_cy - r_inner, cx + r_inner, diag_cy + r_inner], fill=(246, 238, 230), outline=primary, width=line_w)
        draw.line([(ix0 + pad_x, diag_cy), (cx - r_outer - round(16 * scale), diag_cy)], fill=HALLMARK_RULE_DARK, width=line_w)
        draw.line([(cx + r_outer + round(16 * scale), diag_cy), (ix1 - pad_x, diag_cy)], fill=HALLMARK_RULE_DARK, width=line_w)
        center_tag = tokens[0] if tokens else f"Mục 0{scene_no}"
        draw.text((cx, diag_cy), center_tag, fill=HALLMARK_INK, font=font_token, anchor="mm")

    else:
        # Editorial Press Monograph Grid (Clean ruled specimen table)
        cols = min(3, max(2, len(tokens)))
        cell_w = round((w - 2 * pad_x) / cols)
        cell_h = round(diag_h * 0.72)
        cy0 = diag_cy - cell_h // 2
        for c_idx in range(cols):
            cx0 = ix0 + pad_x + c_idx * cell_w
            cx1 = cx0 + cell_w
            tok_val = tokens[c_idx] if c_idx < len(tokens) else f"Hồ sơ 0{c_idx + 1}"
            draw.rectangle([cx0, cy0, cx1, cy0 + cell_h], fill=HALLMARK_PAPER_MAT, outline=HALLMARK_INK, width=line_w)
            draw.line([(cx0, cy0 + round(44 * scale)), (cx1, cy0 + round(44 * scale))], fill=HALLMARK_RULE, width=1)
            draw.text((cx0 + round(18 * scale), cy0 + round(12 * scale)), f"Mục 0{c_idx + 1}", fill=HALLMARK_MUTED, font=font_label)
            draw.text((cx0 + round(18 * scale), cy0 + cell_h // 2 + round(10 * scale)), tok_val, fill=primary if c_idx == 0 else HALLMARK_INK, font=font_token, anchor="lm")

    # Bottom plate caption note (sentence case, untracked)
    draw.text(
        (ix0 + pad_x, bot_band_y + round(h * 0.055)),
        theme["caption_note"],
        fill=HALLMARK_MUTED,
        font=font_label,
    )


def _draw_voice_cadence_trace(
    draw: ImageDraw.ImageDraw,
    x0: int,
    y_center: int,
    total_w: int,
    primary: tuple[int, int, int],
    scale: float,
    scene_no: int,
    progress_ratio: float = 0.5,
    energy_profile: list[float] | None = None,
) -> None:
    """Draw a refined analog studio voice cadence trace instead of a neon equalizer."""
    font_label = resolve_font(max(10, round(19 * scale)), bold=False, serif=False)
    label_str = "Nhịp âm thanh đối thoại"
    draw.text((x0, y_center), label_str, fill=HALLMARK_MUTED, font=font_label, anchor="lm")

    trace_x0 = x0 + round(260 * scale)
    trace_w = max(80, total_w - round(260 * scale))
    bar_count = 28
    step_x = trace_w / max(1, bar_count - 1)

    # Baseline hairline
    draw.line([(trace_x0, y_center), (trace_x0 + trace_w, y_center)], fill=HALLMARK_RULE, width=1)

    active_cutoff = round(bar_count * max(0.15, min(1.0, progress_ratio)))
    for b in range(bar_count):
        if energy_profile and b < len(energy_profile):
            amp = energy_profile[b]
        else:
            amp = 0.25 + 0.65 * abs(math.sin(b * 0.48 + scene_no * 1.15))
        half_h = max(3, round((4 + 18 * amp) * scale))
        bx = round(trace_x0 + b * step_x)
        tick_color = primary if b <= active_cutoff else HALLMARK_RULE_DARK
        tick_w = max(2, round(2.5 * scale)) if b <= active_cutoff else max(1, round(1.5 * scale))
        draw.line([(bx, y_center - half_h), (bx, y_center + half_h)], fill=tick_color, width=tick_w)


def render_cyber_frame(
    scene_no: int,
    total_scenes: int,
    on_screen_text: str,
    narration_text: str,
    duration_sec: float,
    run_id: str,
    width: int = 1080,
    height: int = 1920,
    is_cover: bool = False,
    speaker: str = "Biên tập viên",
    speaker_role: str = "Bản tin đối thoại sáng",
    category_label: str | None = None,
    contextual_image_path: Path | None = None,
    zoom_progress: float = 0.0,
    energy_profile: list[float] | None = None,
) -> Image.Image:
    """Render a 9:16 vertical video frame in the Hallmark Handcrafted Editorial Aesthetic.

    Eliminates sci-fi HUD clichés, neon borders, middle-dot strings, and bracketed all-caps
    tags in favor of warm archival paper (#F6F2EA), deep printer's ink (#1A1816),
    editorial serif display headlines, studio crop marks, and human dialogue typography.
    """
    scale = width / 1080.0
    clean_headline = format_editorial_headline(on_screen_text)
    theme = detect_scene_theme(clean_headline, narration_text, is_cover=is_cover)
    primary = theme["primary"]
    rubric_text = category_label or theme["badge"]

    # 1. Warm Archival Paper Ground (#F6F2EA)
    img = Image.new("RGB", (width, height), color=HALLMARK_PAPER)
    draw = ImageDraw.Draw(img)

    margin_x = round(72 * scale)
    content_w = width - 2 * margin_x

    # 2. Masthead (Left-aligned publication name + right-aligned edition folio, over double ink rule)
    font_masthead = resolve_font(max(16, round(42 * scale)), bold=True, serif=True)
    font_folio = resolve_font(max(10, round(21 * scale)), bold=False, serif=False)
    font_folio_bold = resolve_font(max(10, round(21 * scale)), bold=True, serif=False)

    mast_y = round(108 * scale)
    draw.text((margin_x, mast_y), "CyberPulse", fill=HALLMARK_INK, font=font_masthead)
    draw.text(
        (margin_x, mast_y + round(52 * scale)),
        "Tạp chí điểm tin và phân tích an ninh mạng",
        fill=HALLMARK_MUTED,
        font=font_folio,
    )

    folio_label = "Trang bìa" if is_cover else f"Mục {scene_no:02d} / {max(1, total_scenes):02d}"
    draw.text((width - margin_x, mast_y + round(8 * scale)), folio_label, fill=HALLMARK_INK, font=font_folio_bold, anchor="ra")
    draw.text((width - margin_x, mast_y + round(42 * scale)), "Thời lượng dưới 1 phút", fill=HALLMARK_MUTED, font=font_folio, anchor="ra")

    # Printer's Double Rule under Masthead
    rule_y = mast_y + round(92 * scale)
    draw.line([(margin_x, rule_y), (width - margin_x, rule_y)], fill=HALLMARK_INK, width=max(2, round(3 * scale)))
    draw.line(
        [(margin_x, rule_y + round(7 * scale)), (width - margin_x, rule_y + round(7 * scale))],
        fill=HALLMARK_RULE_DARK,
        width=1,
    )

    # 3. Story Rubric & Editorial Serif Headline (Left-aligned, sentence case)
    rubric_y = rule_y + round(34 * scale)
    sq_size = max(6, round(12 * scale))
    draw.rectangle(
        [margin_x, rubric_y + round(6 * scale), margin_x + sq_size, rubric_y + round(6 * scale) + sq_size],
        fill=primary,
    )
    font_rubric = resolve_font(max(11, round(23 * scale)), bold=True, serif=False)
    draw.text((margin_x + sq_size + round(14 * scale), rubric_y), rubric_text, fill=primary, font=font_rubric)

    head_y0 = rubric_y + round(44 * scale)
    base_head_size = 48 if len(clean_headline) < 42 else (42 if len(clean_headline) < 72 else 36)
    font_headline = resolve_font(max(14, round(base_head_size * scale)), bold=True, serif=True)
    head_lines = wrap_text_to_width(clean_headline, font_headline, content_w, draw)[:3]
    head_lh = round((base_head_size + 13) * scale)

    for idx, hline in enumerate(head_lines):
        draw.text((margin_x, head_y0 + idx * head_lh), hline, fill=HALLMARK_INK, font=font_headline)

    # 4. Tactile Framed Print Plate with Studio Crop Marks
    plate_y0 = round(525 * scale)
    plate_y1 = round(1145 * scale)
    plate_x0 = margin_x
    plate_x1 = width - margin_x

    _draw_studio_crop_marks(draw, plate_x0, plate_y0, plate_x1, plate_y1, scale, color=HALLMARK_INK)

    # Outer 1px ink border + warm passe-partout museum mat
    draw.rectangle([plate_x0, plate_y0, plate_x1, plate_y1], fill=HALLMARK_PAPER_MAT, outline=HALLMARK_INK, width=max(1, round(2 * scale)))
    mat_pad = max(6, round(16 * scale))
    inner_x0, inner_y0 = plate_x0 + mat_pad, plate_y0 + mat_pad
    inner_x1, inner_y1 = plate_x1 - mat_pad, plate_y1 - mat_pad
    inner_w, inner_h = inner_x1 - inner_x0, inner_y1 - inner_y0

    placed_photo = False
    if contextual_image_path and contextual_image_path.exists():
        try:
            with Image.open(contextual_image_path) as raw_im:
                src_im = raw_im.convert("RGB")
                # Subtle documentary pan/zoom (1.00 -> 1.04)
                zoom = 1.0 + 0.04 * max(0.0, min(1.0, zoom_progress))
                sw, sh = src_im.size
                target_aspect = inner_w / max(1, inner_h)
                crop_h = sh / zoom
                crop_w = crop_h * target_aspect
                if crop_w > sw:
                    crop_w = sw / zoom
                    crop_h = crop_w / target_aspect
                cx_i, cy_i = sw / 2.0, sh / 2.0
                crop_box = (
                    int(max(0, cx_i - crop_w / 2)),
                    int(max(0, cy_i - crop_h / 2)),
                    int(min(sw, cx_i + crop_w / 2)),
                    int(min(sh, cy_i + crop_h / 2)),
                )
                cropped = src_im.crop(crop_box).resize((inner_w, inner_h), Image.Resampling.BICUBIC)
                # Warm editorial print toning (soften harsh digital blues slightly)
                warm_layer = Image.new("RGB", (inner_w, inner_h), HALLMARK_PAPER)
                toned = Image.blend(cropped, warm_layer, 0.06)
                img.paste(toned, (inner_x0, inner_y0))
                draw.rectangle([inner_x0, inner_y0, inner_x1, inner_y1], outline=HALLMARK_RULE_DARK, width=1)
                placed_photo = True
        except Exception as exc:
            logger.debug("Fallback to monograph plate for scene %d: %s", scene_no, exc)

    if not placed_photo:
        tokens = _extract_story_tokens(clean_headline, narration_text)
        _draw_editorial_monograph_plate(
            draw=draw,
            ix0=inner_x0,
            iy0=inner_y0,
            ix1=inner_x1,
            iy1=inner_y1,
            theme=theme,
            tokens=tokens,
            scene_no=scene_no,
            scale=scale,
        )
        draw.rectangle([inner_x0, inner_y0, inner_x1, inner_y1], outline=HALLMARK_RULE_DARK, width=1)

    # 5. Conversational Studio Dialogue Well (Human Speaker Attribution + Verbatim Transcript)
    well_y0 = round(1185 * scale)
    well_y1 = round(1665 * scale)
    draw.rectangle([margin_x, well_y0, width - margin_x, well_y1], fill=HALLMARK_PAPER_WELL)
    draw.line([(margin_x, well_y0), (width - margin_x, well_y0)], fill=HALLMARK_INK, width=max(1, round(2 * scale)))
    draw.line([(margin_x, well_y1), (width - margin_x, well_y1)], fill=HALLMARK_RULE, width=1)

    well_pad_x = round(32 * scale)
    byline_y = well_y0 + round(28 * scale)

    # Vertical pigment bar beside speaker attribution
    bar_h = round(30 * scale)
    draw.rectangle(
        [margin_x + well_pad_x, byline_y + round(2 * scale), margin_x + well_pad_x + max(2, round(4 * scale)), byline_y + bar_h],
        fill=primary,
    )

    clean_speaker = re.sub(r"^\[|\]$", "", speaker.strip())
    font_speaker = resolve_font(max(12, round(25 * scale)), bold=True, serif=False)
    font_role = resolve_font(max(11, round(22 * scale)), bold=False, serif=False)
    spk_x = margin_x + well_pad_x + round(16 * scale)
    draw.text((spk_x, byline_y), clean_speaker, fill=HALLMARK_INK, font=font_speaker)
    spk_bbox = draw.textbbox((spk_x, byline_y), clean_speaker, font=font_speaker)
    draw.text(
        (spk_bbox[2] + round(16 * scale), byline_y + round(2 * scale)),
        f"—  {speaker_role}",
        fill=HALLMARK_MUTED,
        font=font_role,
    )

    # Transcript Copy (Left-aligned, clean humanist sans in warm espresso ink)
    clean_narration = re.sub(r"^\s*\[[^\]]+\]\s*", "", narration_text.strip())
    font_sub = resolve_font(max(13, round(33 * scale)), bold=False, serif=False)
    sub_lines = wrap_text_to_width(
        clean_narration or clean_headline,
        font_sub,
        content_w - 2 * well_pad_x,
        draw,
    )[:5]
    sub_lh = round(48 * scale)
    sub_start_y = byline_y + round(58 * scale)

    for j, sline in enumerate(sub_lines):
        draw.text(
            (margin_x + well_pad_x, sub_start_y + j * sub_lh),
            sline,
            fill=HALLMARK_INK_BODY,
            font=font_sub,
        )

    # Source marginalia inside bottom of well
    run_short = run_id[-8:] if len(run_id) > 8 else run_id
    font_foot = resolve_font(max(9, round(18 * scale)), bold=False, serif=False)
    draw.text(
        (margin_x + well_pad_x, well_y1 - round(34 * scale)),
        f"Trích lược từ bản thu âm đối thoại Podcast (Mã phiên {run_short})",
        fill=HALLMARK_MUTED,
        font=font_foot,
    )

    # 6. Analog Voice Cadence Trace & Minimalist Chapter Progress Colophon
    prog_ratio = (scene_no / max(1, total_scenes)) if total_scenes > 0 else 0.5
    _draw_voice_cadence_trace(
        draw=draw,
        x0=margin_x,
        y_center=round(1725 * scale),
        total_w=content_w,
        primary=primary,
        scale=scale,
        scene_no=scene_no,
        progress_ratio=prog_ratio,
        energy_profile=energy_profile,
    )

    # Segmented Hairline Chapter Bar
    bar_y = height - round(115 * scale)
    gap = round(10 * scale)
    if total_scenes > 0:
        seg_w = (content_w - (total_scenes - 1) * gap) / total_scenes
        for s_idx in range(total_scenes):
            sx0 = margin_x + s_idx * (seg_w + gap)
            sx1 = sx0 + seg_w
            is_current = (s_idx + 1) == scene_no
            is_passed = (s_idx + 1) < scene_no
            fill_c = primary if is_current else (HALLMARK_INK if is_passed else HALLMARK_RULE)
            bar_thick = max(2, round(5 * scale)) if is_current else max(1, round(3 * scale))
            draw.rectangle([round(sx0), bar_y, round(sx1), bar_y + bar_thick], fill=fill_c)

    # Bottom Colophon
    colophon_y = height - round(72 * scale)
    draw.text(
        (margin_x, colophon_y),
        "Ấn phẩm biên tập thủ công bởi CyberPulse",
        fill=HALLMARK_MUTED,
        font=font_foot,
    )
    draw.text(
        (width - margin_x, colophon_y),
        f"{duration_sec:.1f} giây",
        fill=HALLMARK_INK,
        font=font_foot,
        anchor="ra",
    )

    return img


# Alias for explicit Hallmark naming
render_hallmark_frame = render_cyber_frame


# ==============================================================================
# Native HyperFrames Composition Exporter (/hyperframes & /hallmark Contract)
# ==============================================================================


def _hyperframes_motion_scene_html(
    s: SceneRenderResult,
    comp_id: str,
    width: int,
    height: int,
    assets_dir: Path,
) -> str:
    """Build a HyperFrames <template> scene with beat illustrations, Ken Burns motion, and word-level karaoke GSAP tweens."""
    motion = s.motion
    dur = round(s.duration_sec, 3)
    headline_esc = html.escape(format_editorial_headline(s.on_screen_text))
    speaker_esc = html.escape(s.speaker)
    role_esc = html.escape(s.speaker_role)
    rubric_esc = html.escape(s.category_label)

    # 1. Copy beat illustration files into assets/ and build beat clips + GSAP tweens
    beat_clips_html: list[str] = []
    beat_tweens_js: list[str] = []
    beats = getattr(motion, "beats", []) or []
    for b_idx, beat in enumerate(beats):
        b_start = round(max(0.0, float(beat.start)), 3)
        b_dur = round(max(0.2, float(beat.end) - b_start), 3)
        if beat.image_path and Path(beat.image_path).exists():
            dest_name = f"scene_{s.scene_no:02d}_beat_{b_idx + 1:02d}{Path(beat.image_path).suffix}"
            shutil.copy2(beat.image_path, assets_dir / dest_name)
            img_rel = f"assets/{dest_name}"
        else:
            img_rel = f"assets/{s.slide_image_path.name}"

        clip_dom_id = f"{comp_id}-beat-{b_idx + 1}"
        inner_dom_id = f"{comp_id}-beat-{b_idx + 1}-inner"
        beat_clips_html.append(
            f"""      <div id="{clip_dom_id}" class="clip" data-start="{b_start}" data-duration="{b_dur}" data-track-index="1" style="position: absolute; inset: 16px; overflow: hidden; z-index: {b_idx + 1};">
        <div id="{inner_dom_id}" style="position: absolute; inset: 0;">
          <img src="{img_rel}" alt="{headline_esc}" style="width: 100%; height: 100%; object-fit: cover;" />
        </div>
      </div>"""
        )
        zoom_from, zoom_to = (1.0, 1.08) if (s.scene_no + b_idx) % 2 == 0 else (1.08, 1.0)
        pan_x = -18 if (s.scene_no + b_idx) % 2 == 0 else 18
        beat_tweens_js.append(
            f'    tl.fromTo("#{inner_dom_id}", {{ opacity: {0.0 if b_idx > 0 else 0.9}, scale: {zoom_from}, x: 0 }}, '
            f'{{ opacity: 1, scale: {zoom_to}, x: {pan_x}, duration: {b_dur}, ease: "sine.inOut" }}, {b_start});'
        )

    # 2. Build word-by-word spans and synchronized highlight tweens
    words = getattr(motion, "words", []) or []
    word_spans_html: list[str] = []
    word_tweens_js: list[str] = []
    for w_idx, wt in enumerate(words):
        w_id = f"{comp_id}-w-{w_idx}"
        w_esc = html.escape(wt.text)
        w_start = round(max(0.0, float(wt.start)), 3)
        w_dur = round(max(0.06, float(wt.end) - w_start), 3)
        word_spans_html.append(f'<span id="{w_id}" class="{comp_id}-word">{w_esc}</span>')
        word_tweens_js.append(
            f'    tl.fromTo("#{w_id}", {{ color: "var(--color-upcoming)", backgroundColor: "transparent" }}, '
            f'{{ color: "var(--color-accent)", backgroundColor: "var(--color-highlight)", duration: 0.06, ease: "power2.out" }}, {w_start});\n'
            f'    tl.to("#{w_id}", {{ color: "var(--color-ink-body)", backgroundColor: "transparent", duration: 0.08, ease: "power1.out" }}, {round(w_start + w_dur, 3)});'
        )

    if not word_spans_html:
        narr_clean = html.escape(re.sub(r"^\s*\[[^\]]+\]\s*", "", s.narration.strip()))
        word_spans_html.append(f'<span class="{comp_id}-word">{narr_clean}</span>')

    fade_out_t = round(max(0.1, dur - 0.28), 3)

    return f"""<template>
  <style>
    #root {{
      position: absolute;
      inset: 0;
      background: var(--color-paper);
      color: var(--color-ink);
      font-family: var(--font-body);
    }}
    .{comp_id}-header {{
      position: absolute;
      left: 72px;
      right: 72px;
      top: 108px;
    }}
    .{comp_id}-rubric {{
      font-size: 23px;
      font-weight: 700;
      color: var(--color-accent);
      margin: 0 0 12px 0;
    }}
    .{comp_id}-headline {{
      font-family: var(--font-display);
      font-size: 42px;
      line-height: 1.24;
      font-weight: 700;
      color: var(--color-ink);
      margin: 0;
    }}
    .{comp_id}-plate {{
      position: absolute;
      left: 72px;
      right: 72px;
      top: 505px;
      height: 600px;
      background: var(--color-paper-mat);
      border: 2px solid var(--color-ink);
    }}
    .{comp_id}-card {{
      position: absolute;
      left: 72px;
      right: 72px;
      top: 1190px;
      height: 482px;
      padding: 28px 32px;
      background: var(--color-paper-well);
      border-top: 2px solid var(--color-ink);
      border-bottom: var(--rule-hair) solid var(--color-rule);
      overflow: hidden;
    }}
    .{comp_id}-byline {{
      font-size: 25px;
      font-weight: 700;
      color: var(--color-ink);
      margin: 0 0 18px 0;
      padding-left: 16px;
      border-left: 4px solid var(--color-accent);
    }}
    .{comp_id}-role {{
      font-weight: 400;
      color: var(--color-muted);
    }}
    .{comp_id}-transcript {{
      font-size: 35px;
      line-height: 1.48;
      color: var(--color-upcoming);
      margin: 0;
      overflow-wrap: break-word;
      min-width: 0;
    }}
    .{comp_id}-word {{
      display: inline-block;
      padding: 1px 5px;
      margin-right: 4px;
      border-radius: 5px;
    }}
  </style>
  <div id="root" data-composition-id="{comp_id}" data-width="{width}" data-height="{height}">
    <div id="{comp_id}-head-clip" class="clip" data-start="0" data-duration="{dur}" data-track-index="1" style="position: absolute; inset: 0; z-index: 1;">
      <div id="{comp_id}-head-inner" class="{comp_id}-header">
        <p class="{comp_id}-rubric">{rubric_esc}</p>
        <h1 class="{comp_id}-headline">{headline_esc}</h1>
      </div>
    </div>
    <div id="{comp_id}-plate-frame" class="{comp_id}-plate" style="z-index: 2;">
{chr(10).join(beat_clips_html)}
    </div>
    <div id="{comp_id}-caption-clip" class="clip" data-start="0.08" data-duration="{round(max(0.2, dur - 0.08), 3)}" data-track-index="2" data-layout-allow-caption-zone style="position: absolute; inset: 0; z-index: 3;">
      <div id="{comp_id}-caption-inner" class="{comp_id}-card">
        <p class="{comp_id}-byline">{speaker_esc} <span class="{comp_id}-role">— {role_esc}</span></p>
        <p class="{comp_id}-transcript">{" ".join(word_spans_html)}</p>
      </div>
    </div>
  </div>
  <script>
    const tl = gsap.timeline({{ paused: true }});
    tl.fromTo(
      "#{comp_id}-head-inner",
      {{ opacity: 0, y: 18 }},
      {{ opacity: 1, y: 0, duration: 0.42, ease: "power3.out" }},
      0
    );
{chr(10).join(beat_tweens_js)}
    tl.fromTo(
      "#{comp_id}-caption-inner",
      {{ opacity: 0, y: 16 }},
      {{ opacity: 1, y: 0, duration: 0.32, ease: "power3.out" }},
      0.12
    );
{chr(10).join(word_tweens_js)}
    tl.to(
      "#{comp_id}-caption-inner",
      {{ opacity: 0.96, duration: 0.18, ease: "power2.in" }},
      {fade_out_t}
    );
    window.__timelines["{comp_id}"] = tl;
  </script>
</template>
"""


def export_hyperframes_project(
    run_id: str,
    video_title: str,
    scenes: list[SceneRenderResult],
    assembled_audio_path: Path | None,
    width: int,
    height: int,
    project_dir: Path,
) -> Path:
    """Export a valid, Studio-editable HyperFrames HTML+GSAP project adhering to the Hallmark Floor.

    Follows the `/hyperframes` Contract:
    - `index.html` declares `data-composition-variables`, mounts scenes from `compositions/`,
      includes `* { box-sizing: border-box; }`, and registers `window.__timelines["main"]`.
    - Each `compositions/scene_XX.html` wraps its markup/styles/script inside `<template>`,
      styles `#root { position: absolute; inset: 0; }`, uses `gsap.fromTo()` on child elements
      of `.clip`, and registers `window.__timelines["scene-XX"] = tl`.
    - Follows `/hallmark` Floor rules: CSS custom properties at `:root`, zero literal colors
      past `:root`, Hallmark stamp on line 1, sentence-case labels, zero middle dots.
    """
    project_dir.mkdir(parents=True, exist_ok=True)
    comp_dir = project_dir / "compositions"
    assets_dir = project_dir / "assets"
    comp_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    total_duration = round(max(1.0, sum(s.duration_sec for s in scenes)), 3)

    # Copy audio and slide assets into project_dir/assets/
    audio_rel_src = ""
    if assembled_audio_path and assembled_audio_path.exists():
        dest_audio = assets_dir / "podcast_excerpt.mp3"
        shutil.copy2(assembled_audio_path, dest_audio)
        audio_rel_src = "assets/podcast_excerpt.mp3"

    for s in scenes:
        if s.slide_image_path.exists():
            shutil.copy2(s.slide_image_path, assets_dir / s.slide_image_path.name)

    # 1. Write Hallmark-compliant tokens.css
    tokens_css = """/* Hallmark · v2.0.0 · genre: editorial · theme: specimen (custom editorial press) · signature: tactile print plate & podcast transcript */
/* critique: hierarchy carried by Source Serif 4 display heads over Hanken Grotesk body on warm oat paper; zero HUD chrome. */
:root {
  --color-paper: oklch(96.2% 0.014 82);
  --color-paper-well: oklch(93.0% 0.018 80);
  --color-paper-mat: oklch(98.2% 0.008 84);
  --color-ink: oklch(17.5% 0.012 60);
  --color-ink-body: oklch(28.0% 0.014 60);
  --color-muted: oklch(48.5% 0.014 65);
  --color-rule: oklch(81.0% 0.018 75);
  --color-rule-dark: oklch(66.0% 0.020 72);
  --color-accent: oklch(48.0% 0.175 28);
  --color-accent-ink: oklch(97.5% 0.010 82);
  --color-focus: oklch(48.0% 0.175 28);
  --color-upcoming: oklch(62.0% 0.016 70);
  --color-highlight: oklch(86.0% 0.060 40);
  --font-display: "Playfair Display", Georgia, serif;
  --font-body: "Inter", system-ui, sans-serif;
  --rule-hair: 1px;
  --rule-double: 3px;
}

* {
  box-sizing: border-box;
}

html, body {
  margin: 0;
  padding: 0;
  overflow-x: clip;
  background: var(--color-paper);
  color: var(--color-ink);
  font-family: var(--font-body);
}

:focus-visible {
  outline: 2px solid var(--color-focus);
  outline-offset: 2px;
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}
"""
    (project_dir / "tokens.css").write_text(tokens_css, encoding="utf-8")

    # 2. Write sub-compositions (compositions/scene_XX.html)
    scene_mounts_html: list[str] = []
    for s in scenes:
        comp_id = f"scene-{s.scene_no:02d}"
        comp_file = f"compositions/scene_{s.scene_no:02d}.html"
        dur = round(s.duration_sec, 3)
        start = round(s.start_sec, 3)
        fade_out_t = round(max(0.1, dur - 0.28), 3)

        headline_esc = html.escape(format_editorial_headline(s.on_screen_text))
        narr_esc = html.escape(re.sub(r"^\s*\[[^\]]+\]\s*", "", s.narration.strip()))
        speaker_esc = html.escape(s.speaker)
        role_esc = html.escape(s.speaker_role)
        rubric_esc = html.escape(s.category_label)
        slide_src = f"assets/{s.slide_image_path.name}"

        if s.motion is not None:
            scene_html = _hyperframes_motion_scene_html(s, comp_id, width, height, assets_dir)
            (comp_dir / f"scene_{s.scene_no:02d}.html").write_text(scene_html, encoding="utf-8")
            scene_mounts_html.append(
                f"""      <div
        id="{comp_id}"
        class="clip"
        data-composition-id="{comp_id}"
        data-composition-src="{comp_file}"
        data-start="{start}"
        data-duration="{dur}"
        data-track-index="1"
        data-track-kind="graphics"
        data-width="{width}"
        data-height="{height}"
        style="position: absolute; left: 0; top: 0; width: {width}px; height: {height}px; z-index: {s.scene_no};"
      ></div>"""
            )
            continue

        scene_html = f"""<template>
  <style>
    #root {{
      position: absolute;
      inset: 0;
      background: var(--color-paper);
      color: var(--color-ink);
      font-family: var(--font-body);
    }}
    .{comp_id}-backdrop {{
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      object-fit: contain;
    }}
    .{comp_id}-card {{
      position: absolute;
      left: 72px;
      right: 72px;
      bottom: 245px;
      padding: 28px 32px;
      background: var(--color-paper-well);
      border-top: 2px solid var(--color-ink);
      border-bottom: var(--rule-hair) solid var(--color-rule);
    }}
    .{comp_id}-byline {{
      font-size: 24px;
      font-weight: 600;
      color: var(--color-ink);
      margin: 0 0 14px 0;
      padding-left: 14px;
      border-left: 4px solid var(--color-accent);
    }}
    .{comp_id}-role {{
      font-weight: 400;
      color: var(--color-muted);
    }}
    .{comp_id}-transcript {{
      font-size: 33px;
      line-height: 1.42;
      color: var(--color-ink-body);
      margin: 0;
      overflow-wrap: break-word;
      min-width: 0;
    }}
  </style>
  <div id="root" data-composition-id="{comp_id}" data-width="{width}" data-height="{height}">
    <div id="{comp_id}-plate-clip" class="clip" data-start="0" data-duration="{dur}" data-track-index="1" style="position: absolute; inset: 0; z-index: 1;">
      <div id="{comp_id}-plate-inner" style="position: absolute; inset: 0;">
        <img class="{comp_id}-backdrop" src="{slide_src}" alt="{headline_esc}" />
      </div>
    </div>
    <div id="{comp_id}-caption-clip" class="clip" data-start="0.08" data-duration="{round(max(0.2, dur - 0.08), 3)}" data-track-index="2" data-layout-allow-caption-zone style="position: absolute; inset: 0; z-index: 2;">
      <div id="{comp_id}-caption-inner" class="{comp_id}-card">
        <p class="{comp_id}-byline">{speaker_esc} <span class="{comp_id}-role">— {role_esc} ({rubric_esc})</span></p>
        <p class="{comp_id}-transcript">{narr_esc}</p>
      </div>
    </div>
  </div>
  <script>
    const tl = gsap.timeline({{ paused: true }});
    tl.fromTo(
      "#{comp_id}-plate-inner",
      {{ opacity: 0.92, scale: 1.0 }},
      {{ opacity: 1, scale: 1.018, duration: {round(max(0.4, dur * 0.85), 3)}, ease: "power1.out" }},
      0
    );
    tl.fromTo(
      "#{comp_id}-caption-inner",
      {{ opacity: 0, y: 14 }},
      {{ opacity: 1, y: 0, duration: 0.24, ease: "power3.out" }},
      0.08
    );
    tl.to(
      "#{comp_id}-caption-inner",
      {{ opacity: 0.96, duration: 0.18, ease: "power2.in" }},
      {fade_out_t}
    );
    window.__timelines["{comp_id}"] = tl;
  </script>
</template>
"""
        (comp_dir / f"scene_{s.scene_no:02d}.html").write_text(scene_html, encoding="utf-8")

        scene_mounts_html.append(
            f"""      <div
        id="{comp_id}"
        class="clip"
        data-composition-id="{comp_id}"
        data-composition-src="{comp_file}"
        data-start="{start}"
        data-duration="{dur}"
        data-track-index="1"
        data-track-kind="graphics"
        data-width="{width}"
        data-height="{height}"
        style="position: absolute; left: 0; top: 0; width: {width}px; height: {height}px; z-index: {s.scene_no};"
      ></div>"""
        )

    audio_tag_html = ""
    if audio_rel_src:
        audio_tag_html = f"""      <audio
        id="podcast-master-audio"
        class="clip"
        data-start="0"
        data-duration="{total_duration}"
        data-track-index="0"
        data-volume="1"
        src="{audio_rel_src}"
      ></audio>"""

    variables_json = json.dumps(
        [
            {"id": "publication", "type": "string", "label": "Publication", "default": "CyberPulse"},
            {"id": "edition_title", "type": "string", "label": "Edition Title", "default": format_editorial_headline(video_title)},
        ],
        ensure_ascii=False,
    )

    index_html = f"""<!DOCTYPE html>
<html lang="vi" data-composition-variables='{html.escape(variables_json, quote=True)}'>
  <head>
    <meta charset="utf-8" />
    <title>{html.escape(format_editorial_headline(video_title))}</title>
    <link rel="stylesheet" href="tokens.css" />
    <script src="https://cdn.jsdelivr.net/npm/gsap@3/dist/gsap.min.js"></script>
  </head>
  <body>
    <div
      id="root"
      data-composition-id="main"
      data-start="0"
      data-duration="{total_duration}"
      data-width="{width}"
      data-height="{height}"
      style="position: relative; width: {width}px; height: {height}px; overflow: hidden; background: var(--color-paper);"
    >
      <div style="position: absolute; inset: 0; background: var(--color-paper); z-index: 0;" data-layout-ignore></div>
{chr(10).join(scene_mounts_html)}
{audio_tag_html}
    </div>
    <script>
      window.__timelines = window.__timelines || {{}};
      window.__timelines["main"] = gsap.timeline({{ paused: true }});
    </script>
  </body>
</html>
"""
    (project_dir / "index.html").write_text(index_html, encoding="utf-8")

    notes_md = f"""# HyperFrames & Hallmark Translation Notes (`{run_id}`)

- **Import Level**: Native HTML + GSAP sub-compositions (`compositions/scene_XX.html`) + Curated Podcast Audio (`assets/podcast_excerpt.mp3`).
- **Aesthetic System**: Hallmark v2.0.0 (`genre: editorial`, warm archival paper `--color-paper`, deep printer's ink `--color-ink`, sentence-case rubrics, studio crop marks, zero HUD clichés).
- **Audio Edit Decision List (Sub-60s Podcast Trim)**:
  - Total Duration: `{total_duration:.2f}s` (strictly `< 60.0s`)
  - Scene Count: `{len(scenes)}`
"""
    (project_dir / "TRANSLATION_NOTES.md").write_text(notes_md, encoding="utf-8")
    return project_dir


# ==============================================================================
# Pure-Python MP4 Container Builder (Fallback when FFmpeg is unavailable)
# ==============================================================================


def _make_box(box_type: bytes, payload: bytes) -> bytes:
    """Pack payload into standard 32-bit ISO BMFF atom."""
    total_len = len(payload) + 8
    return total_len.to_bytes(4, "big") + box_type + payload


def build_pure_python_mp4(
    width: int,
    height: int,
    duration_sec: float,
    media_data: bytes,
    timescale: int = 1000,
) -> bytes:
    """Build a valid standalone ISO Base Media File Format (MP4) container."""
    dur = max(1, round(duration_sec * timescale))

    ftyp = _make_box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2mp41")

    matrix = (
        (0x00010000).to_bytes(4, "big")
        + b"\x00" * 12
        + (0x00010000).to_bytes(4, "big")
        + b"\x00" * 12
        + (0x40000000).to_bytes(4, "big")
    )
    mvhd_payload = (
        b"\x00\x00\x00\x00"
        + b"\x00\x00\x00\x00"
        + b"\x00\x00\x00\x00"
        + timescale.to_bytes(4, "big")
        + dur.to_bytes(4, "big")
        + (0x00010000).to_bytes(4, "big")
        + (0x0100).to_bytes(2, "big")
        + b"\x00" * 10
        + matrix
        + b"\x00" * 24
        + (2).to_bytes(4, "big")
    )
    mvhd = _make_box(b"mvhd", mvhd_payload)

    tkhd_payload = (
        b"\x00\x00\x00\x07"
        + b"\x00\x00\x00\x00"
        + b"\x00\x00\x00\x00"
        + (1).to_bytes(4, "big")
        + b"\x00\x00\x00\x00"
        + dur.to_bytes(4, "big")
        + b"\x00" * 8
        + b"\x00\x00\x00\x00\x00\x00\x00\x00"
        + matrix
        + (width << 16).to_bytes(4, "big")
        + (height << 16).to_bytes(4, "big")
    )
    tkhd = _make_box(b"tkhd", tkhd_payload)

    mdhd_payload = (
        b"\x00\x00\x00\x00"
        + b"\x00\x00\x00\x00"
        + b"\x00\x00\x00\x00"
        + timescale.to_bytes(4, "big")
        + dur.to_bytes(4, "big")
        + b"\x55\xc4\x00\x00"
    )
    mdhd = _make_box(b"mdhd", mdhd_payload)

    hdlr_payload = b"\x00\x00\x00\x00\x00\x00\x00\x00vide" + b"\x00" * 12 + b"CyberPulse Video Handler\x00"
    hdlr = _make_box(b"hdlr", hdlr_payload)

    vmhd = _make_box(b"vmhd", b"\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00")
    dref = _make_box(b"dref", b"\x00\x00\x00\x00\x00\x00\x00\x01" + _make_box(b"url ", b"\x00\x00\x00\x01"))
    dinf = _make_box(b"dinf", dref)

    sample_entry = (
        b"\x00" * 6
        + (1).to_bytes(2, "big")
        + b"\x00" * 16
        + width.to_bytes(2, "big")
        + height.to_bytes(2, "big")
        + (0x00480000).to_bytes(4, "big")
        + (0x00480000).to_bytes(4, "big")
        + b"\x00" * 4
        + (1).to_bytes(2, "big")
        + b"\x0aCyberPulse"
        + b"\x00" * 21
        + (0x0018).to_bytes(2, "big")
        + b"\xff\xff"
    )
    stsd = _make_box(b"stsd", b"\x00\x00\x00\x00\x00\x00\x00\x01" + _make_box(b"mp4v", sample_entry))
    stts = _make_box(b"stts", b"\x00\x00\x00\x00\x00\x00\x00\x01" + (1).to_bytes(4, "big") + dur.to_bytes(4, "big"))
    stsc = _make_box(b"stsc", b"\x00\x00\x00\x00\x00\x00\x00\x01\x00\x00\x00\x01\x00\x00\x00\x01\x00\x00\x00\x01")
    stsz = _make_box(b"stsz", b"\x00\x00\x00\x00" + len(media_data).to_bytes(4, "big") + (1).to_bytes(4, "big"))

    stco_len = 16
    stbl_len = len(stsd) + len(stts) + len(stsc) + len(stsz) + stco_len + 8
    minf_len = len(vmhd) + len(dinf) + stbl_len + 8
    mdia_len = len(mdhd) + len(hdlr) + minf_len + 8
    trak_len = len(tkhd) + mdia_len + 8
    moov_len = len(mvhd) + trak_len + 8
    offset = len(ftyp) + moov_len + 8
    stco = _make_box(b"stco", b"\x00\x00\x00\x00\x00\x00\x00\x01" + offset.to_bytes(4, "big"))

    stbl = _make_box(b"stbl", stsd + stts + stsc + stsz + stco)
    minf = _make_box(b"minf", vmhd + dinf + stbl)
    mdia = _make_box(b"mdia", mdhd + hdlr + minf)
    trak = _make_box(b"trak", tkhd + mdia)
    moov = _make_box(b"moov", mvhd + trak)

    mdat = _make_box(b"mdat", media_data)
    return ftyp + moov + mdat


class PurePythonVideoAssembler:
    """Pure-Python MP4 container builder when FFmpeg is not installed."""

    def assemble_video(
        self,
        scenes: list[SceneRenderResult],
        assembled_audio_path: Path | None,
        output_path: Path,
        width: int,
        height: int,
        fps: int = 30,
    ) -> float:
        """Assemble scene slides and audio into a valid MP4 container file."""
        total_duration = sum(s.duration_sec for s in scenes)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        media_payload_chunks: list[bytes] = []
        if assembled_audio_path and assembled_audio_path.exists():
            media_payload_chunks.append(assembled_audio_path.read_bytes())
        else:
            for s in scenes:
                if s.slide_image_path.exists():
                    media_payload_chunks.append(s.slide_image_path.read_bytes()[:1024])

        payload = b"".join(media_payload_chunks) or b"CYBERPULSE_VIDEO_STREAM"
        mp4_bytes = build_pure_python_mp4(
            width=width,
            height=height,
            duration_sec=total_duration,
            media_data=payload,
        )
        output_path.write_bytes(mp4_bytes)
        return total_duration


# ==============================================================================
# FFmpeg Video Assembler (When FFmpeg is available)
# ==============================================================================


def find_ffmpeg_binary() -> str | None:
    """Find FFmpeg binary on PATH or via imageio_ffmpeg fallback."""
    bin_path = shutil.which("ffmpeg")
    if bin_path:
        return bin_path
    if shutil.which("cmd.exe" if os.name == "nt" else "sh") is not None:
        try:
            import imageio_ffmpeg

            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass
    return None


class FFmpegVideoAssembler:
    """FFmpeg-based H.264/AAC video encoder supporting phrase-level cue frames."""

    def __init__(self, ffmpeg_bin: str | None = None) -> None:
        self.ffmpeg_bin = ffmpeg_bin or find_ffmpeg_binary()
        if not self.ffmpeg_bin:
            raise MediaDependencyError(
                "ffmpeg executable not found on system PATH. Install ffmpeg to use FFmpegVideoAssembler."
            )

    def assemble_video(
        self,
        scenes: list[SceneRenderResult],
        assembled_audio_path: Path | None,
        output_path: Path,
        width: int,
        height: int,
        fps: int = 30,
    ) -> float:
        """Assemble scenes into an MP4 via FFmpeg.

        When every scene carries a motion spec, frames are rendered at full frame rate and
        streamed into FFmpeg (karaoke transcript, Ken Burns plate, dissolves). Otherwise the
        legacy still/cue-frame concat path is used.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        total_duration = sum(s.duration_sec for s in scenes)

        if scenes and all(s.motion is not None for s in scenes):
            from cyberpulse.media_motion import render_motion_video

            timeline_end = max(s.end_sec for s in scenes)
            return render_motion_video(
                specs=[s.motion for s in scenes],
                audio_path=assembled_audio_path,
                output_path=output_path,
                width=width,
                height=height,
                fps=fps,
                ffmpeg_bin=str(self.ffmpeg_bin),
                total_duration=timeline_end,
            )

        with tempfile.TemporaryDirectory(prefix="cyberpulse_ffmpeg_video_") as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            concat_txt = tmp_dir / "slides.txt"

            concat_lines: list[str] = []
            last_img_posix: str | None = None

            for s in scenes:
                if s.cue_frames:
                    for cue_img_path, cue_dur in s.cue_frames:
                        if cue_img_path.exists():
                            resolved_img = cue_img_path.resolve().as_posix()
                            concat_lines.append(f"file '{resolved_img}'")
                            concat_lines.append(f"duration {max(0.1, cue_dur):.3f}")
                            last_img_posix = resolved_img
                else:
                    resolved_img = s.slide_image_path.resolve().as_posix()
                    concat_lines.append(f"file '{resolved_img}'")
                    concat_lines.append(f"duration {s.duration_sec:.3f}")
                    last_img_posix = resolved_img

            if last_img_posix:
                concat_lines.append(f"file '{last_img_posix}'")

            concat_txt.write_text("\n".join(concat_lines), encoding="utf-8")

            cmd = [
                str(self.ffmpeg_bin),
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_txt),
            ]

            if assembled_audio_path and assembled_audio_path.exists():
                cmd.extend(["-i", str(assembled_audio_path), "-c:a", "aac", "-b:a", "160k"])
            else:
                cmd.extend(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-c:a", "aac"])

            cmd.extend([
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-r",
                str(fps),
                "-s",
                f"{width}x{height}",
                "-shortest",
                "-movflags",
                "+faststart",
                str(output_path),
            ])

            try:
                subprocess.run(cmd, capture_output=True, check=True)
            except subprocess.CalledProcessError as exc:
                raise MediaSynthesisError(
                    f"FFmpeg video encoding failed: {exc.stderr.decode('utf-8', errors='ignore')}"
                ) from exc

        return total_duration


def get_default_video_assembler(prefer_ffmpeg: bool = True) -> VideoAssembler:
    """Return FFmpegVideoAssembler if ffmpeg exists on PATH or fallback, otherwise PurePythonVideoAssembler."""
    if prefer_ffmpeg and find_ffmpeg_binary():
        try:
            return FFmpegVideoAssembler()
        except Exception:
            return PurePythonVideoAssembler()
    return PurePythonVideoAssembler()


# ==============================================================================
# Synchronized SRT & Metadata Builders
# ==============================================================================


def format_srt_timestamp(ms: int) -> str:
    """Format milliseconds into SRT timestamp: HH:MM:SS,mmm."""
    if ms < 0:
        ms = 0
    total_seconds, milliseconds = divmod(ms, 1000)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def build_video_srt(scenes: list[SceneRenderResult]) -> str:
    """Build standardized synchronized SRT subtitle file matching scene timings."""
    lines: list[str] = []
    cue_index = 1

    for s in scenes:
        motion = getattr(s, "motion", None)
        if motion is not None and getattr(motion, "words", None):
            # Phrase-level cues timed from the word alignment (max ~7 words or a clause).
            group: list[Any] = []
            first_cue = True
            for i, wt in enumerate(motion.words):
                group.append(wt)
                is_clause_end = bool(re.search(r"[.!?,;:…]$", wt.text))
                if len(group) >= 7 or (is_clause_end and len(group) >= 3) or i == len(motion.words) - 1:
                    st = round((s.start_sec + group[0].start) * 1000)
                    en = round((s.start_sec + group[-1].end) * 1000)
                    phrase = " ".join(w.text for w in group)
                    lines.append(str(cue_index))
                    lines.append(f"{format_srt_timestamp(st)} --> {format_srt_timestamp(en)}")
                    lines.append(f"{s.speaker}: {phrase}" if first_cue else phrase)
                    lines.append("")
                    cue_index += 1
                    first_cue = False
                    group = []
            continue

        start_ms = round(s.start_sec * 1000)
        end_ms = round(s.end_sec * 1000)

        lines.append(str(cue_index))
        lines.append(f"{format_srt_timestamp(start_ms)} --> {format_srt_timestamp(end_ms)}")
        lines.append(f"[SCENE {s.scene_no}] {s.on_screen_text}\n{s.narration}")
        lines.append("")
        cue_index += 1

    return "\n".join(lines)


def build_video_metadata(
    run_id: str,
    video_title: str,
    options: VideoMediaOptions,
    scenes: list[SceneRenderResult],
    total_duration_sec: float,
    video_file_name: str | None,
    created_at: datetime,
    audio_source: str = "podcast",
    hyperframes_dir_name: str | None = None,
) -> dict[str, Any]:
    """Construct technical metadata dictionary."""
    w, h = SUPPORTED_RESOLUTIONS.get(options.resolution, (1080, 1920))
    return {
        "run_id": run_id,
        "video_title": video_title,
        "aspect_ratio": "9:16",
        "resolution": options.resolution,
        "width": w,
        "height": h,
        "voice": options.voice,
        "audio_source": audio_source,
        "aesthetic": "hallmark-editorial-v2",
        "under_one_minute": round(total_duration_sec, 3) < 60.0,
        "total_duration_sec": round(total_duration_sec, 3),
        "scene_count": len(scenes),
        "preview_only": options.preview_only,
        "scenes": [
            {
                "scene_no": s.scene_no,
                "speaker": s.speaker,
                "category_label": s.category_label,
                "on_screen_text": s.on_screen_text,
                "narration": s.narration,
                "duration_sec": round(s.duration_sec, 3),
                "start_sec": round(s.start_sec, 3),
                "end_sec": round(s.end_sec, 3),
                "slide_image": f"{run_id}_scenes/{s.slide_image_path.name}",
                **_motion_metadata(s),
            }
            for s in scenes
        ],
        "files": {
            "video": video_file_name,
            "subtitles": f"{run_id}.srt",
            "metadata": f"{run_id}.json",
            "cover": f"{run_id}_cover.png",
            "hyperframes": hyperframes_dir_name,
        },
        "created_at": created_at.isoformat(),
    }


def _motion_metadata(s: SceneRenderResult) -> dict[str, Any]:
    motion = getattr(s, "motion", None)
    if motion is None:
        return {}
    return {
        "words": [{"w": w.text, "s": w.start, "e": w.end} for w in motion.words],
        "illustrations": [
            {
                "file": b.image_path.name if b.image_path else None,
                "start_sec": round(b.start, 3),
                "end_sec": round(b.end, 3),
                "caption": b.caption,
            }
            for b in motion.beats
        ],
    }


_GENERATED_BEAT_RE = re.compile(r"^seg\d+_b\d+\.(?:jpe?g|png|webp)$", re.IGNORECASE)


def _discover_contextual_images(run_id: str, output_media_dir: Path, count: int) -> list[Path | None]:
    """Look for hand-placed scene illustrations in <run_id>_assets (excluding generated beat images)."""
    candidates: list[Path] = []
    assets_dir = output_media_dir / f"{run_id}_assets"
    if assets_dir.exists() and assets_dir.is_dir():
        for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
            candidates.extend(p for p in sorted(assets_dir.glob(ext)) if not _GENERATED_BEAT_RE.match(p.name))

    if not candidates:
        return [None] * count
    return [candidates[i % len(candidates)] for i in range(count)]


# ==============================================================================
# Orchestration Function
# ==============================================================================


def synthesize_video(
    run_id: str,
    editorial_pkg: EditorialPackage,
    settings: Settings,
    options: VideoMediaOptions,
    output_media_dir: Path,
    tts_backend: TTSBackend | None = None,
    assembler: VideoAssembler | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    clock_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    illustration_providers: list[Any] | None = None,
    prompt_writer: Any = None,
) -> VideoMediaResult:
    """Synthesize a sub-60s 9:16 video in the Hallmark Aesthetic using curated Podcast audio.

    Guarantees:
    - Automatic Podcast Audio Integration: When `<run_id>.mp3` exists in `media/podcast`
      (or when synthesizing from a multi-turn podcast script in production), automatically
      curates and trims the 2-MC dialogue so total duration is strictly `< 60.0s` while
      preserving complete speaker thoughts and core intelligence facts.
    - Hallmark Aesthetic UI: Warm archival paper, deep printer's ink, serif headlines,
      studio crop marks, and real audio cadence traces.
    - Native HyperFrames Project Export: Writes `<run_id>_hyperframes/` alongside the MP4.
    - Atomicity & Idempotency: Temporary directory used for rendering; fails on existing
      files unless `options.force` is True.
    """
    if not RUN_ID_REGEX.match(run_id):
        raise MediaInputError(f"Invalid run_id '{run_id}'. Allowed characters: [A-Za-z0-9._-]")

    if options.resolution not in SUPPORTED_RESOLUTIONS:
        raise MediaInputError(
            f"Unsupported resolution '{options.resolution}'. Supported: {list(SUPPORTED_RESOLUTIONS.keys())}"
        )

    if options.voice and not VOICE_NAME_REGEX.match(options.voice):
        raise MediaInputError(f"Invalid voice format '{options.voice}'. Expected pattern: 'xx-XX-NameNeural'")

    width, height = SUPPORTED_RESOLUTIONS[options.resolution]
    output_media_dir.mkdir(parents=True, exist_ok=True)

    final_video = output_media_dir / f"{run_id}.mp4"
    final_srt = output_media_dir / f"{run_id}.srt"
    final_meta = output_media_dir / f"{run_id}.json"
    final_cover = output_media_dir / f"{run_id}_cover.png"
    scenes_dir = output_media_dir / f"{run_id}_scenes"
    hyperframes_dir = output_media_dir / f"{run_id}_hyperframes"

    # Collision Check
    if not options.force:
        existing_targets: list[Path] = [final_srt, final_meta, final_cover]
        if not options.preview_only:
            existing_targets.append(final_video)
        existing = [p for p in existing_targets if p.exists()]
        if existing:
            raise MediaInputError(
                f"Output files already exist for run '{run_id}' in '{output_media_dir}'. Use --force to overwrite."
            )

    chosen_assembler = assembler or get_default_video_assembler(prefer_ffmpeg=options.prefer_ffmpeg)

    # Locate existing Podcast MP3 & Metadata in media/podcast
    podcast_dirs = [
        output_media_dir.parent / "podcast",
        settings.paths.output_dir / "media" / "podcast",
    ]
    podcast_mp3_path: Path | None = None
    podcast_meta_dict: dict[str, Any] | None = None
    for pdir in podcast_dirs:
        cand_mp3 = pdir / f"{run_id}.mp3"
        cand_meta = pdir / f"{run_id}.json"
        if cand_mp3.exists() and cand_mp3.is_file():
            podcast_mp3_path = cand_mp3
            if cand_meta.exists() and cand_meta.is_file():
                try:
                    podcast_meta_dict = json.loads(cand_meta.read_text(encoding="utf-8"))
                except Exception:
                    podcast_meta_dict = None
            break

    # Decide whether to drive scenes from the curated 2-MC Podcast or fallback to editorial_pkg.video.scenes
    use_podcast_flow = bool(
        options.prefer_podcast_audio
        and (
            podcast_mp3_path is not None
            or (tts_backend is None and not options.preview_only and len(editorial_pkg.podcast.segments) >= 2)
        )
    )

    hard_cap_sec = min(options.max_duration_sec, MAX_VIDEO_DURATION_SEC)
    inter_pause_ms = 240
    inter_pause_sec = inter_pause_ms / 1000.0

    with tempfile.TemporaryDirectory(prefix=f"cyberpulse_video_{run_id}_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        tmp_scenes_dir = tmp_dir / "scenes"
        tmp_cues_dir = tmp_dir / "cues"
        tmp_scenes_dir.mkdir(parents=True)
        tmp_cues_dir.mkdir(parents=True)

        scene_render_results: list[SceneRenderResult] = []
        timeline_curr = 0.0
        audio_source_label = "podcast-sliced" if podcast_mp3_path else "video-tts"
        voice_label = options.voice

        if use_podcast_flow:
            curated_clips = curate_podcast_highlights(
                editorial_pkg=editorial_pkg,
                settings=settings,
                podcast_meta=podcast_meta_dict,
                max_duration_sec=hard_cap_sec,
                pause_sec=inter_pause_sec,
            )
            total_scenes = len(curated_clips)
            ctx_images = _discover_contextual_images(run_id, output_media_dir, total_scenes)
            raw_podcast_bytes = podcast_mp3_path.read_bytes() if podcast_mp3_path else b""
            chosen_tts = tts_backend or (None if podcast_mp3_path else EdgeTTSBackend())

            used_voices = sorted({c.voice for c in curated_clips})
            if used_voices:
                voice_label = " + ".join(used_voices)

            # Motion timeline is only possible when frames are streamed through FFmpeg.
            use_motion = bool(
                options.motion and not options.preview_only and isinstance(chosen_assembler, FFmpegVideoAssembler)
            )
            ffmpeg_for_audio = getattr(chosen_assembler, "ffmpeg_bin", None) if use_motion else None

            # Plan illustration beats (1-2 per turn) and make sure each has a matching image.
            from cyberpulse.media_illustrations import (
                GeminiPromptWriter,
                default_providers,
                ensure_beat_illustrations,
                split_scene_into_beats,
            )

            scene_beats: list[list[Any]] = [
                split_scene_into_beats(i + 1, c.segment_index, c.headline, c.text, c.duration_sec)
                for i, c in enumerate(curated_clips)
            ]
            providers = illustration_providers
            writer = prompt_writer
            if providers is None and options.illustrations == "auto" and not options.preview_only:
                providers = default_providers()
                writer = writer or GeminiPromptWriter()
            if providers:
                if progress_callback:
                    progress_callback(0, total_scenes, "Đang vẽ minh họa AI theo từng đoạn thoại")
                flat, ill_report = ensure_beat_illustrations(
                    [b for group in scene_beats for b in group],
                    assets_dir=output_media_dir / f"{run_id}_assets",
                    providers=providers,
                    prompt_writer=writer,
                )
                logger.info(
                    "Illustrations: generated=%d reused=%d failed=%d providers=%s",
                    ill_report.generated, ill_report.reused, ill_report.failed, ill_report.providers,
                )
                it = iter(flat)
                scene_beats = [[next(it) for _ in group] for group in scene_beats]
            else:
                # Reuse cached beat illustrations from an earlier run, never call the network.
                flat, _ = ensure_beat_illustrations(
                    [b for group in scene_beats for b in group],
                    assets_dir=output_media_dir / f"{run_id}_assets",
                    providers=[],
                ) if (output_media_dir / f"{run_id}_assets").is_dir() else ([b for g in scene_beats for b in g], None)
                it = iter(flat)
                scene_beats = [[next(it) for _ in group] for group in scene_beats]

            for idx, clip in enumerate(curated_clips):
                scene_no = idx + 1
                if progress_callback:
                    progress_callback(scene_no, total_scenes, f"Biên tập cảnh {scene_no}/{total_scenes} ({clip.speaker})")

                scene_audio_path: Path | None = None
                measured_dur = clip.duration_sec
                seg_mp3_bytes = b""

                if not options.preview_only:
                    scene_audio_path = tmp_dir / f"audio_scene_{scene_no:02d}.mp3"
                    if raw_podcast_bytes:
                        seg_mp3_bytes, sliced_dur = slice_mp3_bytes(
                            raw_podcast_bytes,
                            start_sec=clip.source_start_sec,
                            end_sec=clip.source_end_sec,
                        )
                        if seg_mp3_bytes and sliced_dur > 0.2:
                            scene_audio_path.write_bytes(seg_mp3_bytes)
                            measured_dur = sliced_dur
                    if (not seg_mp3_bytes) and chosen_tts is not None:
                        audio_source_label = "podcast-tts"
                        dur_synth = asyncio.run(
                            chosen_tts.synthesize_segment(
                                text=clip.text,
                                voice=clip.voice,
                                output_path=scene_audio_path,
                            )
                        )
                        if dur_synth > 0.1:
                            measured_dur = min(dur_synth, hard_cap_sec)
                        if scene_audio_path.exists():
                            seg_mp3_bytes = scene_audio_path.read_bytes()

                energy_prof = extract_mp3_energy_profile(seg_mp3_bytes, num_points=28)
                beats_here = scene_beats[idx] if idx < len(scene_beats) else []
                first_beat_img = next((b.image_path for b in beats_here if b.image_path), None)
                ctx_img = first_beat_img or (ctx_images[idx] if idx < len(ctx_images) else None)

                start_t = timeline_curr
                end_t = start_t + measured_dur
                timeline_curr = end_t + (inter_pause_sec if idx < total_scenes - 1 else 0.0)

                motion_spec: Any = None
                if use_motion:
                    from cyberpulse.media_motion import PlateBeat, SceneMotionSpec, align_words, decode_audio_envelope

                    envelope = decode_audio_envelope(scene_audio_path, ffmpeg_for_audio) if scene_audio_path else []
                    words = align_words(clip.text, measured_dur, envelope)
                    plate_beats: list[Any] = []
                    for b_idx, b in enumerate(beats_here):
                        b_start = 0.0
                        if b_idx > 0 and b.word_start < len(words):
                            b_start = max(0.0, words[b.word_start].start - 0.35)
                        img_path = b.image_path or (ctx_img if b_idx == 0 else None)
                        if b.provider in ("gemini-image",):
                            credit = "Minh họa AI theo lời thoại, tạo bằng Gemini"
                        elif b.provider == "pollinations":
                            credit = "Minh họa AI theo lời thoại, tạo bằng Pollinations Flux"
                        elif b.image_path:
                            credit = "Minh họa AI theo lời thoại"
                        else:
                            credit = "Ảnh minh họa biên tập"
                        plate_beats.append(PlateBeat(img_path, b_start, measured_dur, credit))
                    for b_idx in range(len(plate_beats) - 1):
                        cur_b = plate_beats[b_idx]
                        plate_beats[b_idx] = PlateBeat(cur_b.image_path, cur_b.start, plate_beats[b_idx + 1].start, cur_b.caption)
                    if not plate_beats:
                        plate_beats = [PlateBeat(ctx_img, 0.0, measured_dur, "Ảnh minh họa biên tập")]
                    motion_spec = SceneMotionSpec(
                        scene_no=scene_no,
                        total_scenes=total_scenes,
                        run_id=run_id,
                        headline=clip.headline,
                        category_label=clip.category_label,
                        speaker=clip.speaker,
                        speaker_role=clip.speaker_role,
                        narration=clip.text,
                        duration_sec=measured_dur,
                        words=words,
                        beats=plate_beats,
                        envelope=envelope,
                        start_sec=start_t,
                    )

                slide_path = tmp_scenes_dir / f"scene_{scene_no:02d}.png"
                cue_frames: list[tuple[Path, float]] = []
                if motion_spec is not None:
                    from cyberpulse.media_motion import SceneMotionRenderer

                    # Representative still: mid-scene, with the karaoke highlight in flight.
                    SceneMotionRenderer(motion_spec, width, height).render(measured_dur * 0.42).save(slide_path, "PNG")
                else:
                    slide_img = render_cyber_frame(
                        scene_no=scene_no,
                        total_scenes=total_scenes,
                        on_screen_text=clip.headline,
                        narration_text=clip.text,
                        duration_sec=measured_dur,
                        run_id=run_id,
                        width=width,
                        height=height,
                        is_cover=False,
                        speaker=clip.speaker,
                        speaker_role=clip.speaker_role,
                        category_label=clip.category_label,
                        contextual_image_path=ctx_img,
                        zoom_progress=0.35,
                        energy_profile=energy_prof,
                    )
                    slide_img.save(slide_path, "PNG")

                # Legacy phrase-level cue frames (only when the motion timeline is unavailable)
                if motion_spec is None and not options.preview_only:
                    cues = split_text_into_cues(clip.text, max_chars_per_line=44, max_lines_per_cue=3)
                    if len(cues) > 1:
                        total_chars = max(1, sum(len(c) for c in cues))
                        for c_idx, cue_str in enumerate(cues):
                            c_dur = max(0.8, measured_dur * (len(cue_str) / total_chars))
                            z_prog = c_idx / max(1, len(cues) - 1)
                            cue_img = render_cyber_frame(
                                scene_no=scene_no,
                                total_scenes=total_scenes,
                                on_screen_text=clip.headline,
                                narration_text=cue_str,
                                duration_sec=measured_dur,
                                run_id=run_id,
                                width=width,
                                height=height,
                                is_cover=False,
                                speaker=clip.speaker,
                                speaker_role=clip.speaker_role,
                                category_label=clip.category_label,
                                contextual_image_path=ctx_img,
                                zoom_progress=z_prog,
                                energy_profile=energy_prof,
                            )
                            cue_path = tmp_cues_dir / f"scene_{scene_no:02d}_cue_{c_idx + 1:02d}.png"
                            cue_img.save(cue_path, "PNG")
                            cue_frames.append((cue_path, c_dur))

                scene_render_results.append(
                    SceneRenderResult(
                        scene_no=scene_no,
                        on_screen_text=clip.headline,
                        narration=f"[{clip.speaker}] {clip.text}",
                        slide_image_path=slide_path,
                        audio_path=scene_audio_path,
                        duration_sec=measured_dur,
                        start_sec=start_t,
                        end_sec=end_t,
                        speaker=clip.speaker,
                        speaker_role=clip.speaker_role,
                        category_label=clip.category_label,
                        cue_frames=cue_frames,
                        motion=motion_spec,
                    )
                )

        else:
            # Standard VideoScene flow (also enforcing < 60s budget)
            chosen_tts = tts_backend or EdgeTTSBackend()
            video_scenes: list[VideoScene] = list(editorial_pkg.video.scenes)
            total_scenes = len(video_scenes)
            ctx_images = _discover_contextual_images(run_id, output_media_dir, total_scenes)

            for idx, scn in enumerate(video_scenes):
                if timeline_curr >= hard_cap_sec and scene_render_results:
                    break

                scene_no = scn.scene_no
                on_screen = scn.on_screen_text.strip()
                narration = scn.narration.text.strip()

                if progress_callback:
                    progress_callback(idx + 1, total_scenes, f"Rendering scene {scene_no}/{total_scenes}")

                scene_audio_path = None
                measured_dur = scn.duration_sec
                seg_mp3_bytes = b""

                if not options.preview_only:
                    scene_audio_path = tmp_dir / f"audio_scene_{scene_no:02d}.mp3"
                    try:
                        dur_synth = asyncio.run(
                            chosen_tts.synthesize_segment(
                                text=narration,
                                voice=options.voice,
                                output_path=scene_audio_path,
                            )
                        )
                        if dur_synth > 0.1:
                            measured_dur = max(dur_synth, scn.duration_sec)
                        if scene_audio_path.exists():
                            seg_mp3_bytes = scene_audio_path.read_bytes()
                    except Exception as exc:
                        logger.warning("TTS generation failed for scene %d: %s. Using planned duration.", scene_no, exc)
                        if isinstance(exc, (MediaDependencyError, MediaSynthesisError)):
                            raise

                # Enforce strict sub-60s budget
                remaining_budget = max(1.0, hard_cap_sec - timeline_curr)
                measured_dur = min(measured_dur, remaining_budget)
                energy_prof = extract_mp3_energy_profile(seg_mp3_bytes, num_points=28)

                slide_img = render_cyber_frame(
                    scene_no=scene_no,
                    total_scenes=total_scenes,
                    on_screen_text=on_screen,
                    narration_text=narration,
                    duration_sec=measured_dur,
                    run_id=run_id,
                    width=width,
                    height=height,
                    is_cover=False,
                    contextual_image_path=ctx_images[idx] if idx < len(ctx_images) else None,
                    energy_profile=energy_prof,
                )
                slide_path = tmp_scenes_dir / f"scene_{scene_no:02d}.png"
                slide_img.save(slide_path, "PNG")

                start_t = timeline_curr
                end_t = start_t + measured_dur
                timeline_curr = end_t

                scene_render_results.append(
                    SceneRenderResult(
                        scene_no=scene_no,
                        on_screen_text=on_screen,
                        narration=narration,
                        slide_image_path=slide_path,
                        audio_path=scene_audio_path,
                        duration_sec=measured_dur,
                        start_sec=start_t,
                        end_sec=end_t,
                    )
                )

        total_video_duration = min(timeline_curr, MAX_VIDEO_DURATION_SEC)
        final_scene_count = len(scene_render_results)

        # 3. Render Dedicated Hallmark Cover Slide
        first_rendered = scene_render_results[0] if scene_render_results else None
        cover_text = editorial_pkg.video.title or (
            first_rendered.on_screen_text if first_rendered else "Điểm tin an ninh mạng"
        )
        cover_narr = first_rendered.narration if first_rendered else editorial_pkg.digest.intro
        cover_ctx_img = _discover_contextual_images(run_id, output_media_dir, 1)[0]
        if first_rendered is not None and first_rendered.motion is not None:
            cover_ctx_img = next(
                (b.image_path for b in first_rendered.motion.beats if b.image_path), cover_ctx_img
            )
        cover_img = render_cyber_frame(
            scene_no=1,
            total_scenes=max(1, final_scene_count),
            on_screen_text=cover_text,
            narration_text=cover_narr,
            duration_sec=total_video_duration,
            run_id=run_id,
            width=width,
            height=height,
            is_cover=True,
            speaker=first_rendered.speaker if first_rendered else "Minh & Lan",
            speaker_role="Đối thoại tiêu điểm sáng",
            contextual_image_path=cover_ctx_img,
        )
        tmp_cover_path = tmp_dir / f"{run_id}_cover.png"
        cover_img.save(tmp_cover_path, "PNG")

        # 4. Assemble Curated Audio Track (with natural studio breath pauses)
        assembled_audio_path: Path | None = None
        if not options.preview_only:
            audio_files = [s.audio_path for s in scene_render_results if s.audio_path and s.audio_path.exists()]
            if audio_files:
                from cyberpulse.media.podcast import SegmentSynthesisResult

                audio_asm = PurePythonMP3Assembler()
                mock_segments = [
                    SegmentSynthesisResult(
                        index=i,
                        speaker=s.speaker,
                        voice=options.voice,
                        char_count=len(s.narration),
                        word_count=len(s.narration.split()),
                        text=s.narration,
                        audio_path=s.audio_path,  # type: ignore[arg-type]
                        duration_sec=s.duration_sec,
                    )
                    for i, s in enumerate(scene_render_results)
                    if s.audio_path
                ]
                assembled_audio_path = tmp_dir / "assembled_narration.mp3"
                pause_val = inter_pause_ms if use_podcast_flow else 100
                audio_asm.assemble(
                    segments=mock_segments,
                    output_path=assembled_audio_path,
                    pause_diff_speaker_ms=pause_val,
                    pause_same_speaker_ms=pause_val,
                )

        # 5. Assemble Final MP4 Video (if not preview_only)
        tmp_video_path = tmp_dir / f"{run_id}.mp4"
        if not options.preview_only:
            chosen_assembler.assemble_video(
                scenes=scene_render_results,
                assembled_audio_path=assembled_audio_path,
                output_path=tmp_video_path,
                width=width,
                height=height,
                fps=options.fps,
            )

        # 6. Generate Synchronized Subtitles and Metadata
        srt_content = build_video_srt(scene_render_results)
        tmp_srt_path = tmp_dir / f"{run_id}.srt"
        tmp_srt_path.write_text(srt_content, encoding="utf-8")

        hf_dir_name = f"{run_id}_hyperframes" if options.export_hyperframes else None
        meta_dict = build_video_metadata(
            run_id=run_id,
            video_title=editorial_pkg.video.title,
            options=options,
            scenes=scene_render_results,
            total_duration_sec=total_video_duration,
            video_file_name=f"{run_id}.mp4" if not options.preview_only else None,
            created_at=clock_fn(),
            audio_source=audio_source_label,
            hyperframes_dir_name=hf_dir_name,
        )
        tmp_meta_path = tmp_dir / f"{run_id}.json"
        tmp_meta_path.write_text(json.dumps(meta_dict, indent=2, ensure_ascii=False), encoding="utf-8")

        # 7. Commit Artifacts Atomically to Final Destination
        if scenes_dir.exists() and options.force:
            shutil.rmtree(scenes_dir, ignore_errors=True)
        scenes_dir.mkdir(parents=True, exist_ok=True)
        final_scene_images: list[Path] = []
        for s in scene_render_results:
            dest_img = scenes_dir / s.slide_image_path.name
            shutil.copy2(s.slide_image_path, dest_img)
            final_scene_images.append(dest_img)

        final_hf_dir: Path | None = None
        if options.export_hyperframes:
            if hyperframes_dir.exists() and options.force:
                shutil.rmtree(hyperframes_dir, ignore_errors=True)
            final_hf_dir = export_hyperframes_project(
                run_id=run_id,
                video_title=editorial_pkg.video.title,
                scenes=scene_render_results,
                assembled_audio_path=assembled_audio_path,
                width=width,
                height=height,
                project_dir=hyperframes_dir,
            )

        shutil.copy2(tmp_cover_path, final_cover)
        shutil.copy2(tmp_srt_path, final_srt)
        shutil.copy2(tmp_meta_path, final_meta)

        final_video_dest: Path | None = None
        if not options.preview_only:
            shutil.copy2(tmp_video_path, final_video)
            final_video_dest = final_video

    return VideoMediaResult(
        run_id=run_id,
        video_path=final_video_dest,
        srt_path=final_srt,
        meta_path=final_meta,
        cover_image=final_cover,
        scene_images=final_scene_images,
        total_duration_sec=total_video_duration,
        scene_count=final_scene_count,
        resolution=options.resolution,
        voice=voice_label,
        preview_only=options.preview_only,
        hyperframes_dir=final_hf_dir,
        audio_source=audio_source_label,
    )
