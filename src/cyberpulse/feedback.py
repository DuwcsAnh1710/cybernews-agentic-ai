"""Structured feedback extraction and sanitization for editorial rewrite loop."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import TYPE_CHECKING, Any, Sequence

if TYPE_CHECKING:
    from cyberpulse.validator import ValidationReport

# 13 rewritable error codes (E_RUN_ID_MISMATCH and E_CITE_UNKNOWN are non-rewritable)
REWRITABLE_ERROR_CODES: frozenset[str] = frozenset({
    "E_ARTICLE_UNCOVERED",
    "E_CAPTION_LENGTH",
    "E_CAPTION_PLATFORM",
    "E_CVE_UNSUPPORTED",
    "E_HASHTAG_FORMAT",
    "E_NUMBER_UNSUPPORTED",
    "E_ONSCREEN_LENGTH",
    "E_SCENE_DURATION",
    "E_SECTION_CITE",
    "E_SECTION_COUNT",
    "E_SPEAKER_UNKNOWN",
    "E_URL_OR_ID_IN_TEXT",
    "E_VIDEO_TOTAL",
})

_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_HEX_ID_PATTERN = re.compile(r"\b[0-9a-fA-F]{16}\b")
_URL_PATTERN = re.compile(r"https?://[^\s]+")
_WHITESPACE_PATTERN = re.compile(r"\s+")


@dataclass(frozen=True)
class FeedbackItem:
    """An individual sanitized feedback item for LLM rewrite prompts."""

    code: str
    path: str
    message: str
    evidence: str


def sanitize_feedback_text(text: str) -> str:
    """Sanitize feedback strings by removing controls, IDs, URLs, XML brackets, and extra spaces.

    1. Removes control characters [\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f].
    2. Replaces 16-hex article IDs with '[removed]'.
    3. Replaces URLs with '[removed]'.
    4. Removes '<' and '>' to prevent prompt delimiter tampering.
    5. Collapses all whitespace (including newlines and tabs) to single spaces.
    """
    if not text:
        return ""
    cleaned = _CONTROL_CHAR_PATTERN.sub("", text)
    cleaned = _HEX_ID_PATTERN.sub("[removed]", cleaned)
    cleaned = _URL_PATTERN.sub("[removed]", cleaned)
    cleaned = cleaned.replace("<", "").replace(">", "")
    cleaned = _WHITESPACE_PATTERN.sub(" ", cleaned).strip()
    return cleaned


def build_feedback(report: Any, max_items: int = 20) -> list[FeedbackItem]:
    """Extract, filter, sanitize, and deterministically sort rewritable errors from a validation report."""
    raw_errors = getattr(report, "errors", [])
    items: list[FeedbackItem] = []

    for issue in raw_errors:
        severity = getattr(issue, "severity", "")
        code = getattr(issue, "code", "")
        if severity != "error" or code not in REWRITABLE_ERROR_CODES:
            continue

        raw_path = getattr(issue, "path", "")
        raw_msg = getattr(issue, "message", "")
        raw_evidence = getattr(issue, "evidence", "")

        sanitized_path = sanitize_feedback_text(raw_path)
        sanitized_msg = sanitize_feedback_text(raw_msg)[:300]
        sanitized_ev = sanitize_feedback_text(raw_evidence)[:200]

        items.append(
            FeedbackItem(
                code=code,
                path=sanitized_path,
                message=sanitized_msg,
                evidence=sanitized_ev,
            )
        )

    # Deterministic sort order
    items.sort(key=lambda x: (x.path, x.code, x.message, x.evidence))
    return items[:max_items]


def feedback_signature(items: Sequence[FeedbackItem]) -> str:
    """Compute a deterministic SHA-256 signature for a sequence of feedback items."""
    canonical_list = [
        (item.code, item.path, item.message, item.evidence)
        for item in sorted(items, key=lambda x: (x.path, x.code, x.message, x.evidence))
    ]
    serialized = json.dumps(canonical_list, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def groups_for(items: Sequence[FeedbackItem]) -> set[str]:
    """Map feedback item path prefixes to editorial prompt groups ('written', 'scripts')."""
    if not items:
        return set()

    groups: set[str] = set()
    for item in items:
        p = item.path
        if p.startswith("digest") or p.startswith("captions"):
            groups.add("written")
        elif p.startswith("podcast") or p.startswith("video"):
            groups.add("scripts")
        else:
            # Unknown or empty prefix affects both calls
            groups.add("written")
            groups.add("scripts")

    return groups
