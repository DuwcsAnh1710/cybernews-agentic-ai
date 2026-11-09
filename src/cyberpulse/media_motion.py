"""Frame-accurate motion renderer for the CyberPulse 9:16 video brief.

Replaces the old "one still per caption cue" slideshow with a real 30 fps timeline that is
streamed straight into FFmpeg:

* **Karaoke transcript** - every spoken word is timed against the actual podcast audio
  (PCM envelope pauses anchor sentence/clause boundaries, syllable weights fill the gaps),
  so the highlighter follows the hosts' voices instead of a guessed cue length.
* **Illustration plate** - AI illustrations for each beat drift with a slow Ken Burns move,
  enter with a "print pull" reveal and cross-dissolve between beats.
* **Editorial motion** - headline lines rise in with a stagger, the dialogue well settles in,
  scenes dissolve into each other; the voice cadence trace is drawn from real loudness.

All motion is a pure function of timeline time (no randomness), mirroring the HyperFrames
render contract, so every render is identical.
"""

from __future__ import annotations

import logging
import math
import re
import subprocess
import tempfile
from array import array
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw

logger = logging.getLogger("cyberpulse.media_motion")

ENVELOPE_HOP_SEC = 0.01


# ------------------------------------------------------------------------------
# Data model
# ------------------------------------------------------------------------------


@dataclass(frozen=True)
class WordTiming:
    text: str
    start: float  # seconds, relative to the scene start
    end: float


@dataclass(frozen=True)
class PlateBeat:
    image_path: Path | None
    start: float
    end: float
    caption: str = ""


@dataclass(frozen=True)
class SceneMotionSpec:
    scene_no: int
    total_scenes: int
    run_id: str
    headline: str
    category_label: str
    speaker: str
    speaker_role: str
    narration: str
    duration_sec: float
    words: list[WordTiming]
    beats: list[PlateBeat]
    envelope: list[float] = field(default_factory=list)  # normalised loudness per ENVELOPE_HOP_SEC
    start_sec: float = 0.0  # absolute start on the master timeline


# ------------------------------------------------------------------------------
# Audio envelope & word alignment
# ------------------------------------------------------------------------------


def decode_audio_envelope(audio_path: Path, ffmpeg_bin: str | None, hop_sec: float = ENVELOPE_HOP_SEC) -> list[float]:
    """Decode audio to mono PCM with FFmpeg and return a 0..1 RMS envelope per hop."""
    if not ffmpeg_bin or not audio_path or not audio_path.exists():
        return []
    sr = 16000
    try:
        proc = subprocess.run(
            [ffmpeg_bin, "-v", "error", "-i", str(audio_path), "-f", "s16le", "-ac", "1", "-ar", str(sr), "pipe:1"],
            capture_output=True,
            check=True,
            timeout=120,
        )
    except Exception as exc:
        logger.info("Envelope decode failed: %s", exc)
        return []
    samples = array("h")
    samples.frombytes(proc.stdout[: len(proc.stdout) // 2 * 2])
    win = max(1, int(sr * hop_sec))
    env: list[float] = []
    for i in range(0, len(samples), win):
        chunk = samples[i : i + win]
        if not chunk:
            break
        env.append(math.sqrt(sum(x * x for x in chunk) / len(chunk)))
    if not env:
        return []
    ordered = sorted(env)
    ref = ordered[int(len(ordered) * 0.95)] or 1.0
    return [min(1.0, v / ref) for v in env]


def _word_weight(word: str) -> float:
    letters = len(re.sub(r"[^\w]", "", word))
    # Vietnamese words are single syllables; long tokens (CVE ids, versions) take longer to read out.
    base = 1.0 + 0.11 * letters
    if re.search(r"\d", word):
        base += 0.5 * len(re.findall(r"\d+", word))
    return base


def _pause_weight(word: str) -> float:
    if re.search(r"[.!?…]$", word):
        return 1.6
    if re.search(r"[,;:]$", word):
        return 0.8
    return 0.0


def _find_pauses(envelope: list[float], hop: float, min_len: float = 0.11, thresh: float = 0.07) -> list[tuple[float, float]]:
    pauses: list[tuple[float, float]] = []
    run_start: int | None = None
    for i, v in enumerate(envelope + [1.0]):
        if v < thresh:
            if run_start is None:
                run_start = i
        elif run_start is not None:
            if (i - run_start) * hop >= min_len:
                pauses.append((run_start * hop, i * hop))
            run_start = None
    return pauses


def align_words(text: str, duration: float, envelope: list[float] | None = None, hop: float = ENVELOPE_HOP_SEC) -> list[WordTiming]:
    """Time every word of ``text`` across ``duration`` seconds of speech.

    With an envelope, leading/trailing silence is trimmed and clause boundaries are snapped
    to the nearest real pause in the audio; words in between share time by syllable weight.
    """
    words = text.split()
    if not words or duration <= 0:
        return []

    speech_start, speech_end = 0.0, duration
    pauses: list[tuple[float, float]] = []
    if envelope:
        voiced = [i for i, v in enumerate(envelope) if v >= 0.07]
        if voiced:
            speech_start = max(0.0, voiced[0] * hop - 0.02)
            speech_end = min(duration, (voiced[-1] + 1) * hop + 0.02)
        pauses = [p for p in _find_pauses(envelope, hop) if speech_start + 0.2 < p[0] and p[1] < speech_end - 0.1]
    if speech_end - speech_start < 0.3:
        speech_start, speech_end = 0.0, duration

    weights = [_word_weight(w) for w in words]
    gaps = [_pause_weight(w) for w in words]
    gaps[-1] = 0.0
    total = sum(weights) + sum(gaps)
    span = speech_end - speech_start

    # Initial proportional estimate of where each word starts.
    starts: list[float] = []
    acc = 0.0
    for w, g in zip(weights, gaps):
        starts.append(speech_start + span * acc / total)
        acc += w + g
    ends = [starts[i] + span * weights[i] / total for i in range(len(words))]

    # Anchor clause boundaries to real pauses (monotonic, nearest within a window).
    anchors: list[tuple[int, float]] = [(0, speech_start)]
    used = -1
    for i, g in enumerate(gaps):
        if g <= 0 or i + 1 >= len(words):
            continue
        est = (ends[i] + starts[i + 1]) / 2
        best: tuple[float, int] | None = None
        for j, (p0, p1) in enumerate(pauses):
            if j <= used:
                continue
            centre = (p0 + p1) / 2
            dist = abs(centre - est)
            if dist <= max(0.9, span * 0.08) and (best is None or dist < best[0]):
                best = (dist, j)
        if best is not None:
            used = best[1]
            p0, p1 = pauses[used]
            if p1 > anchors[-1][1] + 0.15:
                anchors.append((i + 1, p1))
    anchors.append((len(words), speech_end))

    timings: list[WordTiming] = []
    for (a_idx, a_t), (b_idx, b_t) in zip(anchors, anchors[1:]):
        seg = list(range(a_idx, b_idx))
        if not seg:
            continue
        # The end of the last word before an anchor sits at the pause start, not the pause end.
        seg_w = sum(weights[k] for k in seg) + sum(gaps[k] for k in seg[:-1])
        seg_span = max(0.05, b_t - a_t)
        if b_idx < len(words):
            pause_start = next((p0 for p0, p1 in pauses if abs(p1 - b_t) < 1e-6), None)
            if pause_start is not None and pause_start > a_t:
                seg_span = pause_start - a_t
        cur = a_t
        for k in seg:
            dur = seg_span * weights[k] / seg_w
            timings.append(WordTiming(words[k], round(cur, 3), round(cur + dur, 3)))
            cur += dur + (seg_span * gaps[k] / seg_w if k != seg[-1] else 0.0)
    return timings


# ------------------------------------------------------------------------------
# Easing helpers
# ------------------------------------------------------------------------------


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if v < lo else hi if v > hi else v


def ease_out_cubic(p: float) -> float:
    p = _clamp(p)
    return 1 - (1 - p) ** 3


def ease_in_out_sine(p: float) -> float:
    p = _clamp(p)
    return -(math.cos(math.pi * p) - 1) / 2


def _lerp_color(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = _clamp(t)
    return (round(a[0] + (b[0] - a[0]) * t), round(a[1] + (b[1] - a[1]) * t), round(a[2] + (b[2] - a[2]) * t))


# ------------------------------------------------------------------------------
# Scene renderer
# ------------------------------------------------------------------------------


class SceneMotionRenderer:
    """Renders any timeline moment of one scene as a PIL RGB frame."""

    INTRO = 0.75  # seconds of entrance choreography
    BEAT_DISSOLVE = 0.6

    def __init__(self, spec: SceneMotionSpec, width: int, height: int) -> None:
        from cyberpulse import media_video as mv

        self.mv = mv
        self.spec = spec
        self.w, self.h = width, height
        s = width / 1080.0
        self.s = s
        self.theme = mv.detect_scene_theme(mv.format_editorial_headline(spec.headline), spec.narration)
        self.primary = self.theme["primary"]
        self.headline = mv.format_editorial_headline(spec.headline)
        self.margin = round(72 * s)
        self.content_w = width - 2 * self.margin

        # Fonts
        self.f_mast = mv.resolve_font(max(16, round(42 * s)), bold=True, serif=True)
        self.f_folio = mv.resolve_font(max(10, round(21 * s)))
        self.f_folio_b = mv.resolve_font(max(10, round(21 * s)), bold=True)
        self.f_rubric = mv.resolve_font(max(11, round(23 * s)), bold=True)
        head_size = 48 if len(self.headline) < 42 else (42 if len(self.headline) < 72 else 37)
        self.f_head = mv.resolve_font(max(14, round(head_size * s)), bold=True, serif=True)
        self.head_lh = round((head_size + 13) * s)
        self.f_speaker = mv.resolve_font(max(12, round(26 * s)), bold=True)
        self.f_role = mv.resolve_font(max(11, round(22 * s)))
        self.f_word = mv.resolve_font(max(13, round(36 * s)))
        self.f_foot = mv.resolve_font(max(9, round(18 * s)))
        self.f_caption = mv.resolve_font(max(9, round(19 * s)), serif=True)

        # Geometry
        self.rule_y = round(200 * s)
        self.rubric_y = self.rule_y + round(34 * s)
        self.head_y = self.rubric_y + round(44 * s)
        self.plate = (self.margin, round(505 * s), width - self.margin, round(1105 * s))
        mat = max(6, round(16 * s))
        px0, py0, px1, py1 = self.plate
        self.inner = (px0 + mat, py0 + mat, px1 - mat, py1 - mat)
        self.caption_y = py1 + round(26 * s)
        self.well = (self.margin, round(1190 * s), width - self.margin, round(1672 * s))
        self.well_pad = round(32 * s)
        self.byline_y = self.well[1] + round(28 * s)
        self.text_top = self.byline_y + round(64 * s)
        self.line_h = round(54 * s)
        self.visible_lines = max(3, (self.well[3] - round(30 * s) - self.text_top) // self.line_h)

        scratch = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        self.head_lines = mv.wrap_text_to_width(self.headline, self.f_head, self.content_w, scratch)[:3]
        self._layout_words(scratch)
        self._load_beats()
        self.base = self._render_base()

    # -- preparation ---------------------------------------------------------

    def _layout_words(self, draw: ImageDraw.ImageDraw) -> None:
        max_w = self.content_w - 2 * self.well_pad
        space = draw.textlength(" ", font=self.f_word)
        x, line = 0.0, 0
        self.word_boxes: list[tuple[float, int, float]] = []  # (x, line, width)
        for wt in self.spec.words:
            ww = draw.textlength(wt.text, font=self.f_word)
            if x > 0 and x + ww > max_w:
                x, line = 0.0, line + 1
            self.word_boxes.append((x, line, ww))
            x += ww + space
        self.line_count = (self.word_boxes[-1][1] + 1) if self.word_boxes else 0

    def _load_beats(self) -> None:
        ix0, iy0, ix1, iy1 = self.inner
        iw, ih = ix1 - ix0, iy1 - iy0
        self.beat_images: list[Image.Image | None] = []
        for beat in self.spec.beats:
            im_out: Image.Image | None = None
            if beat.image_path and beat.image_path.exists():
                try:
                    with Image.open(beat.image_path) as raw:
                        src = raw.convert("RGB")
                    # Cover-fit at 1.12x so Ken Burns never shows an edge.
                    cover = max(iw * 1.12 / src.width, ih * 1.12 / src.height)
                    src = src.resize((round(src.width * cover), round(src.height * cover)), Image.Resampling.LANCZOS)
                    # Warm print toning keeps any generator's palette inside the Hallmark paper world.
                    gray = src.convert("L").convert("RGB")
                    src = Image.blend(src, gray, 0.12)
                    src = Image.blend(src, Image.new("RGB", src.size, self.mv.HALLMARK_PAPER), 0.07)
                    im_out = src
                except Exception as exc:
                    logger.info("Could not load beat image %s: %s", beat.image_path, exc)
            self.beat_images.append(im_out)
        if not any(self.beat_images):
            # Monograph fallback (drawn once, then animated like a photo).
            plate = Image.new("RGB", (round(iw * 1.12), round(ih * 1.12)), (241, 235, 223))
            d = ImageDraw.Draw(plate)
            tokens = self.mv._extract_story_tokens(self.headline, self.spec.narration)
            self.mv._draw_editorial_monograph_plate(
                d, round(iw * 0.06), round(ih * 0.06), round(iw * 1.06), round(ih * 1.06),
                self.theme, tokens, self.spec.scene_no, self.s,
            )
            self.beat_images = [plate for _ in (self.spec.beats or [None])]
            if not self.spec.beats:
                self.spec = SceneMotionSpec(**{**self.spec.__dict__, "beats": [PlateBeat(None, 0.0, self.spec.duration_sec)]})

    def _render_base(self) -> Image.Image:
        mv, s, m = self.mv, self.s, self.margin
        img = Image.new("RGB", (self.w, self.h), mv.HALLMARK_PAPER)
        d = ImageDraw.Draw(img)
        mast_y = round(108 * s)
        d.text((m, mast_y), "CyberPulse", fill=mv.HALLMARK_INK, font=self.f_mast)
        d.text((m, mast_y + round(52 * s)), "Tạp chí điểm tin và phân tích an ninh mạng", fill=mv.HALLMARK_MUTED, font=self.f_folio)
        folio = f"Mục {self.spec.scene_no:02d} / {max(1, self.spec.total_scenes):02d}"
        d.text((self.w - m, mast_y + round(8 * s)), folio, fill=mv.HALLMARK_INK, font=self.f_folio_b, anchor="ra")
        d.text((self.w - m, mast_y + round(42 * s)), "Thời lượng dưới 1 phút", fill=mv.HALLMARK_MUTED, font=self.f_folio, anchor="ra")
        d.line([(m, self.rule_y), (self.w - m, self.rule_y)], fill=mv.HALLMARK_INK, width=max(2, round(3 * s)))
        d.line([(m, self.rule_y + round(7 * s)), (self.w - m, self.rule_y + round(7 * s))], fill=mv.HALLMARK_RULE_DARK, width=1)

        px0, py0, px1, py1 = self.plate
        mv._draw_studio_crop_marks(d, px0, py0, px1, py1, s, color=mv.HALLMARK_INK)
        d.rectangle(self.plate, fill=mv.HALLMARK_PAPER_MAT, outline=mv.HALLMARK_INK, width=max(1, round(2 * s)))

        wx0, wy0, wx1, wy1 = self.well
        d.rectangle(self.well, fill=mv.HALLMARK_PAPER_WELL)
        d.line([(wx0, wy0), (wx1, wy0)], fill=mv.HALLMARK_INK, width=max(1, round(2 * s)))
        d.line([(wx0, wy1), (wx1, wy1)], fill=mv.HALLMARK_RULE, width=1)

        d.text((m, self.h - round(72 * s)), "Ấn phẩm biên tập thủ công bởi CyberPulse", fill=mv.HALLMARK_MUTED, font=self.f_foot)
        return img

    # -- per-frame drawing ---------------------------------------------------

    def _draw_plate(self, img: Image.Image, t: float) -> None:
        ix0, iy0, ix1, iy1 = self.inner
        iw, ih = ix1 - ix0, iy1 - iy0
        beats = self.spec.beats
        idx = 0
        for i, b in enumerate(beats):
            if t >= b.start - 1e-6:
                idx = i
        frame = self._beat_frame(idx, t, iw, ih)
        if idx > 0 and t - beats[idx].start < self.BEAT_DISSOLVE:
            prev = self._beat_frame(idx - 1, t, iw, ih)
            frame = Image.blend(prev, frame, ease_in_out_sine((t - beats[idx].start) / self.BEAT_DISSOLVE))

        # "Print pull" reveal: the plate rolls down from the top edge during the intro.
        reveal = ease_out_cubic((t - 0.08) / 0.62)
        if reveal < 1.0:
            visible_h = max(0, round(ih * reveal))
            if visible_h > 0:
                img.paste(frame.crop((0, 0, iw, visible_h)), (ix0, iy0))
                d = ImageDraw.Draw(img)
                d.line([(ix0, iy0 + visible_h), (ix1, iy0 + visible_h)], fill=self.primary, width=max(1, round(2 * self.s)))
        else:
            img.paste(frame, (ix0, iy0))
            ImageDraw.Draw(img).rectangle(self.inner, outline=self.mv.HALLMARK_RULE_DARK, width=1)

    def _beat_frame(self, idx: int, t: float, iw: int, ih: int) -> Image.Image:
        src = self.beat_images[idx] if idx < len(self.beat_images) else None
        if src is None:
            src = next((b for b in self.beat_images if b is not None), None)
        if src is None:
            return Image.new("RGB", (iw, ih), self.mv.HALLMARK_PAPER_MAT)
        beat = self.spec.beats[idx]
        span = max(0.5, beat.end - beat.start + self.BEAT_DISSOLVE)
        p = ease_in_out_sine((t - beat.start) / span)
        # Alternate push-in / pull-out and pan direction per beat for variety without randomness.
        k = (self.spec.scene_no + idx) % 2
        zoom = (1.0 + 0.085 * p) if k == 0 else (1.085 - 0.085 * p)
        crop_w = src.width / 1.12 / zoom * 1.0
        crop_h = crop_w * ih / iw
        if crop_h > src.height:
            crop_h = src.height / zoom
            crop_w = crop_h * iw / ih
        pan = (p - 0.5) * (src.width - crop_w) * 0.6 * (1 if k == 0 else -1)
        cx = src.width / 2 + pan
        cy = src.height / 2 - (p - 0.5) * (src.height - crop_h) * 0.3
        x0 = _clamp(cx - crop_w / 2, 0, src.width - crop_w)
        y0 = _clamp(cy - crop_h / 2, 0, src.height - crop_h)
        return src.resize((iw, ih), Image.Resampling.BILINEAR, box=(x0, y0, x0 + crop_w, y0 + crop_h))

    def _current_word(self, t: float) -> int:
        words = self.spec.words
        cur = -1
        for i, wt in enumerate(words):
            if t >= wt.start:
                cur = i
            else:
                break
        return cur

    def render(self, t: float) -> Image.Image:
        mv, s, m = self.mv, self.s, self.margin
        t = _clamp(t, 0.0, self.spec.duration_sec)
        img = self.base.copy()
        d = ImageDraw.Draw(img)
        paper, well_bg = mv.HALLMARK_PAPER, mv.HALLMARK_PAPER_WELL

        # Rubric + headline entrance (fade from paper + rise, staggered by line).
        a = ease_out_cubic(t / 0.45)
        sq = max(6, round(12 * s))
        ry = self.rubric_y + round(10 * s * (1 - a))
        d.rectangle([m, ry + round(6 * s), m + sq, ry + round(6 * s) + sq], fill=_lerp_color(paper, self.primary, a))
        d.text((m + sq + round(14 * s), ry), self.spec.category_label or self.theme["badge"], fill=_lerp_color(paper, self.primary, a), font=self.f_rubric)
        for i, line in enumerate(self.head_lines):
            la = ease_out_cubic((t - 0.1 - i * 0.09) / 0.5)
            dy = round(22 * s * (1 - la))
            d.text((m, self.head_y + i * self.head_lh + dy), line, fill=_lerp_color(paper, mv.HALLMARK_INK, la), font=self.f_head)

        # Illustration plate.
        self._draw_plate(img, t)
        beat_caption = ""
        for b in self.spec.beats:
            if t >= b.start:
                beat_caption = b.caption
        cap_a = ease_out_cubic((t - 0.5) / 0.4)
        if beat_caption:
            d.text((m + round(16 * s), self.caption_y), beat_caption, fill=_lerp_color(paper, mv.HALLMARK_MUTED, cap_a), font=self.f_caption)

        # Dialogue well: byline + karaoke transcript.
        wa = ease_out_cubic((t - 0.2) / 0.5)
        wdy = round(16 * s * (1 - wa))
        bar_x = m + self.well_pad
        d.rectangle([bar_x, self.byline_y + wdy + round(2 * s), bar_x + max(2, round(4 * s)), self.byline_y + wdy + round(32 * s)], fill=_lerp_color(well_bg, self.primary, wa))
        spk_x = bar_x + round(16 * s)
        d.text((spk_x, self.byline_y + wdy), self.spec.speaker, fill=_lerp_color(well_bg, mv.HALLMARK_INK, wa), font=self.f_speaker)
        sb = d.textbbox((spk_x, self.byline_y + wdy), self.spec.speaker, font=self.f_speaker)
        d.text((sb[2] + round(16 * s), self.byline_y + wdy + round(3 * s)), f"—  {self.spec.speaker_role}", fill=_lerp_color(well_bg, mv.HALLMARK_MUTED, wa), font=self.f_role)

        self._draw_transcript(img, d, t, wa, wdy)
        self._draw_cadence(d, t)
        self._draw_progress(d, t)
        return img

    def _draw_transcript(self, img: Image.Image, d: ImageDraw.ImageDraw, t: float, wa: float, wdy: int) -> None:
        mv, s = self.mv, self.s
        if not self.word_boxes:
            return
        cur = self._current_word(t)
        words = self.spec.words
        # Smooth scroll: keep the active line on the 2nd visible row once the text overflows.
        cur_line = self.word_boxes[max(0, cur)][1]
        target = _clamp(cur_line - 1, 0, max(0, self.line_count - self.visible_lines))
        if cur >= 0:
            # Glide toward the target line during the first 0.35 s of the line's first word.
            first_on_line = next(i for i, b in enumerate(self.word_boxes) if b[1] == cur_line)
            prev_target = _clamp(cur_line - 2, 0, max(0, self.line_count - self.visible_lines))
            glide = ease_in_out_sine((t - words[first_on_line].start) / 0.35)
            scroll = prev_target + (target - prev_target) * glide
        else:
            scroll = 0.0

        x_base = self.margin + self.well_pad
        top = self.text_top + wdy
        clip_top, clip_bot = self.text_top - round(4 * s), self.well[3] - round(26 * s)
        upcoming = _lerp_color(mv.HALLMARK_PAPER_WELL, (150, 141, 128), wa)
        spoken = _lerp_color(mv.HALLMARK_PAPER_WELL, mv.HALLMARK_INK_BODY, wa)
        marker = _lerp_color(mv.HALLMARK_PAPER_WELL, self.primary, 0.20)

        for i, (bx, line, bw) in enumerate(self.word_boxes):
            y = top + (line - scroll) * self.line_h
            if y < clip_top - self.line_h * 0.2 or y > clip_bot - self.line_h * 0.6:
                continue
            # Fade lines entering/leaving the window instead of hard clipping.
            edge = min((y - clip_top) / (self.line_h * 0.8) + 1.0, (clip_bot - self.line_h - y) / (self.line_h * 0.8) + 1.0)
            vis = _clamp(edge)
            x = x_base + bx
            if i == cur:
                wt = words[i]
                p = ease_out_cubic((t - wt.start) / max(0.08, (wt.end - wt.start) * 0.7))
                pad = round(6 * s)
                d.rounded_rectangle(
                    [x - pad, y + round(4 * s), x - pad + (bw + 2 * pad) * p, y + self.line_h - round(6 * s)],
                    radius=round(5 * s), fill=_lerp_color(mv.HALLMARK_PAPER_WELL, marker, vis),
                )
                d.text((x, y), wt.text, fill=_lerp_color(mv.HALLMARK_PAPER_WELL, self.primary, vis * wa), font=self.f_word)
            else:
                col = spoken if i < cur else upcoming
                d.text((x, y), words[i].text, fill=_lerp_color(mv.HALLMARK_PAPER_WELL, col, vis), font=self.f_word)
            if i < cur and i >= cur - 3 and line == self.word_boxes[cur][1]:
                pass  # recently spoken words simply settle into ink; no trailing effects.

        # Mask any glyph overflow beneath the window with the well colour.
        d.rectangle([self.well[0], clip_bot, self.well[2], self.well[3] - 1], fill=mv.HALLMARK_PAPER_WELL)
        foot_y = self.well[3] - round(30 * s)
        run_short = self.spec.run_id[-8:]
        d.text((x_base, foot_y), f"Trích lược từ bản thu âm đối thoại Podcast (Mã phiên {run_short})", fill=mv.HALLMARK_MUTED, font=self.f_foot)

    def _draw_cadence(self, d: ImageDraw.ImageDraw, t: float) -> None:
        mv, s, m = self.mv, self.s, self.margin
        y = round(1730 * s)
        d.text((m, y), "Nhịp giọng nói", fill=mv.HALLMARK_MUTED, font=self.f_foot, anchor="lm")
        x0 = m + round(200 * s)
        tw = self.content_w - round(200 * s)
        bars = 56
        step = tw / (bars - 1)
        env = self.spec.envelope
        hop = ENVELOPE_HOP_SEC
        # A rolling 2.8 s window centred slightly behind the playhead: the trace "listens".
        window = 2.8
        d.line([(x0, y), (x0 + tw, y)], fill=mv.HALLMARK_RULE, width=1)
        play_x = x0 + tw * 0.72
        for b in range(bars):
            bt = t - window * 0.72 + window * b / (bars - 1)
            if env:
                k = int(bt / hop)
                amp = max(env[k + j] for j in range(-2, 3) if 0 <= k + j < len(env)) if 0 <= k < len(env) else 0.0
            else:
                amp = 0.3 + 0.4 * abs(math.sin(bt * 5.1)) if 0 <= bt <= self.spec.duration_sec else 0.0
            half = max(1, round((2 + 20 * amp) * s))
            bx = round(x0 + b * step)
            past = bx <= play_x
            d.line([(bx, y - half), (bx, y + half)], fill=self.primary if past else mv.HALLMARK_RULE_DARK, width=max(2, round(3 * s)) if past else max(1, round(2 * s)))
        d.line([(round(play_x), y - round(26 * s)), (round(play_x), y + round(26 * s))], fill=mv.HALLMARK_INK, width=1)

    def _draw_progress(self, d: ImageDraw.ImageDraw, t: float) -> None:
        mv, s, m = self.mv, self.s, self.margin
        total = max(1, self.spec.total_scenes)
        bar_y = self.h - round(115 * s)
        gap = round(10 * s)
        seg_w = (self.content_w - (total - 1) * gap) / total
        prog = _clamp(t / max(0.1, self.spec.duration_sec))
        for i in range(total):
            sx0 = m + i * (seg_w + gap)
            no = i + 1
            d.rectangle([round(sx0), bar_y, round(sx0 + seg_w), bar_y + max(1, round(3 * s))], fill=mv.HALLMARK_INK if no < self.spec.scene_no else mv.HALLMARK_RULE)
            if no == self.spec.scene_no:
                d.rectangle([round(sx0), bar_y - round(1 * s), round(sx0 + seg_w * prog), bar_y + max(2, round(5 * s))], fill=self.primary)
        abs_t = self.spec.start_sec + t
        d.text((self.w - m, self.h - round(72 * s)), f"{int(abs_t // 60):02d}:{int(abs_t % 60):02d}", fill=mv.HALLMARK_INK, font=self.f_foot, anchor="ra")


# ------------------------------------------------------------------------------
# Timeline streaming into FFmpeg
# ------------------------------------------------------------------------------


SCENE_DISSOLVE_SEC = 0.42


def iter_timeline_frames(specs: list[SceneMotionSpec], width: int, height: int, fps: int, total_duration: float):
    """Yield RGB frames for the whole master timeline (scene dissolves included)."""
    renderers: dict[int, SceneMotionRenderer] = {}
    n_frames = max(1, math.ceil(total_duration * fps))
    last_frame_of: dict[int, Image.Image] = {}

    def renderer(i: int) -> SceneMotionRenderer:
        if i not in renderers:
            renderers[i] = SceneMotionRenderer(specs[i], width, height)
            # Free the scene two steps back; we only ever dissolve from the previous one.
            renderers.pop(i - 2, None)
        return renderers[i]

    for f in range(n_frames):
        t = f / fps
        idx = 0
        for i, sp in enumerate(specs):
            if t >= sp.start_sec - 1e-9:
                idx = i
        local = t - specs[idx].start_sec
        frame = renderer(idx).render(local)
        if idx > 0 and local < SCENE_DISSOLVE_SEC:
            prev = last_frame_of.get(idx - 1)
            if prev is None:
                prev = renderer(idx - 1).render(specs[idx - 1].duration_sec)
                last_frame_of[idx - 1] = prev
            frame = Image.blend(prev, frame, ease_in_out_sine(local / SCENE_DISSOLVE_SEC))
        if idx + 1 < len(specs) and t + 1.0 / fps >= specs[idx + 1].start_sec:
            last_frame_of[idx] = frame
        yield frame


def render_motion_video(
    specs: list[SceneMotionSpec],
    audio_path: Path | None,
    output_path: Path,
    width: int,
    height: int,
    fps: int,
    ffmpeg_bin: str,
    total_duration: float,
) -> float:
    """Stream rendered frames into FFmpeg (H.264 + AAC). Returns the encoded duration."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_bin, "-y", "-v", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0",
    ]
    if audio_path and audio_path.exists():
        cmd += ["-i", str(audio_path)]
    else:
        cmd += ["-f", "lavfi", "-t", f"{total_duration:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
    cmd += [
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart",
        str(output_path),
    ]
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=err)
        assert proc.stdin is not None
        try:
            for frame in iter_timeline_frames(specs, width, height, fps, total_duration):
                proc.stdin.write(frame.tobytes())
        except BrokenPipeError:
            pass
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass
        code = proc.wait()
        if code != 0:
            err.seek(0)
            from cyberpulse.media_video import MediaSynthesisError

            raise MediaSynthesisError(f"FFmpeg motion encode failed: {err.read().decode('utf-8', errors='ignore')[-800:]}")
    return total_duration

