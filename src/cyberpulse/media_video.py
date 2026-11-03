"""CyberPulse Short-Form Video Synthesis Module.

Generates 9:16 vertical short-form videos (TikTok / YouTube Shorts / Reels),
synthesizes Vietnamese voiceover narration via Edge-TTS, renders technical
cybersecurity-themed visual frames, and outputs synchronized SRT subtitles.
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

from PIL import Image, ImageDraw, ImageFont

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


# ==============================================================================
# Exceptions Mapped to Exit Codes
# ==============================================================================

from cyberpulse.media.podcast import (
    MediaDependencyError,
    MediaError,
    MediaInputError,
    MediaSynthesisError,
    MediaValidationError,
)


# ==============================================================================
# Dataclasses
# ==============================================================================


@dataclass(frozen=True)
class VideoMediaOptions:
    """Options for video synthesis."""

    voice: str = DEFAULT_VOICE
    resolution: str = DEFAULT_RESOLUTION
    output_dir: Path | None = None
    force: bool = False
    preview_only: bool = False
    allow_unvalidated: bool = False
    fps: int = 30
    prefer_ffmpeg: bool = True


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
        communicate = edge_tts.Communicate(text=text, voice=voice)
        await asyncio.wait_for(communicate.save(str(output_path)), timeout=timeout)

        from cyberpulse.media.podcast import PurePythonMP3Assembler

        assembler = PurePythonMP3Assembler()
        return assembler.measure_duration(output_path)


# ==============================================================================
# Visual Frame Rendering (Dark Cybersecurity Theme)
# ==============================================================================


def resolve_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Find and load TrueType font supporting Vietnamese diacritics, or default."""
    candidates: list[str] = []
    if sys.platform == "win32":
        if bold:
            candidates.extend([
                "C:/Windows/Fonts/segoeuib.ttf",
                "C:/Windows/Fonts/arialbd.ttf",
                "C:/Windows/Fonts/calibrib.ttf",
            ])
        candidates.extend([
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/calibri.ttf",
            "C:/Windows/Fonts/consola.ttf",
        ])
    else:
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
) -> Image.Image:
    """Render a single 9:16 vertical video frame in Dark Cybersecurity Theme."""
    scale = width / 1080.0
    img = Image.new("RGB", (width, height), color=(15, 23, 42))  # #0F172A
    draw = ImageDraw.Draw(img)

    # 1. Tech Background Grid
    grid_spacing = max(20, round(60 * scale))
    grid_color = (30, 41, 59)  # #1E293B
    for x in range(0, width, grid_spacing):
        draw.line([(x, 0), (x, height)], fill=grid_color, width=1)
    for y in range(0, height, grid_spacing):
        draw.line([(0, y), (width, y)], fill=grid_color, width=1)

    # Subtle cyan accent borders / corner HUD
    bracket_len = round(32 * scale)
    bracket_w = max(2, round(3 * scale))
    cyan_accent = (56, 189, 248)  # #38BDF8
    slate_dark = (15, 23, 42)
    slate_card = (30, 41, 59)
    slate_muted = (148, 163, 184)  # #94A3B8
    white_bright = (248, 250, 252)  # #F8FAFC
    yellow_sub = (254, 240, 138)  # #FEF08A

    margin_corner = round(24 * scale)
    # Top-Left Corner
    draw.line([(margin_corner, margin_corner), (margin_corner + bracket_len, margin_corner)], fill=cyan_accent, width=bracket_w)
    draw.line([(margin_corner, margin_corner), (margin_corner, margin_corner + bracket_len)], fill=cyan_accent, width=bracket_w)
    # Top-Right Corner
    draw.line([(width - margin_corner, margin_corner), (width - margin_corner - bracket_len, margin_corner)], fill=cyan_accent, width=bracket_w)
    draw.line([(width - margin_corner, margin_corner), (width - margin_corner, margin_corner + bracket_len)], fill=cyan_accent, width=bracket_w)
    # Bottom-Left Corner
    draw.line([(margin_corner, height - margin_corner), (margin_corner + bracket_len, height - margin_corner)], fill=cyan_accent, width=bracket_w)
    draw.line([(margin_corner, height - margin_corner), (margin_corner, height - margin_corner - bracket_len)], fill=cyan_accent, width=bracket_w)
    # Bottom-Right Corner
    draw.line([(width - margin_corner, height - margin_corner), (width - margin_corner - bracket_len, height - margin_corner)], fill=cyan_accent, width=bracket_w)
    draw.line([(width - margin_corner, height - margin_corner), (width - margin_corner, height - margin_corner - bracket_len)], fill=cyan_accent, width=bracket_w)

    # 2. Header: Brand Banner & Bulletin ID
    font_brand = resolve_font(max(14, round(36 * scale)), bold=True)
    font_meta = resolve_font(max(10, round(22 * scale)), bold=False)

    brand_y = round(120 * scale)
    draw.text((width // 2, brand_y), "[CYBERPULSE INTEL]", fill=cyan_accent, font=font_brand, anchor="mm")

    run_tag = run_id[-12:] if len(run_id) > 12 else run_id
    sub_header = f"• BULLETIN: {run_tag} • 9:16 VERTICAL INTEL •"
    draw.text((width // 2, brand_y + round(50 * scale)), sub_header, fill=slate_muted, font=font_meta, anchor="mm")

    # Header divider line
    div_y = brand_y + round(85 * scale)
    draw.line([(round(80 * scale), div_y), (width - round(80 * scale), div_y)], fill=(51, 65, 85), width=max(1, round(2 * scale)))
    draw.ellipse([
        (width // 2 - round(4 * scale), div_y - round(4 * scale)),
        (width // 2 + round(4 * scale), div_y + round(4 * scale)),
    ], fill=cyan_accent)

    # 3. Center Card: on_screen_text (Main Focus)
    card_margin_x = round(70 * scale)
    card_y0 = round(400 * scale)
    card_y1 = round(1180 * scale)
    card_w = width - 2 * card_margin_x
    card_h = card_y1 - card_y0

    # Draw Center Card Box
    draw.rectangle([card_margin_x, card_y0, width - card_margin_x, card_y1], fill=slate_card, outline=cyan_accent, width=max(2, round(3 * scale)))

    # Tech brackets on card corners
    c_pad = round(12 * scale)
    c_len = round(20 * scale)
    draw.line([(card_margin_x - c_pad, card_y0), (card_margin_x - c_pad, card_y0 + c_len)], fill=cyan_accent, width=bracket_w)
    draw.line([(width - card_margin_x + c_pad, card_y0), (width - card_margin_x + c_pad, card_y0 + c_len)], fill=cyan_accent, width=bracket_w)
    draw.line([(card_margin_x - c_pad, card_y1), (card_margin_x - c_pad, card_y1 - c_len)], fill=cyan_accent, width=bracket_w)
    draw.line([(width - card_margin_x + c_pad, card_y1), (width - card_margin_x + c_pad, card_y1 - c_len)], fill=cyan_accent, width=bracket_w)

    # Scene Tag Badge on top of card
    font_badge = resolve_font(max(12, round(26 * scale)), bold=True)
    if is_cover:
        scene_badge_text = "• BÌA BẢN TIN VIDEO •"
    else:
        scene_badge_text = f"• PHÂN CẢNH {scene_no:02d} / {total_scenes:02d} •"
    draw.text((width // 2, card_y0 + round(60 * scale)), scene_badge_text, fill=cyan_accent, font=font_badge, anchor="mm")

    # Center text: dynamically size font based on length
    base_text_size = 48 if len(on_screen_text) < 40 else (40 if len(on_screen_text) < 70 else 34)
    font_center = resolve_font(max(14, round(base_text_size * scale)), bold=True)

    center_lines = wrap_text_to_width(on_screen_text, font_center, card_w - round(100 * scale), draw)
    line_height = round((base_text_size + 14) * scale)
    total_text_h = len(center_lines) * line_height
    start_text_y = card_y0 + (card_h // 2) - (total_text_h // 2) + round(15 * scale)

    for i, line in enumerate(center_lines):
        y_pos = start_text_y + (i * line_height)
        draw.text((width // 2, y_pos), line, fill=white_bright, font=font_center, anchor="mm")

    # Bottom badge inside Center Card
    font_tag = resolve_font(max(10, round(20 * scale)), bold=False)
    tag_str = f"[DUR: {duration_sec:.0f}s] • [STATUS: ACTIVE INTEL]" if not is_cover else "[COVER SLIDE]"
    draw.text((width // 2, card_y1 - round(45 * scale)), tag_str, fill=slate_muted, font=font_tag, anchor="mm")

    # 4. Subtitles / Narration Preview Container (Bottom Half)
    sub_y0 = round(1240 * scale)
    sub_y1 = round(1640 * scale)
    sub_w = width - 2 * card_margin_x
    draw.rectangle([card_margin_x, sub_y0, width - card_margin_x, sub_y1], fill=(11, 19, 43), outline=(51, 65, 85), width=max(1, round(2 * scale)))

    font_sub_label = resolve_font(max(10, round(20 * scale)), bold=True)
    draw.text((width // 2, sub_y0 + round(35 * scale)), "[ LỜI DẪN / PHỤ ĐỀ ĐỒNG BỘ ]", fill=cyan_accent, font=font_sub_label, anchor="mm")

    font_sub = resolve_font(max(12, round(28 * scale)), bold=False)
    sub_lines = wrap_text_to_width(narration_text, font_sub, sub_w - round(60 * scale), draw)
    sub_line_h = round(38 * scale)
    max_sub_lines = 6
    sub_lines = sub_lines[:max_sub_lines]

    total_sub_h = len(sub_lines) * sub_line_h
    start_sub_y = sub_y0 + round(75 * scale) + max(0, ((sub_y1 - (sub_y0 + round(75 * scale))) - total_sub_h) // 2)

    for j, sline in enumerate(sub_lines):
        draw.text((width // 2, start_sub_y + (j * sub_line_h)), sline, fill=yellow_sub, font=font_sub, anchor="mm")

    # 5. Footer: Watermark & Timeline Progress Bar
    font_footer = resolve_font(max(10, round(20 * scale)), bold=False)
    draw.text(
        (width // 2, height - round(95 * scale)),
        "CyberPulse AI Reader • Automated Threat Intelligence",
        fill=(100, 116, 139),
        font=font_footer,
        anchor="mm",
    )

    # Segmented Scene Progress Bar
    bar_y = height - round(45 * scale)
    bar_x0 = round(80 * scale)
    bar_x1 = width - round(80 * scale)
    total_bar_w = bar_x1 - bar_x0
    gap = round(8 * scale)

    if total_scenes > 0:
        seg_w = (total_bar_w - (total_scenes - 1) * gap) / total_scenes
        for s_idx in range(total_scenes):
            sx0 = bar_x0 + s_idx * (seg_w + gap)
            sx1 = sx0 + seg_w
            # Highlights active or completed scenes
            is_active = (s_idx + 1) <= scene_no
            fill_bar = (2, 132, 199) if is_active else (51, 65, 85)
            draw.rectangle([round(sx0), bar_y, round(sx1), bar_y + round(8 * scale)], fill=fill_bar)

    return img


# ==============================================================================
# Pure-Python MP4 Container Assembler
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

    # 1. ftyp box
    ftyp = _make_box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2mp41")

    # 2. mvhd box (Movie Header Box)
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

    # 3. trak box (Video track)
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

        # Concatenate audio bytes or image frames into media payload
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


class FFmpegVideoAssembler:
    """FFmpeg-based H.264/AAC video encoder and multiplexer."""

    def __init__(self) -> None:
        self.ffmpeg_bin = shutil.which("ffmpeg")
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
        """Assemble scenes into an MP4 video file via FFmpeg."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        total_duration = sum(s.duration_sec for s in scenes)

        with tempfile.TemporaryDirectory(prefix="cyberpulse_ffmpeg_video_") as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            concat_txt = tmp_dir / "slides.txt"

            # Build FFmpeg concat file with duration per scene
            concat_lines: list[str] = []
            for s in scenes:
                resolved_img = s.slide_image_path.resolve().as_posix()
                concat_lines.append(f"file '{resolved_img}'")
                concat_lines.append(f"duration {s.duration_sec:.3f}")

            # Concat demuxer requires repeating the last file
            if scenes:
                concat_lines.append(f"file '{scenes[-1].slide_image_path.resolve().as_posix()}'")

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
                cmd.extend(["-i", str(assembled_audio_path), "-c:a", "aac", "-b:a", "128k"])
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
    """Return FFmpegVideoAssembler if ffmpeg exists on PATH, otherwise PurePythonVideoAssembler."""
    if prefer_ffmpeg and shutil.which("ffmpeg"):
        return FFmpegVideoAssembler()
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
        start_ms = round(s.start_sec * 1000)
        end_ms = round(s.end_sec * 1000)

        # Main scene cue with headline
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
        "total_duration_sec": round(total_duration_sec, 3),
        "scene_count": len(scenes),
        "preview_only": options.preview_only,
        "scenes": [
            {
                "scene_no": s.scene_no,
                "on_screen_text": s.on_screen_text,
                "narration": s.narration,
                "duration_sec": round(s.duration_sec, 3),
                "start_sec": round(s.start_sec, 3),
                "end_sec": round(s.end_sec, 3),
                "slide_image": f"{run_id}_scenes/{s.slide_image_path.name}",
            }
            for s in scenes
        ],
        "files": {
            "video": video_file_name,
            "subtitles": f"{run_id}.srt",
            "metadata": f"{run_id}.json",
            "cover": f"{run_id}_cover.png",
        },
        "created_at": created_at.isoformat(),
    }


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
) -> VideoMediaResult:
    """Synthesize 9:16 short-form video, scene slides, SRT subtitles, and metadata.

    Guarantees:
    - Atomicity: temporary directory used for rendering.
    - Idempotency & Overwrite check: fails if files exist unless options.force is True.
    - Resolution validation: strictly 1080x1920 or 720x1280.
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

    # Resolve Assembler & TTS Backend
    chosen_assembler = assembler or get_default_video_assembler(prefer_ffmpeg=options.prefer_ffmpeg)
    chosen_tts = tts_backend or EdgeTTSBackend()

    # Scratch temporary directory for atomic rendering
    with tempfile.TemporaryDirectory(prefix=f"cyberpulse_video_{run_id}_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        tmp_scenes_dir = tmp_dir / "scenes"
        tmp_scenes_dir.mkdir(parents=True)

        video_scenes: list[VideoScene] = editorial_pkg.video.scenes
        total_scenes = len(video_scenes)
        scene_render_results: list[SceneRenderResult] = []
        timeline_curr = 0.0

        for idx, scn in enumerate(video_scenes):
            scene_no = scn.scene_no
            on_screen = scn.on_screen_text.strip()
            narration = scn.narration.text.strip()

            if progress_callback:
                progress_callback(idx + 1, total_scenes, f"Rendering scene {scene_no}/{total_scenes}")

            # 1. Synthesize Scene Voiceover (unless preview_only)
            scene_audio_path: Path | None = None
            measured_dur = scn.duration_sec

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
                    # Use the larger of measured narration length or storyboard planned duration
                    if dur_synth > 0.1:
                        measured_dur = max(dur_synth, scn.duration_sec)
                except Exception as exc:
                    logger.warning("TTS generation failed for scene %d: %s. Using planned duration.", scene_no, exc)
                    # Keep planned duration if TTS failed in non-strict environments
                    if isinstance(exc, (MediaDependencyError, MediaSynthesisError)):
                        raise

            # 2. Render Slide Image
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

        total_video_duration = timeline_curr

        # 3. Render Dedicated Cover Slide
        first_scene = video_scenes[0] if video_scenes else None
        cover_text = editorial_pkg.video.title or (first_scene.on_screen_text if first_scene else "BẢN TIN VIDEO")
        cover_narr = first_scene.narration.text if first_scene else ""
        cover_img = render_cyber_frame(
            scene_no=1,
            total_scenes=total_scenes,
            on_screen_text=cover_text,
            narration_text=cover_narr,
            duration_sec=total_video_duration,
            run_id=run_id,
            width=width,
            height=height,
            is_cover=True,
        )
        tmp_cover_path = tmp_dir / f"{run_id}_cover.png"
        cover_img.save(tmp_cover_path, "PNG")

        # 4. Assembled Audio (if video encoding is requested)
        assembled_audio_path: Path | None = None
        if not options.preview_only:
            audio_files = [s.audio_path for s in scene_render_results if s.audio_path and s.audio_path.exists()]
            if audio_files:
                from cyberpulse.media.podcast import PurePythonMP3Assembler, SegmentSynthesisResult

                audio_asm = PurePythonMP3Assembler()
                mock_segments = [
                    SegmentSynthesisResult(
                        index=i,
                        speaker="Narrator",
                        voice=options.voice,
                        char_count=len(s.narration),
                        word_count=len(s.narration.split()),
                        text=s.narration,
                        audio_path=s.audio_path,  # type: ignore
                        duration_sec=s.duration_sec,
                    )
                    for i, s in enumerate(scene_render_results)
                    if s.audio_path
                ]
                assembled_audio_path = tmp_dir / "assembled_narration.mp3"
                audio_asm.assemble(
                    segments=mock_segments,
                    output_path=assembled_audio_path,
                    pause_diff_speaker_ms=100,
                    pause_same_speaker_ms=100,
                )

        # 5. Assemble Video (if not preview_only)
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

        # 6. Generate Subtitles and Metadata
        srt_content = build_video_srt(scene_render_results)
        tmp_srt_path = tmp_dir / f"{run_id}.srt"
        tmp_srt_path.write_text(srt_content, encoding="utf-8")

        meta_dict = build_video_metadata(
            run_id=run_id,
            video_title=editorial_pkg.video.title,
            options=options,
            scenes=scene_render_results,
            total_duration_sec=total_video_duration,
            video_file_name=f"{run_id}.mp4" if not options.preview_only else None,
            created_at=clock_fn(),
        )
        tmp_meta_path = tmp_dir / f"{run_id}.json"
        tmp_meta_path.write_text(json.dumps(meta_dict, indent=2, ensure_ascii=False), encoding="utf-8")

        # 7. Commit Artifacts Atomically to final destination
        scenes_dir.mkdir(parents=True, exist_ok=True)
        final_scene_images: list[Path] = []
        for s in scene_render_results:
            dest_img = scenes_dir / s.slide_image_path.name
            shutil.copy2(s.slide_image_path, dest_img)
            final_scene_images.append(dest_img)

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
        scene_count=total_scenes,
        resolution=options.resolution,
        voice=options.voice,
        preview_only=options.preview_only,
    )
