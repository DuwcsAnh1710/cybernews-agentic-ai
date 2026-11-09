"""Context-matched AI illustrations for the CyberPulse 9:16 video brief.

Each curated podcast turn is split into one or two *beats* (by sentence). Every beat gets
its own illustration that depicts what the hosts are saying at that moment, drawn in the
warm Hallmark editorial register (gouache / coloured pencil on cream stock) rather than
the neon "hacker HUD" look that generic image models default to.

Flow:
    beat text (Vietnamese) --> visual brief in English (Gemini text, rule-based fallback)
        --> image provider chain (Gemini image -> Pollinations, configurable)
        --> cached file in ``<run_id>_assets/`` + ``illustrations.json`` manifest

The manifest stores a hash of each beat's text, so re-rendering a run reuses images that
still match and regenerates only beats whose script changed. Every network step is
optional: when no provider succeeds the renderer falls back to hand-placed images or the
monograph plate, so the video always renders.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import urllib.parse
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol

from PIL import Image

logger = logging.getLogger("cyberpulse.media_illustrations")

MANIFEST_NAME = "illustrations.json"
ILLUSTRATION_SIZE = (1200, 800)  # 3:2, matches the framed plate inside the 9:16 frame
LONG_TURN_SPLIT_SEC = 12.5       # turns longer than this get a second illustration beat

HALLMARK_STYLE_SUFFIX = (
    "Editorial illustration for a printed news magazine, gouache and coloured pencil on warm "
    "cream paper, muted earthy palette of ochre, brick vermilion, prussian blue and forest "
    "green, soft natural daylight, visible paper grain and brush texture, calm documentary "
    "composition at human scale, generous negative space. No text, no letters, no numbers, "
    "no logos, no watermarks, no neon, no glowing holograms, no sci-fi HUD, no hooded hacker."
)

# Rule-based visual vocabulary used when no LLM is available to write the brief.
_MOTIFS: list[tuple[tuple[str, ...], str]] = [
    (("openssh", "ssh", "mật khẩu", "password"), "a heavy brass padlock and an old iron key resting on a server cabinet, a sysadmin's hand holding a printed patch notice"),
    (("apple", "iphone", "ios", "macos"), "a smartphone and a laptop on a wooden desk, a red wax warning seal stamped on a paper folder beside them"),
    (("cisa", "kev", "cảnh báo"), "a government bulletin board with pinned paper warning notices and a red ribbon, an analyst reading one closely"),
    (("linux", "kernel", "debian", "ubuntu", "alpine"), "a cutaway drawing of a computer as a layered building, the deepest foundation floor highlighted in vermilion"),
    (("apt", "gói", "package", "mitm", "trung gian"), "parcels travelling on a conveyor belt between two buildings while a shadowy hand swaps one parcel midway"),
    (("ransomware", "tống tiền", "mã hóa", "lockbit"), "filing cabinets wrapped in heavy chains with a padlock, scattered papers on an office floor"),
    (("rò rỉ", "lộ lọt", "breach", "leak", "dữ liệu"), "paper records slipping out of a cracked archive box onto a desk, a magnifying glass nearby"),
    (("chatgpt", "ai", "prompt", "trí tuệ nhân tạo"), "a typewriter connected by thread to a small mechanical brass robot, papers with redacted lines"),
    (("botnet", "android", "thiết bị"), "dozens of small household devices linked by red thread to a single puppeteer's hand"),
    (("vá", "patch", "bản sửa", "cập nhật"), "a craftsman carefully stitching a patch onto a worn canvas shaped like a shield"),
    (("lỗ hổng", "cve", "zero-day", "exploit", "khai thác"), "a crack running through a stone wall, a small figure inspecting it with a lantern"),
]


@dataclass(frozen=True)
class IllustrationBeat:
    """One illustration moment inside a scene (1-2 per curated podcast turn)."""

    key: str                 # stable cache key, e.g. "seg03_b1"
    scene_no: int
    text: str                # the spoken words this image must match
    headline: str
    word_start: int          # index of the first word of this beat inside the scene text
    word_end: int            # exclusive
    prompt: str = ""
    image_path: Path | None = None
    provider: str = ""

    @property
    def text_hash(self) -> str:
        return hashlib.sha1(f"{self.headline}|{self.text}".encode("utf-8")).hexdigest()[:12]


@dataclass
class IllustrationReport:
    generated: int = 0
    reused: int = 0
    failed: int = 0
    providers: dict[str, int] = field(default_factory=dict)


class ImageProvider(Protocol):
    name: str

    def generate(self, prompt: str, seed: int) -> bytes | None:
        """Return encoded image bytes or None on failure."""
        ...


class PromptWriter(Protocol):
    def write(self, beats: list[IllustrationBeat]) -> dict[str, str]:
        """Return {beat.key: english visual brief}."""
        ...


# ------------------------------------------------------------------------------
# Beat planning
# ------------------------------------------------------------------------------


def split_scene_into_beats(
    scene_no: int,
    segment_index: int,
    headline: str,
    text: str,
    duration_sec: float,
) -> list[IllustrationBeat]:
    """Split a spoken turn into 1-2 beats at the sentence boundary nearest its middle."""
    words = text.split()
    if not words:
        return [IllustrationBeat(f"seg{segment_index:02d}_b1", scene_no, text, headline, 0, 0)]

    split_at: int | None = None
    if duration_sec > LONG_TURN_SPLIT_SEC and len(words) >= 16:
        boundaries = [i + 1 for i, w in enumerate(words[:-4]) if re.search(r"[.!?…]$", w) and i >= 4]
        if boundaries:
            mid = len(words) / 2
            split_at = min(boundaries, key=lambda b: abs(b - mid))
            if not (len(words) * 0.25 <= split_at <= len(words) * 0.78):
                split_at = None

    if split_at is None:
        return [IllustrationBeat(f"seg{segment_index:02d}_b1", scene_no, text, headline, 0, len(words))]
    return [
        IllustrationBeat(f"seg{segment_index:02d}_b1", scene_no, " ".join(words[:split_at]), headline, 0, split_at),
        IllustrationBeat(f"seg{segment_index:02d}_b2", scene_no, " ".join(words[split_at:]), headline, split_at, len(words)),
    ]


def rule_based_brief(beat: IllustrationBeat) -> str:
    """Translate a Vietnamese beat into an English visual brief using a motif dictionary."""
    combined = f"{beat.headline} {beat.text}".lower()
    picked = [motif for keys, motif in _MOTIFS if any(k in combined for k in keys)]
    subject = picked[0] if picked else "a quiet newsroom desk with printed security reports, a cup of tea and a desk lamp"
    if len(picked) > 1:
        subject = f"{subject}; in the background, {picked[1]}"
    return subject


def build_image_prompt(brief: str) -> str:
    return f"{brief.strip().rstrip('.')}. {HALLMARK_STYLE_SUFFIX}"


# ------------------------------------------------------------------------------
# Gemini prompt writer & providers
# ------------------------------------------------------------------------------


def _gemini_keys() -> list[str]:
    try:
        from cyberpulse.llm import parse_api_keys

        return parse_api_keys()
    except Exception:
        return []


class GeminiPromptWriter:
    """Ask a Gemini text model for one concrete English scene description per beat."""

    def __init__(self, model: str = "gemini-2.5-flash", api_keys: list[str] | None = None) -> None:
        self.model = model
        self.api_keys = api_keys if api_keys is not None else _gemini_keys()

    def write(self, beats: list[IllustrationBeat]) -> dict[str, str]:
        if not self.api_keys or not beats:
            return {}
        from google import genai
        from google.genai import types

        listing = "\n".join(
            f'- key: {b.key}\n  headline: {b.headline}\n  spoken: "{b.text}"' for b in beats
        )
        instruction = (
            "You art-direct illustrations for a Vietnamese cybersecurity news video. For each beat, "
            "write ONE concrete English scene description (max 40 words) that literally depicts "
            "what is being said: the real objects, people, places or actions involved (e.g. a "
            "server rack, a phone, a government notice, a parcel being swapped). Use physical "
            "metaphors for abstract ideas. Never ask for any visible text, letters, logos, screens "
            "full of code, neon or holograms. Answer as a JSON object mapping key to description.\n\n"
            f"{listing}"
        )
        for key in self.api_keys:
            try:
                client = genai.Client(api_key=key)
                resp = client.models.generate_content(
                    model=self.model,
                    contents=instruction,
                    config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.6),
                )
                data = json.loads(resp.text or "{}")
                if isinstance(data, dict):
                    return {str(k): str(v) for k, v in data.items() if str(v).strip()}
            except Exception as exc:  # quota, network, schema
                logger.info("Gemini prompt writer failed: %s", str(exc)[:160])
        return {}


class GeminiImageProvider:
    """Gemini native image generation (requires an API key with image quota)."""

    name = "gemini-image"

    def __init__(
        self,
        models: tuple[str, ...] = ("gemini-2.5-flash-image", "gemini-3.1-flash-image"),
        api_keys: list[str] | None = None,
    ) -> None:
        self.models = models
        self.api_keys = api_keys if api_keys is not None else _gemini_keys()
        self._exhausted = False

    def generate(self, prompt: str, seed: int) -> bytes | None:
        if not self.api_keys or self._exhausted:
            return None
        from google import genai
        from google.genai import types

        quota_errors = 0
        attempts = 0
        for key in self.api_keys:
            client = genai.Client(api_key=key)
            for model in self.models:
                attempts += 1
                try:
                    resp = client.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_modalities=["IMAGE"],
                            image_config=types.ImageConfig(aspect_ratio="3:2"),
                        ),
                    )
                    for cand in resp.candidates or []:
                        for part in (cand.content.parts if cand.content else []) or []:
                            if part.inline_data and part.inline_data.data:
                                return bytes(part.inline_data.data)
                except Exception as exc:
                    if "RESOURCE_EXHAUSTED" in str(exc) or "429" in str(exc):
                        quota_errors += 1
                    logger.info("Gemini image (%s) failed: %s", model, str(exc)[:160])
        if attempts and quota_errors == attempts:
            self._exhausted = True  # stop hammering a key without image quota for later beats
        return None


class PollinationsImageProvider:
    """Free, key-less Flux endpoint (image.pollinations.ai). Used as a fallback provider."""

    name = "pollinations"

    def __init__(self, timeout: float = 90.0, model: str = "flux") -> None:
        self.timeout = timeout
        self.model = model

    def generate(self, prompt: str, seed: int) -> bytes | None:
        import time
        import httpx

        w, h = ILLUSTRATION_SIZE
        url = (
            "https://image.pollinations.ai/prompt/"
            f"{urllib.parse.quote(prompt[:900])}?width={w}&height={h}&seed={seed}"
            f"&model={self.model}&nologo=true&enhance=false"
        )
        for attempt in range(3):
            try:
                resp = httpx.get(url, timeout=self.timeout, follow_redirects=True)
                if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image/"):
                    return resp.content
                logger.info("Pollinations returned %s (attempt %d)", resp.status_code, attempt + 1)
            except Exception as exc:
                logger.info("Pollinations failed (attempt %d): %s", attempt + 1, str(exc)[:160])
            time.sleep(1.5 * (attempt + 1))
        return None


def default_providers(spec: str | None = None) -> list[ImageProvider]:
    """Build the provider chain from ``CYBERPULSE_ILLUSTRATION_PROVIDERS`` (default gemini,pollinations)."""
    raw = spec if spec is not None else os.environ.get("CYBERPULSE_ILLUSTRATION_PROVIDERS", "gemini,pollinations")
    chain: list[ImageProvider] = []
    for name in [p.strip().lower() for p in raw.split(",") if p.strip()]:
        if name in ("gemini", "gemini-image"):
            chain.append(GeminiImageProvider())
        elif name == "pollinations":
            chain.append(PollinationsImageProvider())
    return chain


# ------------------------------------------------------------------------------
# Orchestration with manifest cache
# ------------------------------------------------------------------------------


def _load_manifest(assets_dir: Path) -> dict[str, Any]:
    path = assets_dir / MANIFEST_NAME
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("beats"), dict):
                return data
        except Exception:
            pass
    return {"version": 1, "style": "hallmark-gouache", "beats": {}}


def _normalise_image(raw: bytes, out_path: Path) -> bool:
    try:
        with Image.open(BytesIO(raw)) as im:
            rgb = im.convert("RGB")
            if rgb.width >= 400 and rgb.height >= 300:
                inset_x = round(rgb.width * 0.05)
                inset_y = round(rgb.height * 0.06)
                rgb = rgb.crop((inset_x, inset_y, rgb.width - inset_x, rgb.height - inset_y))
            tw, th = ILLUSTRATION_SIZE
            sw, sh = rgb.size
            scale = max(tw / sw, th / sh)
            resized = rgb.resize((max(tw, round(sw * scale)), max(th, round(sh * scale))), Image.Resampling.LANCZOS)
            left = (resized.width - tw) // 2
            top = (resized.height - th) // 2
            resized.crop((left, top, left + tw, top + th)).save(out_path, "JPEG", quality=90)
        return True
    except Exception as exc:
        logger.info("Could not decode generated image: %s", exc)
        return False


def ensure_beat_illustrations(
    beats: list[IllustrationBeat],
    assets_dir: Path,
    providers: list[ImageProvider] | None = None,
    prompt_writer: PromptWriter | None = None,
    force: bool = False,
) -> tuple[list[IllustrationBeat], IllustrationReport]:
    """Return beats with ``image_path`` filled where a matching illustration exists or was made.

    Never raises for provider failures: beats that could not be illustrated keep
    ``image_path=None`` and the renderer falls back gracefully.
    """
    report = IllustrationReport()
    if not beats:
        return beats, report

    assets_dir.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest(assets_dir)
    entries: dict[str, Any] = manifest["beats"]

    pending: list[IllustrationBeat] = []
    resolved: dict[str, IllustrationBeat] = {}
    for b in beats:
        entry = entries.get(b.key)
        if (
            not force
            and isinstance(entry, dict)
            and entry.get("text_hash") == b.text_hash
            and (assets_dir / str(entry.get("file", ""))).is_file()
        ):
            resolved[b.key] = IllustrationBeat(
                **{**b.__dict__, "prompt": entry.get("prompt", ""), "image_path": assets_dir / entry["file"], "provider": entry.get("provider", "cache")}
            )
            report.reused += 1
        else:
            pending.append(b)

    if pending and providers:
        briefs: dict[str, str] = {}
        if prompt_writer is not None:
            try:
                briefs = prompt_writer.write(pending)
            except Exception as exc:
                logger.info("Prompt writer error: %s", exc)
        for b in pending:
            brief = briefs.get(b.key) or rule_based_brief(b)
            prompt = build_image_prompt(brief)
            seed = int(b.text_hash[:6], 16) % 100000
            out_file = f"{b.key}.jpg"
            made_by = ""
            for provider in providers:
                raw = provider.generate(prompt, seed)
                if raw and _normalise_image(raw, assets_dir / out_file):
                    made_by = provider.name
                    break
            if made_by:
                entries[b.key] = {
                    "file": out_file,
                    "prompt": prompt,
                    "brief": brief,
                    "text": b.text,
                    "text_hash": b.text_hash,
                    "provider": made_by,
                }
                resolved[b.key] = IllustrationBeat(
                    **{**b.__dict__, "prompt": prompt, "image_path": assets_dir / out_file, "provider": made_by}
                )
                report.generated += 1
                report.providers[made_by] = report.providers.get(made_by, 0) + 1
            else:
                report.failed += 1

        (assets_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    elif pending:
        report.failed += len(pending)

    return [resolved.get(b.key, b) for b in beats], report

