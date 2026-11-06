"""Public reader editorial portal and static site generation engine for CyberPulse AI.

Generates a rich, full-featured cybersecurity news publication website styled after
The Hacker News with Google Material Symbols (opsz=24, wght=400, FILL=0, GRAD=0),
2-column layout (70% main, 30% sticky sidebar), dedicated single article pages,
threat radar, trending stories, interactive podcast radio box, and reading progress bar.
Zero external CDN JS dependencies, offline & online compatibility, NO JSON FEED.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import html
import logging
from pathlib import Path
import re
import shutil
from typing import Any

from cyberpulse.models import Article, EditorialPackage, ResearchResult

logger = logging.getLogger("cyberpulse.portal")


@dataclass
class PortalBuildResult:
    """Result of public reader portal generation."""

    dist_dir: Path
    files_created: list[str]
    editions_count: int
    featured_run_id: str
    has_audio: bool


# Official GitHub Repository and GitHub Pages CDN endpoints
GITHUB_REPO_URL: str = "https://github.com/DuwcsAnh1710/cybernews-agentic-ai"
GITHUB_PAGES_CDN_URL: str = "https://duwcsanh1710.github.io/cybernews-agentic-ai"

# Inline SVG fallback for GitHub Octocat logo (18x18 / 24x24 compatible)
GITHUB_SVG_ICON: str = (
    '<svg class="github-svg-icon" viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true">'
    '<path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 '
    '0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695'
    '-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99'
    '.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225'
    '-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405'
    'c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605'
    '-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57'
    'A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/>'
    '</svg>'
)

# Google Material Symbols Outlined stylesheet URL
MATERIAL_SYMBOLS_STYLESHEET: str = (
    "https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@24,400,0,0"
)

# Inline SVG fallbacks for Google Material Symbols (strictly 24x24 px)
MATERIAL_FALLBACK_SVGS: dict[str, str] = {
    "shield": (
        '<svg class="icon-fallback" viewBox="0 0 24 24" width="24" height="24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>'
    ),
    "dark_mode": (
        '<svg class="icon-fallback" viewBox="0 0 24 24" width="24" height="24" fill="currentColor">'
        '<path d="M12 3a9 9 0 1 0 9 9c0-.46-.04-.92-.1-1.36a5.389 5.389 0 0 1-4.4 2.26 '
        '5.403 5.403 0 0 1-3.14-9.8c-.44-.06-.9-.1-1.36-.1z"></path></svg>'
    ),
    "light_mode": (
        '<svg class="icon-fallback" viewBox="0 0 24 24" width="24" height="24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="5"></circle>'
        '<line x1="12" y1="1" x2="12" y2="3"></line><line x1="12" y1="21" x2="12" y2="23"></line>'
        '<line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line>'
        '<line x1="1" y1="12" x2="3" y2="12"></line><line x1="21" y1="12" x2="23" y2="12"></line>'
        '<line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line></svg>'
    ),
    "timer": (
        '<svg class="icon-fallback" viewBox="0 0 24 24" width="24" height="24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="13" r="8"></circle>'
        '<line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="13" x2="15" y2="15"></line>'
        '<line x1="9" y1="2" x2="15" y2="2"></line></svg>'
    ),
    "person": (
        '<svg class="icon-fallback" viewBox="0 0 24 24" width="24" height="24" fill="currentColor">'
        '<path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"></path></svg>'
    ),
    "schedule": (
        '<svg class="icon-fallback" viewBox="0 0 24 24" width="24" height="24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="9"></circle>'
        '<polyline points="12 7 12 12 15 15"></polyline></svg>'
    ),
    "share": (
        '<svg class="icon-fallback" viewBox="0 0 24 24" width="24" height="24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="18" cy="5" r="3"></circle><circle cx="6" cy="12" r="3"></circle>'
        '<circle cx="18" cy="19" r="3"></circle>'
        '<line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line>'
        '<line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line></svg>'
    ),
    "content_copy": (
        '<svg class="icon-fallback" viewBox="0 0 24 24" width="24" height="24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>'
        '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>'
    ),
    "verified": (
        '<svg class="icon-fallback" viewBox="0 0 24 24" width="24" height="24" fill="currentColor">'
        '<path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm-2 16l-4-4 1.41-1.41L10 14.17l6.59-6.59L18 9l-8 8z"></path></svg>'
    ),
    "open_in_new": (
        '<svg class="icon-fallback" viewBox="0 0 24 24" width="24" height="24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>'
        '<polyline points="15 3 21 3 21 9"></polyline>'
        '<line x1="10" y1="14" x2="21" y2="3"></line></svg>'
    ),
    "play_circle": (
        '<svg class="icon-fallback" viewBox="0 0 24 24" width="24" height="24" fill="currentColor">'
        '<path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 14.5v-9l6 4.5-6 4.5z"></path></svg>'
    ),
    "pause_circle": (
        '<svg class="icon-fallback" viewBox="0 0 24 24" width="24" height="24" fill="currentColor">'
        '<path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 14H9V8h2v8zm4 0h-2V8h2v8z"></path></svg>'
    ),
    "trending_up": (
        '<svg class="icon-fallback" viewBox="0 0 24 24" width="24" height="24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<polyline points="23 6 13.5 15.5 8.5 10.5 1 18"></polyline>'
        '<polyline points="17 6 23 6 23 12"></polyline></svg>'
    ),
    "bug_report": (
        '<svg class="icon-fallback" viewBox="0 0 24 24" width="24" height="24" fill="currentColor">'
        '<path d="M20 8h-2.81c-.45-.78-1.07-1.45-1.82-1.96L17 4.41 15.59 3l-2.17 2.17C12.96 5.06 12.49 5 12 5c-.49 0-.96.06-1.41.17L8.41 3 7 4.41l1.62 1.63C7.88 6.55 7.26 7.22 6.81 8H4v2h2.09c-.05.33-.09.66-.09 1v1H4v2h2v1c0 .34.04.67.09 1H4v2h2.81c1.04 1.79 2.97 3 5.19 3s4.15-1.21 5.19-3H20v-2h-2.09c.05-.33.09-.66.09-1v-1h2v-2h-2v-1c0-.34-.04-.67-.09-1H20V8zm-6 8h-4v-2h4v2zm0-4h-4v-2h4v2z"></path></svg>'
    ),
    "warning": (
        '<svg class="icon-fallback" viewBox="0 0 24 24" width="24" height="24" fill="currentColor">'
        '<path d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z"></path></svg>'
    ),
    "calendar_today": (
        '<svg class="icon-fallback" viewBox="0 0 24 24" width="24" height="24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect>'
        '<line x1="16" y1="2" x2="16" y2="6"></line><line x1="8" y1="2" x2="8" y2="6"></line>'
        '<line x1="3" y1="10" x2="21" y2="10"></line></svg>'
    ),
    "search": (
        '<svg class="icon-fallback" viewBox="0 0 24 24" width="24" height="24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="11" cy="11" r="8"></circle>'
        '<line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>'
    ),
    "podcasts": (
        '<svg class="icon-fallback" viewBox="0 0 24 24" width="24" height="24" fill="currentColor">'
        '<path d="M12 1a9 9 0 0 0-9 9v7c0 1.66 1.34 3 3 3h3v-8H5v-2c0-3.87 3.13-7 7-7s7 3.13 7 7v2h-4v8h3c1.66 0 3-1.34 3-3v-7a9 9 0 0 0-9-9z"></path></svg>'
    ),
    "arrow_back": (
        '<svg class="icon-fallback" viewBox="0 0 24 24" width="24" height="24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<line x1="19" y1="12" x2="5" y2="12"></line>'
        '<polyline points="12 19 5 12 12 5"></polyline></svg>'
    ),
    "arrow_forward": (
        '<svg class="icon-fallback" viewBox="0 0 24 24" width="24" height="24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<line x1="5" y1="12" x2="19" y2="12"></line>'
        '<polyline points="12 5 19 12 12 19"></polyline></svg>'
    ),
    "speed": (
        '<svg class="icon-fallback" viewBox="0 0 24 24" width="24" height="24" fill="currentColor">'
        '<path d="M20.38 8.57l-1.23 1.85a8 8 0 0 1-.22 7.58H5.07A8 8 0 0 1 15.58 6.85l1.85-1.23A10 10 0 0 0 3.35 19a2 2 0 0 0 1.72 1h13.85a2 2 0 0 0 1.74-1 10 10 0 0 0-.28-10.43zM10.59 15.41a2 2 0 0 0 2.83 0l5.66-8.49-8.49 5.66a2 2 0 0 0 0 2.83z"></path></svg>'
    ),
    "headphones": (
        '<svg class="icon-fallback" viewBox="0 0 24 24" width="24" height="24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M3 18v-6a9 9 0 0 1 18 0v6"></path>'
        '<path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"></path></svg>'
    ),
    "visibility": (
        '<svg class="icon-fallback" viewBox="0 0 24 24" width="24" height="24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>'
        '<circle cx="12" cy="12" r="3"></circle></svg>'
    ),
    "format_size": (
        '<svg class="icon-fallback" viewBox="0 0 24 24" width="24" height="24" fill="currentColor">'
        '<path d="M9 4v3h5v12h3V7h5V4H9zm-6 8h3v7h3v-7h3V9H3v3z"></path></svg>'
    ),
    "bolt": (
        '<svg class="icon-fallback" viewBox="0 0 24 24" width="24" height="24" fill="currentColor">'
        '<path d="M11 21h-1l1-7H7.5c-.88 0-.33-.75-.31-.78C8.48 10.94 10.42 7.54 13 3h1l-1 7h3.5c.49 0 .77.34.64.67l-6.14 10.33z"></path></svg>'
    ),
    "calendar_month": (
        '<svg class="icon-fallback" viewBox="0 0 24 24" width="24" height="24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect>'
        '<line x1="16" y1="2" x2="16" y2="6"></line><line x1="8" y1="2" x2="8" y2="6"></line>'
        '<line x1="3" y1="10" x2="21" y2="10"></line>'
        '<circle cx="8" cy="14" r="1" fill="currentColor"></circle>'
        '<circle cx="12" cy="14" r="1" fill="currentColor"></circle>'
        '<circle cx="16" cy="14" r="1" fill="currentColor"></circle>'
        '<circle cx="8" cy="18" r="1" fill="currentColor"></circle>'
        '<circle cx="12" cy="18" r="1" fill="currentColor"></circle></svg>'
    ),
    "smart_display": (
        '<svg class="icon-fallback" viewBox="0 0 24 24" width="24" height="24" fill="currentColor">'
        '<path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm-10 12.5v-9l6 4.5-6 4.5z"></path></svg>'
    ),
    "code": (
        '<svg class="icon-fallback" viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<polyline points="16 18 22 12 16 6"></polyline><polyline points="8 6 2 12 8 18"></polyline></svg>'
    ),
}


def render_material_symbol(name: str, extra_class: str = "") -> str:
    """Render Google Material Symbols Outlined icon with strict 24x24 px sizing and fallback inline SVG."""
    cls_str = f"material-symbols-outlined {extra_class}".strip()
    fallback_svg = MATERIAL_FALLBACK_SVGS.get(name, "")
    return (
        f'<span class="{cls_str}" style="font-size: 24px; width: 24px; height: 24px;" aria-hidden="true">'
        f'{fallback_svg}{name}</span>'
    )


KNOWN_VENDORS: list[str] = [
    "Palo Alto",
    "VMware",
    "Chrome",
    "Ivanti",
    "LXD",
    "MicroCloud",
    "Microsoft",
    "Windows",
    "Linux",
    "Apple",
    "macOS",
    "iOS",
    "Android",
    "Cisco",
    "Fortinet",
    "Google",
    "AWS",
    "Cloudflare",
    "OpenSSL",
    "Citrix",
    "Apache",
    "Atlassian",
    "Telegram",
    "Reddit",
    "Binance",
    "GitHub",
    "GitLab",
    "Docker",
    "Kubernetes",
    "Splunk",
    "Oracle",
    "SAP",
    "CrowdStrike",
    "Check Point",
    "SonicWall",
    "Brotli",
    "Sophos",
    "Juniper",
    "Qualys",
    "Tenable",
    "Okta",
]


@dataclass
class ThreatBadge:
    """Badge representation for an identified threat severity."""

    label: str
    css_class: str
    text: str


def detect_threat_badge(text_corpus: str) -> ThreatBadge:
    """Analyze article content and return appropriate severity badge."""
    lower = text_corpus.lower()
    # 1. Patch / Remediation
    if any(k in lower for k in [
        "bản vá",
        "khắc phục",
        "vá lỗi",
        "cập nhật",
        "patch",
        "hotfix",
        "update",
        "fix",
        "mitigat",
    ]):
        return ThreatBadge(label="[Bản vá]", css_class="badge-patch", text="Bản vá")
    # 2. Critical Threat / Zero-day
    if any(k in lower for k in [
        "cvss 10",
        "cvss 9",
        "khẩn cấp",
        "critical",
        "zero-day",
        "0-day",
        "rce",
        "remote code execution",
    ]):
        return ThreatBadge(label="[Khẩn cấp]", css_class="badge-critical", text="Khẩn cấp")
    # 3. High Warning / Active Exploitation
    if any(k in lower for k in [
        "cvss 8",
        "cvss 7",
        "cảnh báo",
        "nguy hiểm",
        "warning",
        "high",
        "exploit",
        "tấn công",
        "khai thác",
        "bypass",
        "root access",
    ]):
        return ThreatBadge(label="[Cảnh báo cao]", css_class="badge-warning", text="Cảnh báo cao")
    # 4. General Security Information
    return ThreatBadge(label="[Thông tin]", css_class="badge-info", text="Thông tin")


def extract_vendor_tags(text_corpus: str, topics: list[str] | None = None) -> list[str]:
    """Extract vendor/software tags found in the story and topics."""
    tags: list[str] = []
    lower = text_corpus.lower()
    for vendor in KNOWN_VENDORS:
        pattern = r"\b" + re.escape(vendor.lower()) + r"\b"
        if re.search(pattern, lower) and vendor not in tags:
            tags.append(vendor)
    if topics:
        for t in topics:
            clean_t = t.strip()
            if clean_t and clean_t.title() not in tags and len(clean_t) > 2:
                tags.append(clean_t.title())
    return tags[:5]


def extract_cves(text_corpus: str) -> list[str]:
    """Extract unique CVE identifiers from text corpus."""
    matches = re.findall(r"\bCVE-\d{4}-\d{4,7}\b", text_corpus, re.IGNORECASE)
    seen: set[str] = set()
    result: list[str] = []
    for m in matches:
        up = m.upper()
        if up not in seen:
            seen.add(up)
            result.append(up)
    return result


def calculate_reading_time(text: str) -> int:
    """Calculate reading time in minutes (minimum 2 minutes for editorial depth)."""
    words = len(text.split())
    minutes = max(2, round(words / 180))
    return minutes


def parse_run_timestamp(run_id: str, file_path: Path | None = None) -> datetime:
    """Parse UTC datetime from run identifier or fall back to file mtime."""
    match = re.search(r"^(?:run|standalone)-(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z", run_id)
    if match:
        y, m, d, hh, mm, ss = match.groups()
        return datetime(int(y), int(m), int(d), int(hh), int(mm), int(ss), tzinfo=timezone.utc)
    if file_path and file_path.exists():
        return datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
    return datetime.fromtimestamp(0, tz=timezone.utc)


def format_vietnam_time(dt: datetime) -> str:
    """Format UTC datetime as Vietnam Time (UTC+7) 'DD/MM/YYYY HH:MM'."""
    vn_tz = timezone(timedelta(hours=7))
    dt_vn = dt.astimezone(vn_tz)
    return dt_vn.strftime("%d/%m/%Y %H:%M")


def format_vietnam_date(dt: datetime) -> str:
    """Format UTC datetime as Vietnam Date (UTC+7) 'DD/MM/YYYY'."""
    vn_tz = timezone(timedelta(hours=7))
    dt_vn = dt.astimezone(vn_tz)
    return dt_vn.strftime("%d/%m/%Y")


def calculate_baseline_views(seed_str: str) -> int:
    """Calculate deterministic baseline view count between 800 and 2,500 based on seed hash."""
    h = 0
    for ch in seed_str:
        h = ((h << 5) - h) + ord(ch)
        h &= 0xFFFFFFFF
    return 800 + (h % 1701)


def extract_tldr_bullets(
    article: Article,
    relevance_reason: str,
    matching_section_texts: list[str],
) -> list[str]:
    """Extract 3 concise executive bullet takeaways from article and editorial synthesis."""
    bullets: list[str] = []

    corpus = f"{article.title} {article.summary} {relevance_reason} {' '.join(matching_section_texts)}"
    cves = extract_cves(corpus)
    vendor_tags = extract_vendor_tags(corpus, topics=article.topics_matched)

    # Bullet 1: Core threat & vulnerability details
    cve_part = f" ({', '.join(cves[:2])})" if cves else ""
    tech_part = f" trên hệ thống {', '.join(vendor_tags[:2])}" if vendor_tags else ""
    if article.summary:
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", article.summary.strip()) if len(s.strip()) > 15]
        if sentences:
            bullets.append(sentences[0])
    if not bullets:
        bullets.append(f"Phát hiện rủi ro an ninh mạng{cve_part}{tech_part} ảnh hưởng trực tiếp tới hệ thống.")

    # Bullet 2: Exploitation vector & editorial impact
    if relevance_reason:
        bullets.append(f"Đánh giá phân tích: {relevance_reason.strip()}")
    elif len(matching_section_texts) > 0:
        sec_text = matching_section_texts[0].split(":", 1)[-1].strip()
        sec_sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", sec_text) if len(s.strip()) > 15]
        if sec_sentences:
            bullets.append(sec_sentences[0])
        else:
            bullets.append("Kẻ tấn công có thể lợi dụng sơ hở để leo thang đặc quyền hoặc thực thi mã từ xa.")
    else:
        bullets.append("Lỗ hổng cho phép kẻ tấn công vượt qua rào cản kiểm soát và can thiệp tiến trình hệ thống.")

    # Bullet 3: Mitigation recommendation
    badge = detect_threat_badge(corpus)
    if "patch" in badge.css_class:
        bullets.append("Nhà sản xuất đã phát hành bản vá bảo mật khẩn cấp, khuyến nghị quản trị viên cập nhật ngay lập tức.")
    elif "critical" in badge.css_class:
        bullets.append("Mức độ đe dọa khẩn cấp (CVSS cao), cần cô lập các máy chủ bị ảnh hưởng và giám sát lưu lượng bất thường.")
    else:
        bullets.append("Cần kích hoạt giám sát hành vi trên giải pháp EDR/SIEM và rà soát đặc quyền truy cập tài khoản người dùng.")

    # Guarantee exactly 3 bullets
    while len(bullets) < 3:
        bullets.append("Theo dõi cập nhật từ các kênh cảnh báo an ninh mạng chính thống để kịp thời phản ứng sự cố.")

    return bullets[:3]


def _render_tldr_box(bullets: list[str]) -> str:
    """Render executive TL;DR highlight box with Google Material Symbol bolt (strictly 24x24 px)."""
    bolt_icon = render_material_symbol("bolt")
    items_html = "".join(f"<li>{html.escape(b)}</li>" for b in bullets[:3])
    return f"""<div class="tldr-box">
  <div class="tldr-header">{bolt_icon} <strong>Tóm tắt nhanh (TL;DR)</strong></div>
  <ul class="tldr-list">
    {items_html}
  </ul>
</div>"""


def _render_article_pagination(
    prev_article: Article | None,
    next_article: Article | None,
) -> str:
    """Render 2-column continuous reading navigation cards (Bài trước / Bài kế tiếp)."""
    arrow_back_icon = render_material_symbol("arrow_back")
    arrow_forward_icon = render_material_symbol("arrow_forward")

    prev_html = ""
    if prev_article:
        prev_html = f"""<a href="{prev_article.id}.html" class="pagination-card pagination-card-prev" rel="prev">
  <div class="pagination-sublabel">{arrow_back_icon} <span>Bài trước</span></div>
  <div class="pagination-title">{html.escape(prev_article.title)}</div>
</a>"""
    else:
        prev_html = '<div class="pagination-placeholder"></div>'

    next_html = ""
    if next_article:
        next_html = f"""<a href="{next_article.id}.html" class="pagination-card pagination-card-next" rel="next">
  <div class="pagination-sublabel"><span>Bài kế tiếp</span> {arrow_forward_icon}</div>
  <div class="pagination-title">{html.escape(next_article.title)}</div>
</a>"""
    else:
        next_html = '<div class="pagination-placeholder"></div>'

    return f"""<nav class="article-pagination" aria-label="Điều hướng bài viết">
  {prev_html}
  {next_html}
</nav>"""


def _render_filter_bar(
    total_count: int,
    critical_count: int,
    warning_count: int,
    patch_count: int,
) -> str:
    """Render interactive filter taskbar with live category counts and podcast quick button."""
    podcasts_icon = render_material_symbol("podcasts")
    return f"""<div class="filter-taskbar">
  <div class="filter-pills">
    <button class="filter-pill active" data-filter="all">Tất cả ({total_count})</button>
    <button class="filter-pill" data-filter="critical"><span class="status-dot dot-critical" role="status"></span> Khẩn cấp ({critical_count})</button>
    <button class="filter-pill" data-filter="warning"><span class="status-dot dot-warning" role="status"></span> Cảnh báo cao ({warning_count})</button>
    <button class="filter-pill" data-filter="patch"><span class="status-dot dot-patch" role="status"></span> Bản vá ({patch_count})</button>
  </div>
  <a href="#podcast-radio-box" class="podcast-quick-btn">
    {podcasts_icon} <span>Nghe Podcast số này</span>
  </a>
</div>"""


def _render_article_utility_bar(
    parent_edition_link: str,
    edition_date: str,
    baseline_views: int = 1248,
    github_url: str = GITHUB_REPO_URL,
) -> str:
    """Render single article reading utility bar with back-to-edition link, GitHub repo badge, font resizer, and view counter."""
    arrow_back_icon = render_material_symbol("arrow_back")
    visibility_icon = render_material_symbol("visibility")
    open_icon = render_material_symbol("open_in_new", extra_class="icon-util-ext")
    formatted_views = f"{baseline_views:,}"
    return f"""<div class="article-utility-bar">
  <a href="../{parent_edition_link}" class="back-edition-btn">
    {arrow_back_icon} <span>Về bản tin ngày {edition_date}</span>
  </a>
  <div class="utility-actions">
    <a href="{github_url}" target="_blank" rel="noopener noreferrer" class="github-util-btn" title="Xem chi tiết &amp; mã nguồn trên GitHub">
      {GITHUB_SVG_ICON}
      <span>GitHub Repo</span>
      {open_icon}
    </a>
    <div class="font-resizer">
      <button id="font-decrease" title="Giảm cỡ chữ" aria-label="Giảm cỡ chữ">A-</button>
      <button id="font-increase" title="Tăng cỡ chữ" aria-label="Tăng cỡ chữ">A+</button>
    </div>
    <span class="meta-item views-counter">
      {visibility_icon} <span id="article-views-count">{formatted_views}</span> lượt xem
    </span>
  </div>
</div>"""


def _render_edition_strip(
    editions_data: list[dict[str, Any]],
    active_run_id: str = "",
    base_prefix: str = "",
    limit: int = 5,
) -> str:
    """Render horizontal daily edition strip below main header for 1-click date switching."""
    calendar_month_icon = render_material_symbol("calendar_month")
    date_pills: list[str] = []
    archive_link = f"{base_prefix}archive.html"

    for idx, ed in enumerate(editions_data[:limit]):
        pkg = ed["package"]
        ed_date_str = format_vietnam_date(ed["dt"])
        is_active = (pkg.run_id == active_run_id)
        active_cls = " active" if is_active else ""
        current_attr = ' aria-current="page"' if is_active else ""
        link = f"{base_prefix}editions/{pkg.run_id}.html"
        label_suffix = " (Mới nhất)" if idx == 0 else ""
        date_pills.append(
            f'<a href="{link}" class="strip-date-pill{active_cls}"{current_attr}>'
            f'{ed_date_str}{label_suffix}</a>'
        )

    pills_html = "\n        ".join(date_pills)
    return f"""<div class="edition-strip-wrap">
  <div class="container edition-strip-inner">
    <span class="strip-label">{calendar_month_icon} Chọn ngày xem tin:</span>
    <div class="strip-dates">
      {pills_html}
      <a href="{archive_link}" class="strip-date-all"><div class="strip-date-all-inner">Tất cả các ngày &rarr;</div></a>
    </div>
  </div>
</div>"""


def _excerpt_text(text: str, max_chars: int = 120) -> str:
    """Return a clean magazine excerpt at a word boundary when text exceeds max_chars."""
    clean = text.strip()
    if len(clean) <= max_chars:
        return clean
    first_sentence = re.split(r"(?<=[.!?])\s+", clean)[0]
    if len(first_sentence) <= max_chars:
        return first_sentence
    cut = clean[:max_chars].rsplit(" ", 1)[0].rstrip(".,;:")
    return f"{cut}…"


def _get_style_css() -> str:
    """Return standalone CSS styled in the Hallmark Editorial theme with 2-column layout and 24x24 icons."""
    return """/* Hallmark · editorial · dealt 1/3 · daily-vietnamese-cybersecurity-intelligence-97f9·5dfcf566·2026-11-06·r0 · draw 0980c5 */
@import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wdth,wght@12..96,75..100,400..800&family=Source+Serif+4:ital,opsz,wght@0,8..60,400..700;1,8..60,400..700&display=swap');

[data-theme="dark"] {
  --color-paper: oklch(15% 0.010 265);
  --color-paper-2: oklch(19% 0.012 265);
  --color-paper-3: oklch(23% 0.014 265);
  --color-ink: oklch(95% 0.007 86);
  --color-ink-2: oklch(84% 0.009 86);
  --color-muted: oklch(68% 0.010 80);
  --color-rule: oklch(28% 0.012 265);
  --color-rule-2: oklch(42% 0.014 265);
  --color-accent: oklch(74% 0.140 152);
  --color-accent-ink: oklch(15% 0.010 265);
  --color-accent-tint: oklch(24% 0.045 152);
  --color-danger: oklch(76% 0.155 24);
  --color-danger-bg: oklch(23% 0.050 24);
  --color-danger-border: oklch(42% 0.105 24);
  --color-warn: oklch(80% 0.135 76);
  --color-warn-bg: oklch(24% 0.045 76);
  --color-warn-border: oklch(44% 0.095 76);
  --color-ok: oklch(75% 0.140 152);
  --color-ok-bg: oklch(23% 0.045 152);
  --color-ok-border: oklch(42% 0.095 152);
  --color-info: oklch(76% 0.115 245);
  --color-info-bg: oklch(23% 0.040 245);
  --color-info-border: oklch(42% 0.085 245);
  --color-banner-bg: oklch(20% 0.016 265);
  --color-banner-ink: oklch(96% 0.007 86);
  --color-banner-muted: oklch(82% 0.010 86);
  --color-banner-accent: oklch(76% 0.140 152);
  --color-video-bg: oklch(11% 0.010 265);
}

:root, [data-theme="light"] {
  --color-paper: oklch(97% 0.007 86);
  --color-paper-2: oklch(94% 0.010 86);
  --color-paper-3: oklch(91% 0.012 84);
  --color-ink: oklch(16% 0.012 280);
  --color-ink-2: oklch(29% 0.011 280);
  --color-muted: oklch(44% 0.010 75);
  --color-rule: oklch(84% 0.009 85);
  --color-rule-2: oklch(62% 0.010 80);
  --color-accent: oklch(43% 0.130 152);
  --color-accent-ink: oklch(98% 0.005 88);
  --color-accent-tint: oklch(93% 0.032 152);
  --color-danger: oklch(46% 0.180 26);
  --color-danger-bg: oklch(95% 0.030 26);
  --color-danger-border: oklch(80% 0.085 26);
  --color-warn: oklch(45% 0.130 70);
  --color-warn-bg: oklch(95% 0.035 78);
  --color-warn-border: oklch(82% 0.080 75);
  --color-ok: oklch(43% 0.130 152);
  --color-ok-bg: oklch(94% 0.032 152);
  --color-ok-border: oklch(80% 0.075 152);
  --color-info: oklch(43% 0.115 245);
  --color-info-bg: oklch(94% 0.025 245);
  --color-info-border: oklch(81% 0.065 245);
  --color-banner-bg: oklch(18% 0.016 265);
  --color-banner-ink: oklch(97% 0.007 86);
  --color-banner-muted: oklch(82% 0.012 86);
  --color-banner-accent: oklch(76% 0.140 152);
  --color-video-bg: oklch(12% 0.010 265);

  --bg-primary: var(--color-paper);
  --bg-surface: var(--color-paper-2);
  --bg-card: var(--color-paper);
  --bg-card-hover: var(--color-paper-2);
  --text-primary: var(--color-ink);
  --text-secondary: var(--color-ink-2);
  --text-muted: var(--color-muted);
  --accent-cyber: var(--color-accent);
  --accent-cyber-glow: var(--color-accent-tint);
  --accent-cyber-subtle: var(--color-paper-2);
  --border-color: var(--color-rule);
  --border-color-glow: var(--color-rule-2);
  --badge-critical-bg: var(--color-danger-bg);
  --badge-critical-text: var(--color-danger);
  --badge-critical-border: var(--color-danger-border);
  --badge-warning-bg: var(--color-warn-bg);
  --badge-warning-text: var(--color-warn);
  --badge-warning-border: var(--color-warn-border);
  --badge-patch-bg: var(--color-ok-bg);
  --badge-patch-text: var(--color-ok);
  --badge-patch-border: var(--color-ok-border);
  --badge-info-bg: var(--color-info-bg);
  --badge-info-text: var(--color-info);
  --badge-info-border: var(--color-info-border);
  --tag-bg: var(--color-paper-2);
  --tag-text: var(--color-ink-2);
  --tag-border: var(--color-rule);
  --shadow: none;
  --shadow-lg: none;

  --font-display: "Bricolage Grotesque", ui-sans-serif, system-ui, sans-serif;
  --font-body: "Source Serif 4", ui-serif, Georgia, serif;
  --font-icon: "Material Symbols Outlined";
  --font-sans: var(--font-display);
  --font-serif: var(--font-body);
  --font-mono: var(--font-display);

  --rule-thin: 1px;
  --rule-heavy: 4px;
  --ease-out: cubic-bezier(0.22, 1, 0.36, 1);
  --dur-fast: 120ms;
  --dur-short: 160ms;
  --dur-med: 240ms;
}

*, *::before, *::after {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

html {
  color-scheme: light dark;
  caret-color: var(--accent-cyber);
  accent-color: var(--accent-cyber);
  scroll-behavior: smooth;
  -webkit-text-size-adjust: 100%;
  overflow-x: clip;
  background-color: var(--bg-primary);
  color: var(--text-primary);
}

body {
  font-family: var(--font-body);
  background-color: var(--bg-primary);
  color: var(--text-primary);
  line-height: 1.65;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  overflow-x: clip;
  font-kerning: normal;
  font-feature-settings: "kern" 1, "liga" 1, "onum" 1;
  transition: background-color var(--dur-med) var(--ease-out), color var(--dur-med) var(--ease-out);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  text-rendering: optimizeLegibility;
}

img, svg, video {
  display: block;
  max-width: 100%;
}

::selection {
  background: var(--color-accent-tint);
  color: var(--color-ink);
}

/* Interactive Control States (Focus, Hover, Active, Disabled) */
a:focus-visible,
button:focus-visible,
input:focus-visible,
video:focus-visible {
  outline: 2px solid var(--color-ink);
  outline-offset: 2px;
}

button:active,
.btn-read-more:active,
.filter-pill:active,
.podcast-quick-btn:active,
.strip-date-pill:active,
.github-header-btn:active,
.github-util-btn:active,
.btn-github-action:active,
.theme-toggle-btn:active,
.share-btn:active {
  transform: translateY(1px);
}

button:disabled,
input:disabled,
[aria-disabled="true"] {
  opacity: 0.45;
  cursor: not-allowed;
  pointer-events: none;
}

.container {
  width: 100%;
  max-width: 1280px;
  margin: 0 auto;
  padding: 0 24px;
}

/* Google Material Symbols (Strictly 24x24 px) */
.material-symbols-outlined {
  font-family: var(--font-icon);
  font-weight: normal;
  font-style: normal;
  font-size: 24px;
  width: 24px;
  height: 24px;
  line-height: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  vertical-align: middle;
  letter-spacing: normal;
  text-transform: none;
  white-space: nowrap;
  word-wrap: normal;
  direction: ltr;
  -webkit-font-feature-settings: 'liga';
  -webkit-font-smoothing: antialiased;
  flex-shrink: 0;
}

.material-symbols-outlined .icon-fallback {
  display: none;
  width: 24px;
  height: 24px;
}

.fonts-failed .material-symbols-outlined {
  font-size: 0;
}

.fonts-failed .material-symbols-outlined .icon-fallback {
  display: inline-block;
  width: 24px;
  height: 24px;
}

/* Editorial Masthead Header */
.site-header {
  background-color: var(--bg-primary);
  border-top: var(--rule-heavy) solid var(--color-ink);
  border-bottom: var(--rule-thin) solid var(--border-color);
  position: sticky;
  top: 0;
  z-index: 1000;
}

.header-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 0;
  gap: 20px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 16px;
  text-decoration: none;
  color: var(--text-primary);
  transition: opacity var(--dur-short) var(--ease-out);
}

.brand:hover {
  opacity: 0.86;
}

.brand-icon {
  width: 40px;
  height: 40px;
  color: var(--color-accent-ink);
  background: var(--color-ink);
  border: var(--rule-thin) solid var(--color-ink);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background-color var(--dur-short) var(--ease-out), border-color var(--dur-short) var(--ease-out);
}

.brand:hover .brand-icon {
  background: var(--accent-cyber);
  border-color: var(--accent-cyber);
}

.brand-text h1 {
  font-family: var(--font-display);
  font-size: 1.5rem;
  font-weight: 800;
  font-stretch: 85%;
  letter-spacing: -0.025em;
  color: var(--text-primary);
  line-height: 1.05;
  text-wrap: balance;
}

.brand-text p {
  font-family: var(--font-display);
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--text-muted);
  letter-spacing: 0.01em;
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
}

.brand-live-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background-color: var(--color-ok);
  display: inline-block;
  flex-shrink: 0;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.search-box {
  position: relative;
  width: 280px;
}

.search-input {
  width: 100%;
  padding: 8px 12px 8px 40px;
  background-color: var(--bg-surface);
  border: var(--rule-thin) solid var(--border-color);
  color: var(--text-primary);
  font-family: var(--font-display);
  font-size: 0.875rem;
  transition: border-color var(--dur-short) var(--ease-out), background-color var(--dur-short) var(--ease-out);
}

.search-input::placeholder {
  color: var(--text-muted);
  font-size: 0.875rem;
}

.search-input:hover {
  border-color: var(--color-rule-2);
}

.search-input:focus {
  border-color: var(--color-ink);
  background-color: var(--bg-primary);
}

.search-icon-pos {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-muted);
  pointer-events: none;
  display: flex;
  align-items: center;
  justify-content: center;
}

.theme-toggle-btn {
  background: var(--bg-surface);
  border: var(--rule-thin) solid var(--border-color);
  color: var(--text-primary);
  width: 40px;
  height: 40px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: border-color var(--dur-short) var(--ease-out), background-color var(--dur-short) var(--ease-out), color var(--dur-short) var(--ease-out);
  flex-shrink: 0;
}

.theme-toggle-btn:hover {
  border-color: var(--color-ink);
  background-color: var(--color-ink);
  color: var(--color-paper);
}

[data-theme="dark"] .icon-dark-mode { display: none; }
[data-theme="dark"] .icon-light-mode { display: inline-flex; }
[data-theme="light"] .icon-light-mode { display: none; }
[data-theme="light"] .icon-dark-mode { display: inline-flex; }

/* Navigation Bar */
.nav-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 0;
  overflow-x: auto;
  white-space: nowrap;
  border-top: var(--rule-thin) solid var(--border-color);
}

.nav-link {
  font-family: var(--font-display);
  color: var(--text-secondary);
  text-decoration: none;
  font-size: 0.875rem;
  font-weight: 600;
  padding: 8px 12px;
  border-bottom: 2px solid transparent;
  transition: color var(--dur-short) var(--ease-out), border-color var(--dur-short) var(--ease-out);
}

.nav-link:hover, .nav-link.active {
  color: var(--accent-cyber);
  border-bottom-color: var(--accent-cyber);
}

/* Horizontal Daily Edition Strip */
.edition-strip-wrap {
  background-color: var(--bg-surface);
  border-bottom: var(--rule-thin) solid var(--border-color);
  padding: 8px 0;
}

.edition-strip-inner {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: 16px;
  overflow-x: auto;
  scrollbar-width: none;
  -ms-overflow-style: none;
}

.edition-strip-inner::-webkit-scrollbar {
  display: none;
}

.strip-label {
  font-family: var(--font-display);
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 0.875rem;
  font-weight: 700;
  color: var(--text-primary);
  white-space: nowrap;
  flex-shrink: 0;
}

.strip-label .material-symbols-outlined {
  color: var(--accent-cyber);
}

.strip-dates {
  display: flex;
  align-items: center;
  gap: 8px;
  overflow-x: auto;
  white-space: nowrap;
  padding: 4px 0;
}

.strip-date-pill {
  display: inline-flex;
  align-items: center;
  font-size: 0.8125rem;
  font-weight: 600;
  font-family: var(--font-display);
  font-variant-numeric: lining-nums tabular-nums;
  padding: 4px 12px;
  background-color: var(--bg-primary);
  border: var(--rule-thin) solid var(--border-color);
  color: var(--text-secondary);
  text-decoration: none;
  transition: border-color var(--dur-short) var(--ease-out), color var(--dur-short) var(--ease-out), background-color var(--dur-short) var(--ease-out);
  flex-shrink: 0;
}

.strip-date-pill:hover {
  border-color: var(--color-ink);
  color: var(--text-primary);
}

.strip-date-pill.active {
  background-color: var(--color-ink);
  border-color: var(--color-ink);
  color: var(--color-paper);
  font-weight: 700;
}

.strip-date-all {
  font-family: var(--font-display);
  font-size: 0.8125rem;
  font-weight: 700;
  color: var(--accent-cyber);
  text-decoration: none;
  padding: 4px 12px;
  transition: color var(--dur-short) var(--ease-out), background-color var(--dur-short) var(--ease-out);
  white-space: nowrap;
  flex-shrink: 0;
}

.strip-date-all-inner {
  display: inline;
}

.strip-date-all:hover {
  color: var(--text-primary);
  background-color: var(--color-paper-3);
}

/* Interactive Filter Taskbar */
.filter-taskbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 0;
  border-top: var(--rule-thin) solid var(--border-color);
}

.filter-pills {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.filter-pill {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background-color: var(--bg-primary);
  border: var(--rule-thin) solid var(--border-color);
  color: var(--text-secondary);
  font-family: var(--font-display);
  font-variant-numeric: lining-nums tabular-nums;
  font-size: 0.8125rem;
  font-weight: 600;
  padding: 8px 16px;
  cursor: pointer;
  transition: border-color var(--dur-short) var(--ease-out), color var(--dur-short) var(--ease-out), background-color var(--dur-short) var(--ease-out);
}

.filter-pill:hover {
  border-color: var(--color-ink);
  color: var(--text-primary);
  background-color: var(--bg-surface);
}

.filter-pill.active {
  background-color: var(--color-ink);
  color: var(--color-paper);
  border-color: var(--color-ink);
  font-weight: 700;
}

.status-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.dot-critical {
  background-color: var(--color-danger);
}

.dot-warning {
  background-color: var(--color-warn);
}

.dot-patch {
  background-color: var(--color-ok);
}

.podcast-quick-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: transparent;
  border: var(--rule-thin) solid var(--color-ink);
  color: var(--text-primary);
  font-family: var(--font-display);
  font-size: 0.8125rem;
  font-weight: 700;
  padding: 8px 16px;
  text-decoration: none;
  white-space: nowrap;
  transition: background-color var(--dur-short) var(--ease-out), color var(--dur-short) var(--ease-out), border-color var(--dur-short) var(--ease-out);
}

.podcast-quick-btn:hover {
  background: var(--bg-surface);
  border-color: var(--accent-cyber);
  color: var(--accent-cyber);
}

/* Editorial 2-Column Publication Grid */
.portal-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 352px);
  gap: 48px;
  margin-top: 32px;
  margin-bottom: 64px;
}

.portal-main {
  min-width: 0;
}

.portal-sidebar {
  display: flex;
  flex-direction: column;
  gap: 32px;
  position: sticky;
  top: 96px;
  align-self: start;
  border-left: var(--rule-thin) solid var(--border-color);
  padding-left: 32px;
}

/* Lead Feature Story (Hero Card) */
.hero-card {
  background-color: var(--bg-surface);
  color: var(--text-primary);
  border-top: var(--rule-heavy) solid var(--color-ink);
  border-bottom: var(--rule-thin) solid var(--border-color);
  padding: 32px;
  margin-bottom: 40px;
  position: relative;
}

.hero-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 16px;
  font-family: var(--font-display);
  font-variant-numeric: lining-nums tabular-nums;
  font-size: 0.8125rem;
  color: var(--text-muted);
  margin-bottom: 16px;
}

.meta-item {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-family: var(--font-display);
  font-variant-numeric: lining-nums tabular-nums;
}

.meta-item .material-symbols-outlined {
  color: var(--text-muted);
}

.hero-title {
  font-family: var(--font-display);
  font-size: clamp(1.85rem, 1.35rem + 1.8vw, 2.75rem);
  font-weight: 800;
  font-stretch: 85%;
  line-height: 1.08;
  letter-spacing: -0.025em;
  margin-bottom: 16px;
  color: var(--text-primary);
  text-wrap: balance;
}

.hero-title a {
  color: inherit;
  text-decoration: none;
  transition: color var(--dur-short) var(--ease-out);
}

.hero-title a:hover {
  color: var(--accent-cyber);
}

.hero-lead {
  font-family: var(--font-body);
  font-size: 1.125rem;
  color: var(--text-secondary);
  line-height: 1.65;
  max-width: 65ch;
  margin-bottom: 24px;
}

.btn-read-more {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background-color: var(--color-ink);
  color: var(--color-paper);
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 0.875rem;
  padding: 12px 24px;
  text-decoration: none;
  transition: background-color var(--dur-short) var(--ease-out), color var(--dur-short) var(--ease-out);
}

.btn-read-more:hover {
  background-color: var(--accent-cyber);
  color: var(--color-accent-ink);
}

/* Curated Threats Editorial Stream */
.feed-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
  padding-top: 12px;
  padding-bottom: 12px;
  border-top: var(--rule-heavy) solid var(--color-ink);
  border-bottom: var(--rule-thin) solid var(--border-color);
}

.feed-title {
  font-family: var(--font-display);
  font-size: 1.25rem;
  font-weight: 800;
  font-stretch: 88%;
  letter-spacing: -0.015em;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 12px;
}

.feed-title .material-symbols-outlined {
  color: var(--accent-cyber);
}

.stories-stream {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.story-card {
  background-color: transparent;
  border-bottom: var(--rule-thin) solid var(--border-color);
  padding: 28px 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.story-card:first-child {
  padding-top: 8px;
}

.story-header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.badge {
  font-family: var(--font-display);
  font-size: 0.8125rem;
  font-weight: 700;
  padding: 4px 8px;
  border: var(--rule-thin) solid transparent;
  letter-spacing: 0.01em;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.badge-critical {
  background-color: var(--badge-critical-bg);
  color: var(--badge-critical-text);
  border-color: var(--badge-critical-border);
}

.badge-warning {
  background-color: var(--badge-warning-bg);
  color: var(--badge-warning-text);
  border-color: var(--badge-warning-border);
}

.badge-patch {
  background-color: var(--badge-patch-bg);
  color: var(--badge-patch-text);
  border-color: var(--badge-patch-border);
}

.badge-info {
  background-color: var(--badge-info-bg);
  color: var(--badge-info-text);
  border-color: var(--badge-info-border);
}

.vendor-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.vendor-tag {
  background-color: var(--tag-bg);
  color: var(--tag-text);
  border: var(--rule-thin) solid var(--tag-border);
  font-family: var(--font-display);
  font-size: 0.75rem;
  font-weight: 600;
  padding: 4px 8px;
}

.story-title {
  font-family: var(--font-display);
  font-size: 1.375rem;
  font-weight: 700;
  line-height: 1.22;
  letter-spacing: -0.015em;
  color: var(--text-primary);
  text-wrap: balance;
}

.story-title a {
  color: inherit;
  text-decoration: none;
  transition: color var(--dur-short) var(--ease-out);
}

.story-title a:hover {
  color: var(--accent-cyber);
}

.story-body {
  font-family: var(--font-body);
  font-size: 1.0625rem;
  color: var(--text-secondary);
  line-height: 1.65;
  max-width: 65ch;
}

.story-footer {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 4px;
  padding-top: 8px;
  font-family: var(--font-display);
  font-variant-numeric: lining-nums tabular-nums;
  font-size: 0.8125rem;
  color: var(--text-muted);
}

.story-footer-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 16px;
}

.story-read-link {
  font-family: var(--font-display);
  color: var(--accent-cyber);
  font-weight: 700;
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  transition: color var(--dur-short) var(--ease-out);
}

.story-read-link:hover {
  color: var(--text-primary);
}

/* Sidebar Editorial Modules */
.sidebar-widget {
  background-color: transparent;
  border-top: var(--rule-heavy) solid var(--color-ink);
  border-bottom: var(--rule-thin) solid var(--border-color);
  padding: 20px 0;
}

.widget-title {
  font-family: var(--font-display);
  font-size: 1.0625rem;
  font-weight: 800;
  letter-spacing: -0.01em;
  color: var(--text-primary);
  margin-bottom: 16px;
  display: flex;
  align-items: center;
  gap: 8px;
  border-bottom: var(--rule-thin) solid var(--border-color);
  padding-bottom: 12px;
}

.widget-title .material-symbols-outlined {
  color: var(--accent-cyber);
}

.widget-empty-note {
  font-family: var(--font-body);
  font-size: 0.875rem;
  color: var(--text-muted);
}

/* Widget 1: Podcast Radio Box */
.podcast-radio-box {
  background-color: var(--bg-surface);
  border-top: var(--rule-heavy) solid var(--color-ink);
  border-bottom: var(--rule-thin) solid var(--border-color);
  padding: 20px;
  position: relative;
}

.podcast-badge-wrap {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 12px;
}

.podcast-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 0.8125rem;
  font-weight: 700;
  font-family: var(--font-display);
  background-color: var(--color-accent-tint);
  color: var(--accent-cyber);
  border: var(--rule-thin) solid var(--accent-cyber);
  padding: 4px 12px;
}

.cdn-stream-badge {
  font-family: var(--font-display);
  font-size: 1rem;
  letter-spacing: 0;
  color: var(--text-muted);
  font-weight: 600;
}

.podcast-controls {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
}

.podcast-play-btn {
  background: transparent;
  color: var(--text-primary);
  border: var(--rule-thin) solid var(--color-ink);
  width: 44px;
  height: 44px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: background-color var(--dur-short) var(--ease-out), border-color var(--dur-short) var(--ease-out), color var(--dur-short) var(--ease-out);
  flex-shrink: 0;
}

.podcast-play-btn:hover {
  background: var(--bg-primary);
  border-color: var(--accent-cyber);
  color: var(--accent-cyber);
}

.podcast-play-btn .btn-pause { display: none; }
.podcast-play-btn.playing .btn-play { display: none; }
.podcast-play-btn.playing .btn-pause { display: inline-flex; }

.podcast-scrubber-wrap {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.podcast-scrubber {
  width: 100%;
  accent-color: var(--accent-cyber);
  height: 4px;
  cursor: pointer;
}

.podcast-time {
  font-size: 0.75rem;
  color: var(--text-muted);
  font-family: var(--font-display);
  font-variant-numeric: lining-nums tabular-nums;
  display: flex;
  justify-content: space-between;
}

.speed-toggle-btn {
  background-color: var(--bg-primary);
  border: var(--rule-thin) solid var(--border-color);
  color: var(--text-secondary);
  font-size: 0.75rem;
  font-weight: 700;
  font-family: var(--font-display);
  font-variant-numeric: lining-nums tabular-nums;
  padding: 8px 12px;
  cursor: pointer;
  transition: border-color var(--dur-short) var(--ease-out), color var(--dur-short) var(--ease-out);
}

.speed-toggle-btn:hover {
  border-color: var(--color-ink);
  color: var(--text-primary);
}

/* Waveform Animation (compositor-friendly transform) */
.waveform-bars {
  display: inline-flex;
  align-items: flex-end;
  gap: 4px;
  height: 20px;
  margin-left: auto;
}

.waveform-bar {
  width: 4px;
  height: 16px;
  background-color: var(--accent-cyber);
  transform: scaleY(0.25);
  transform-origin: bottom;
  transition: transform var(--dur-short) var(--ease-out);
}

.is-playing .waveform-bar:nth-child(1) { animation: waveAnim 0.7s ease-in-out infinite 0.1s; }
.is-playing .waveform-bar:nth-child(2) { animation: waveAnim 0.7s ease-in-out infinite 0.3s; }
.is-playing .waveform-bar:nth-child(3) { animation: waveAnim 0.7s ease-in-out infinite 0.2s; }
.is-playing .waveform-bar:nth-child(4) { animation: waveAnim 0.7s ease-in-out infinite 0.4s; }
.is-playing .waveform-bar:nth-child(5) { animation: waveAnim 0.7s ease-in-out infinite 0.15s; }

@keyframes waveAnim {
  0%, 100% { transform: scaleY(0.25); }
  50% { transform: scaleY(1); }
}

/* Widget 2: Trending Stories */
.trending-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.trending-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding-bottom: 12px;
  border-bottom: var(--rule-thin) solid var(--border-color);
  text-decoration: none;
  color: var(--text-primary);
  transition: color var(--dur-short) var(--ease-out);
}

.trending-item:last-child {
  border-bottom: none;
  padding-bottom: 0;
}

.trending-num {
  font-size: 1.25rem;
  font-weight: 800;
  color: var(--accent-cyber);
  font-family: var(--font-display);
  font-variant-numeric: lining-nums tabular-nums;
  line-height: 1;
  min-width: 28px;
}

.trending-content h4 {
  font-family: var(--font-display);
  font-size: 0.9375rem;
  font-weight: 700;
  line-height: 1.3;
  margin-bottom: 4px;
  color: var(--text-primary);
  transition: color var(--dur-short) var(--ease-out);
}

.trending-item:hover .trending-content h4 {
  color: var(--accent-cyber);
}

.trending-content p {
  font-size: 0.75rem;
  color: var(--text-muted);
  font-family: var(--font-display);
  font-variant-numeric: lining-nums tabular-nums;
}

/* Widget 3: Threat Radar / CVE Tracker */
.threat-radar-box {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.radar-sublabel {
  font-family: var(--font-display);
  font-size: 0.8125rem;
  font-weight: 700;
  color: var(--text-muted);
  margin-bottom: 8px;
}

.cve-pill-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.cve-pill {
  font-family: var(--font-display);
  font-variant-numeric: lining-nums tabular-nums;
  font-size: 0.75rem;
  font-weight: 700;
  background-color: var(--badge-critical-bg);
  color: var(--badge-critical-text);
  border: var(--rule-thin) solid var(--badge-critical-border);
  padding: 4px 8px;
}

.tech-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}

/* Widget 4: Recent Editions */
.archive-mini-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.archive-mini-item {
  font-size: 0.875rem;
  color: var(--text-secondary);
  text-decoration: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 0;
  border-bottom: var(--rule-thin) solid var(--border-color);
  transition: color var(--dur-short) var(--ease-out);
  font-family: var(--font-display);
  font-variant-numeric: lining-nums tabular-nums;
}

.archive-mini-item:last-child {
  border-bottom: none;
}

.archive-mini-item:hover {
  color: var(--accent-cyber);
}

.archive-mini-item code {
  font-family: var(--font-display);
  font-variant-numeric: lining-nums tabular-nums;
  font-size: 0.75rem;
  color: var(--text-muted);
  background: var(--bg-surface);
  padding: 4px 8px;
  border: var(--rule-thin) solid var(--border-color);
}

/* Dedicated Single Article Page Styles */
.reading-progress-bar {
  position: fixed;
  top: 0;
  left: 0;
  height: 4px;
  background: var(--accent-cyber);
  z-index: 9999;
  width: 0%;
}

.breadcrumb {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-family: var(--font-display);
  font-size: 0.875rem;
  color: var(--text-muted);
  margin-bottom: 16px;
}

.breadcrumb a {
  color: var(--text-secondary);
  text-decoration: none;
  transition: color var(--dur-short) var(--ease-out);
}

.breadcrumb a:hover {
  color: var(--accent-cyber);
}

.article-h1-title {
  font-family: var(--font-display);
  font-size: clamp(2rem, 1.45rem + 2vw, 3rem);
  font-weight: 800;
  font-stretch: 85%;
  line-height: 1.08;
  letter-spacing: -0.025em;
  color: var(--text-primary);
  margin-bottom: 20px;
  text-wrap: balance;
}

.article-meta-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 0;
  border-top: var(--rule-heavy) solid var(--color-ink);
  border-bottom: var(--rule-thin) solid var(--border-color);
  margin-bottom: 28px;
  font-family: var(--font-display);
  font-variant-numeric: lining-nums tabular-nums;
  font-size: 0.875rem;
  color: var(--text-muted);
}

.article-meta-left {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 16px;
}

.article-share-group {
  display: flex;
  align-items: center;
  gap: 8px;
}

.share-btn {
  background: var(--bg-surface);
  border: var(--rule-thin) solid var(--border-color);
  color: var(--text-secondary);
  width: 36px;
  height: 36px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: border-color var(--dur-short) var(--ease-out), color var(--dur-short) var(--ease-out), background-color var(--dur-short) var(--ease-out);
}

.share-btn:hover {
  border-color: var(--color-ink);
  color: var(--color-paper);
  background-color: var(--color-ink);
}

/* Cyber Security Editorial Dispatch Plate */
.cyber-banner {
  background-color: var(--color-banner-bg);
  color: var(--color-banner-ink);
  border-top: var(--rule-heavy) solid var(--accent-cyber);
  border-bottom: var(--rule-thin) solid var(--border-color);
  padding: 28px;
  margin-bottom: 28px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  position: relative;
}

.cyber-banner-badge {
  align-self: flex-start;
  font-size: 1rem;
  font-weight: 700;
  font-family: var(--font-display);
  color: var(--color-banner-accent);
  border-bottom: var(--rule-thin) solid var(--color-banner-accent);
  padding-bottom: 4px;
  letter-spacing: 0;
}

.cyber-banner-title {
  font-family: var(--font-display);
  font-size: 1.375rem;
  font-weight: 700;
  line-height: 1.25;
  color: var(--color-banner-ink);
  max-width: 65ch;
}

/* Article Content Body */
.article-lead {
  font-family: var(--font-body);
  font-size: 1.25rem;
  font-weight: 500;
  line-height: 1.6;
  color: var(--text-primary);
  margin-bottom: 24px;
  border-left: var(--rule-heavy) solid var(--color-ink);
  padding-left: 20px;
  max-width: 65ch;
}

.article-body-text {
  font-family: var(--font-body);
  font-size: 1.125rem;
  line-height: 1.72;
  color: var(--text-primary);
  margin-bottom: 32px;
  max-width: 65ch;
}

.article-body-text p {
  margin-bottom: 20px;
}

/* Threat Box & Defense Mitigation Box */
.threat-summary-box {
  background-color: var(--bg-surface);
  border: var(--rule-thin) solid var(--border-color);
  border-top: var(--rule-heavy) solid var(--badge-critical-text);
  padding: 24px;
  margin-bottom: 28px;
}

.threat-summary-box h3 {
  font-family: var(--font-display);
  font-size: 1.125rem;
  font-weight: 800;
  color: var(--text-primary);
  margin-bottom: 16px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.threat-specs-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(0, 1fr));
  gap: 16px;
  font-size: 0.875rem;
}

.spec-item strong {
  display: block;
  font-family: var(--font-display);
  font-size: 0.8125rem;
  color: var(--text-muted);
  margin-bottom: 4px;
}

.spec-item span {
  font-family: var(--font-display);
  font-variant-numeric: lining-nums tabular-nums;
  font-weight: 600;
  color: var(--text-primary);
}

.mitigation-box {
  background-color: var(--bg-surface);
  border: var(--rule-thin) solid var(--badge-patch-border);
  border-top: var(--rule-heavy) solid var(--badge-patch-text);
  padding: 24px;
  margin-bottom: 28px;
}

.mitigation-box h3 {
  font-family: var(--font-display);
  font-size: 1.125rem;
  font-weight: 800;
  color: var(--badge-patch-text);
  margin-bottom: 16px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.mitigation-list {
  padding-left: 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  font-family: var(--font-body);
  font-size: 1rem;
  color: var(--text-secondary);
  line-height: 1.65;
  max-width: 65ch;
}

/* Podcast Snippet Box */
.podcast-snippet-box {
  background-color: var(--bg-surface);
  border-top: var(--rule-thin) solid var(--border-color);
  border-bottom: var(--rule-thin) solid var(--border-color);
  border-left: var(--rule-heavy) solid var(--accent-cyber);
  padding: 20px;
  margin-bottom: 28px;
}

.podcast-snippet-header {
  font-family: var(--font-display);
  font-size: 1rem;
  font-weight: 700;
  color: var(--text-primary);
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.podcast-snippet-header .material-symbols-outlined {
  color: var(--accent-cyber);
}

.podcast-line {
  font-family: var(--font-body);
  font-size: 1rem;
  color: var(--text-secondary);
  font-style: italic;
  line-height: 1.65;
  padding: 8px 0;
  max-width: 65ch;
}

/* Source Attribution Box */
.source-box {
  background-color: var(--bg-surface);
  border-top: var(--rule-thin) solid var(--border-color);
  border-bottom: var(--rule-thin) solid var(--border-color);
  padding: 20px 24px;
  margin-bottom: 28px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.source-box-title {
  font-family: var(--font-display);
  font-size: 0.8125rem;
  color: var(--text-muted);
}

.source-box-name {
  font-family: var(--font-display);
  font-size: 1rem;
  font-weight: 700;
  color: var(--text-primary);
  margin-top: 4px;
}

.source-box-actions {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

.source-external-link {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--accent-cyber);
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 0.875rem;
  text-decoration: none;
  padding: 8px 12px;
  border: var(--rule-thin) solid var(--border-color);
  background-color: var(--bg-primary);
  transition: border-color var(--dur-short) var(--ease-out), color var(--dur-short) var(--ease-out);
}

.source-external-link:hover {
  border-color: var(--color-ink);
  color: var(--text-primary);
}

.source-box .mirror-link {
  border-color: var(--accent-cyber);
  color: var(--accent-cyber);
}

.source-box .mirror-link:hover {
  background-color: var(--color-accent-tint);
  border-color: var(--accent-cyber);
}

/* Toast Notification */
.toast-notification {
  position: fixed;
  bottom: 24px;
  right: 24px;
  background-color: var(--color-ink);
  color: var(--color-paper);
  border: var(--rule-thin) solid var(--color-ink);
  padding: 12px 20px;
  opacity: 0;
  transform: translateY(12px);
  transition: opacity var(--dur-med) var(--ease-out), transform var(--dur-med) var(--ease-out);
  pointer-events: none;
  z-index: 10000;
  font-family: var(--font-display);
  font-size: 0.875rem;
  font-weight: 600;
}

.toast-notification.show {
  opacity: 1;
  transform: translateY(0);
}

/* Site Footer */
.site-footer {
  background-color: var(--bg-surface);
  border-top: var(--rule-heavy) solid var(--color-ink);
  padding: 36px 0;
  margin-top: auto;
  color: var(--text-muted);
  font-family: var(--font-display);
  font-size: 0.875rem;
}

.footer-inner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.footer-links {
  display: flex;
  flex-wrap: wrap;
  gap: 20px;
}

.footer-link {
  color: var(--text-secondary);
  text-decoration: none;
  font-weight: 600;
  transition: color var(--dur-short) var(--ease-out);
}

.footer-link:hover {
  color: var(--accent-cyber);
}

/* Archive List Layout */
.archive-section {
  margin-top: 24px;
  margin-bottom: 64px;
}

.archive-grid {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.archive-card {
  background-color: transparent;
  border-bottom: var(--rule-thin) solid var(--border-color);
  padding: 24px 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  text-decoration: none;
  color: var(--text-primary);
  transition: color var(--dur-short) var(--ease-out);
}

.archive-card:hover .archive-info h3 {
  color: var(--accent-cyber);
}

.archive-info h3 {
  font-family: var(--font-display);
  font-size: 1.25rem;
  font-weight: 700;
  margin-bottom: 8px;
  color: var(--text-primary);
  letter-spacing: -0.015em;
  transition: color var(--dur-short) var(--ease-out);
}

.archive-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  font-family: var(--font-display);
  font-variant-numeric: lining-nums tabular-nums;
  font-size: 0.8125rem;
  color: var(--text-muted);
}

.archive-meta code {
  font-family: var(--font-display);
  font-variant-numeric: lining-nums tabular-nums;
  background: var(--bg-surface);
  padding: 4px 8px;
  border: var(--rule-thin) solid var(--border-color);
}

/* Article Reading Utility Bar */
.article-utility-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 0;
  border-top: var(--rule-thin) solid var(--border-color);
}

.back-edition-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--text-secondary);
  font-family: var(--font-display);
  font-size: 0.875rem;
  font-weight: 700;
  text-decoration: none;
  transition: color var(--dur-short) var(--ease-out);
}

.back-edition-btn:hover {
  color: var(--accent-cyber);
}

.utility-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 16px;
}

.font-resizer {
  display: inline-flex;
  align-items: center;
  border: var(--rule-thin) solid var(--border-color);
  background-color: var(--bg-surface);
}

.font-resizer button {
  background: none;
  border: none;
  color: var(--text-primary);
  font-size: 0.8125rem;
  font-weight: 700;
  font-family: var(--font-display);
  padding: 8px 12px;
  cursor: pointer;
  transition: background-color var(--dur-short) var(--ease-out), color var(--dur-short) var(--ease-out);
}

.font-resizer button:first-child {
  border-right: var(--rule-thin) solid var(--border-color);
}

.font-resizer button:hover {
  background-color: var(--color-ink);
  color: var(--color-paper);
}

.views-counter {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-family: var(--font-display);
  font-variant-numeric: lining-nums tabular-nums;
  font-size: 0.8125rem;
  color: var(--text-muted);
}

/* Article Pagination (Previous / Next) */
.article-pagination {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(0, 1fr));
  gap: 16px;
  margin: 36px 0 20px 0;
  padding-top: 24px;
  border-top: var(--rule-heavy) solid var(--color-ink);
}

.pagination-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 20px;
  background-color: var(--bg-surface);
  border: var(--rule-thin) solid var(--border-color);
  text-decoration: none;
  color: var(--text-primary);
  transition: border-color var(--dur-short) var(--ease-out), background-color var(--dur-short) var(--ease-out);
}

.pagination-card:hover {
  border-color: var(--color-ink);
  background-color: var(--color-paper-3);
}

.pagination-card-next {
  text-align: right;
  margin-left: auto;
  width: 100%;
}

.pagination-sublabel {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 0.8125rem;
  font-weight: 700;
  color: var(--text-muted);
  font-family: var(--font-display);
}

.pagination-card-next .pagination-sublabel {
  justify-content: flex-end;
}

.pagination-title {
  font-family: var(--font-display);
  font-size: 1rem;
  font-weight: 700;
  line-height: 1.35;
  color: var(--text-primary);
}

.pagination-placeholder {
  min-height: 4px;
}

/* Executive TL;DR Box */
.tldr-box {
  background-color: var(--bg-surface);
  border: var(--rule-thin) solid var(--border-color);
  border-top: var(--rule-heavy) solid var(--color-ink);
  padding: 20px 24px;
  margin: 24px 0 28px 0;
}

.tldr-header {
  font-family: var(--font-display);
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 1.0625rem;
  color: var(--text-primary);
  margin-bottom: 12px;
}

.tldr-header .material-symbols-outlined {
  color: var(--accent-cyber);
}

.tldr-list {
  margin: 0;
  padding-left: 20px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-family: var(--font-body);
  font-size: 1rem;
  line-height: 1.65;
  color: var(--text-primary);
  max-width: 65ch;
}

.tldr-list li::marker {
  color: var(--accent-cyber);
}

/* GitHub Header Button */
.github-header-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: var(--bg-surface);
  border: var(--rule-thin) solid var(--border-color);
  color: var(--text-primary);
  height: 40px;
  padding: 0 16px;
  font-family: var(--font-display);
  font-size: 0.8125rem;
  font-weight: 700;
  text-decoration: none;
  transition: border-color var(--dur-short) var(--ease-out), background-color var(--dur-short) var(--ease-out), color var(--dur-short) var(--ease-out);
  white-space: nowrap;
}

.github-header-btn:hover {
  border-color: var(--color-ink);
  background-color: var(--color-ink);
  color: var(--color-paper);
}

.github-header-btn .icon-github-ext {
  font-size: 16px !important;
  width: 16px !important;
  height: 16px !important;
  opacity: 0.75;
}

/* GitHub Utility Button in Article Utility Bar */
.github-util-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: var(--bg-surface);
  border: var(--rule-thin) solid var(--border-color);
  color: var(--text-secondary);
  padding: 4px 12px;
  font-family: var(--font-display);
  font-size: 0.8125rem;
  font-weight: 700;
  text-decoration: none;
  transition: border-color var(--dur-short) var(--ease-out), color var(--dur-short) var(--ease-out), background-color var(--dur-short) var(--ease-out);
}

.github-util-btn:hover {
  border-color: var(--color-ink);
  color: var(--text-primary);
  background: var(--bg-primary);
}

.github-util-btn .icon-util-ext {
  font-size: 14px !important;
  width: 14px !important;
  height: 14px !important;
  opacity: 0.75;
}

/* GitHub Intel Box in Article Page */
.github-intel-box {
  background-color: var(--bg-surface);
  border: var(--rule-thin) solid var(--border-color);
  border-left: var(--rule-heavy) solid var(--color-ink);
  padding: 20px;
  margin: 24px 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.github-intel-info {
  display: flex;
  align-items: center;
  gap: 16px;
}

.github-intel-info .github-svg-icon {
  width: 28px;
  height: 28px;
  color: var(--text-primary);
  flex-shrink: 0;
}

.github-intel-title {
  font-family: var(--font-display);
  font-size: 0.9375rem;
  font-weight: 700;
  color: var(--text-primary);
  margin-bottom: 4px;
}

.github-intel-desc {
  font-family: var(--font-body);
  font-size: 0.875rem;
  color: var(--text-muted);
  line-height: 1.5;
}

.btn-github-action {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: transparent;
  border: var(--rule-thin) solid var(--color-ink);
  color: var(--text-primary);
  padding: 8px 16px;
  font-family: var(--font-display);
  font-size: 0.8125rem;
  font-weight: 700;
  text-decoration: none;
  white-space: nowrap;
  transition: background-color var(--dur-short) var(--ease-out), border-color var(--dur-short) var(--ease-out), color var(--dur-short) var(--ease-out);
  flex-shrink: 0;
}

.btn-github-action:hover {
  background: var(--bg-primary);
  border-color: var(--accent-cyber);
  color: var(--accent-cyber);
}

/* Video Brief Widget & Streaming Player */
.video-brief-box {
  background-color: var(--bg-surface);
  border-top: var(--rule-heavy) solid var(--color-ink);
  border-bottom: var(--rule-thin) solid var(--border-color);
  padding: 20px;
  position: relative;
}

.video-badge-live {
  font-family: var(--font-display);
  font-size: 1rem;
  letter-spacing: 0;
  color: var(--accent-cyber);
  font-weight: 700;
  margin-left: auto;
}

.video-container {
  width: 100%;
  background: var(--color-video-bg);
  overflow: hidden;
  display: flex;
  justify-content: center;
  align-items: center;
  margin-top: 12px;
  border: var(--rule-thin) solid var(--border-color);
}

.edition-video-player {
  width: 100%;
  max-height: 480px;
  aspect-ratio: 9/16;
  object-fit: contain;
  background: var(--color-video-bg);
  outline: none;
}

.video-caption {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-family: var(--font-body);
  font-size: 0.875rem;
  color: var(--text-muted);
}

.video-source-badge {
  font-family: var(--font-display);
  font-size: 1rem;
  letter-spacing: 0;
  color: var(--accent-cyber);
  font-weight: 700;
}

.edition-top-bar {
  margin-bottom: 20px;
  display: flex;
  gap: 12px;
}

.edition-top-link {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.empty-state-box {
  margin-top: 40px;
  text-align: center;
  padding: 40px;
}

.empty-state-desc {
  color: var(--text-muted);
  margin-top: 12px;
}

/* Responsive Breakpoints */
@media (max-width: 960px) {
  .portal-grid {
    grid-template-columns: minmax(0, 1fr);
    gap: 32px;
  }
  .portal-sidebar {
    position: static;
    border-left: none;
    padding-left: 0;
  }
}

@media (max-width: 680px) {
  .header-top {
    flex-direction: column;
    align-items: stretch;
    gap: 16px;
  }
  .header-actions {
    width: 100%;
    justify-content: space-between;
  }
  .search-box {
    width: 100%;
  }
  .hero-title {
    font-size: 1.625rem;
  }
  .article-h1-title {
    font-size: 1.75rem;
  }
  .footer-inner {
    flex-direction: column;
    align-items: flex-start;
  }
  .filter-taskbar {
    flex-direction: column;
    align-items: flex-start;
  }
  .article-utility-bar {
    flex-direction: column;
    align-items: flex-start;
  }
  .article-pagination {
    grid-template-columns: minmax(0, 1fr);
  }
  .pagination-card-next {
    text-align: left;
  }
  .pagination-card-next .pagination-sublabel {
    justify-content: flex-start;
  }
  .github-intel-box {
    flex-direction: column;
    align-items: flex-start;
  }
}

@media (prefers-reduced-motion: reduce) {
  html {
    scroll-behavior: auto;
  }
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
"""


def _get_app_js() -> str:
    """Return vanilla JS with theme toggle, sticky podcast player, reading progress, and search filter."""
    return r"""/* CyberPulse Reader Static Portal Script - Hallmark Editorial Edition */
(function() {
  // Theme initialization before DOM paint (defaults to Editorial light paper)
  var saved = localStorage.getItem('cyberpulse-theme') || 'light';
  document.documentElement.setAttribute('data-theme', saved);

  // Check Google Material Symbols font loading
  if ('fonts' in document) {
    document.fonts.load('24px "Material Symbols Outlined"').catch(function() {
      document.documentElement.classList.add('fonts-failed');
    });
  }
})();

document.addEventListener('DOMContentLoaded', function() {
  // 1. Theme toggle listener
  var themeBtn = document.getElementById('theme-toggle');
  if (themeBtn) {
    themeBtn.addEventListener('click', function() {
      var current = document.documentElement.getAttribute('data-theme') || 'light';
      var next = current === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('cyberpulse-theme', next);
    });
  }

  // 2. Reading progress bar on article pages
  var progressBar = document.getElementById('reading-progress');
  if (progressBar) {
    window.addEventListener('scroll', function() {
      var winScroll = document.documentElement.scrollTop || document.body.scrollTop;
      var height = document.documentElement.scrollHeight - document.documentElement.clientHeight;
      var scrolled = height > 0 ? (winScroll / height) * 100 : 0;
      progressBar.style.width = scrolled + '%';
    });
  }

  // 3. Interactive Audio Player with Speed Toggle & Waveform
  var audio = document.getElementById('podcast-audio');
  var playBtn = document.getElementById('podcast-play-btn');
  var scrubber = document.getElementById('podcast-scrubber');
  var curTime = document.getElementById('podcast-time-cur');
  var durTime = document.getElementById('podcast-time-dur');
  var speedBtn = document.getElementById('podcast-speed-btn');
  var radioBox = document.getElementById('podcast-radio-box');

  if (audio && playBtn) {
    function formatTime(sec) {
      if (isNaN(sec) || sec < 0) return '00:00';
      var m = Math.floor(sec / 60);
      var s = Math.floor(sec % 60);
      return (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
    }

    playBtn.addEventListener('click', function() {
      if (audio.paused) {
        audio.play().catch(function() {});
      } else {
        audio.pause();
      }
    });

    audio.addEventListener('play', function() {
      playBtn.classList.add('playing');
      if (radioBox) radioBox.classList.add('is-playing');
      playBtn.setAttribute('aria-label', 'Tạm dừng');
    });

    audio.addEventListener('pause', function() {
      playBtn.classList.remove('playing');
      if (radioBox) radioBox.classList.remove('is-playing');
      playBtn.setAttribute('aria-label', 'Phát');
    });

    audio.addEventListener('loadedmetadata', function() {
      if (durTime) durTime.textContent = formatTime(audio.duration);
      if (scrubber) scrubber.max = Math.floor(audio.duration || 100);
    });

    audio.addEventListener('timeupdate', function() {
      if (curTime) curTime.textContent = formatTime(audio.currentTime);
      if (scrubber && !scrubber.dataset.seeking) {
        scrubber.value = Math.floor(audio.currentTime);
      }
    });

    audio.addEventListener('ended', function() {
      playBtn.classList.remove('playing');
      if (radioBox) radioBox.classList.remove('is-playing');
      if (scrubber) scrubber.value = 0;
      if (curTime) curTime.textContent = '00:00';
    });

    if (scrubber) {
      scrubber.addEventListener('input', function() {
        scrubber.dataset.seeking = 'true';
        if (curTime) curTime.textContent = formatTime(scrubber.value);
      });
      scrubber.addEventListener('change', function() {
        audio.currentTime = scrubber.value;
        delete scrubber.dataset.seeking;
      });
    }

    if (speedBtn) {
      var speeds = [1.0, 1.25, 1.5];
      var speedIdx = 0;
      speedBtn.addEventListener('click', function() {
        speedIdx = (speedIdx + 1) % speeds.length;
        var newSpeed = speeds[speedIdx];
        audio.playbackRate = newSpeed;
        speedBtn.textContent = newSpeed.toFixed(2).replace(/\.00$/, '') + 'x';
      });
    }
  }

  // 4. Combined Filter and Search Function
  function applyCardFilters() {
    var activePill = document.querySelector('.filter-pill.active');
    var filter = activePill ? activePill.getAttribute('data-filter') : 'all';
    var searchInput = document.getElementById('search-input');
    var query = searchInput ? searchInput.value.toLowerCase().trim() : '';

    var cards = document.querySelectorAll('.threat-card, .story-card, .hero-card, .filterable-card');
    cards.forEach(function(card) {
      var sev = card.getAttribute('data-severity') || '';
      var matchFilter = false;
      if (filter === 'all') {
        matchFilter = true;
      } else if (filter === 'critical') {
        matchFilter = (sev === 'critical') || !!card.querySelector('.badge-critical');
      } else if (filter === 'warning') {
        matchFilter = (sev === 'warning' || sev === 'high') || !!card.querySelector('.badge-warning, .badge-high');
      } else if (filter === 'patch') {
        matchFilter = (sev === 'patch') || !!card.querySelector('.badge-patch');
      }

      var text = card.textContent.toLowerCase();
      var matchSearch = !query || text.indexOf(query) !== -1;

      if (matchFilter && matchSearch) {
        card.style.display = '';
      } else {
        card.style.display = 'none';
      }
    });
  }

  // 5. Search Filter
  var searchInput = document.getElementById('search-input');
  if (searchInput) {
    searchInput.addEventListener('input', function() {
      applyCardFilters();
    });
  }

  // Quick keyboard focus on search with '/'
  window.addEventListener('keydown', function(e) {
    if (e.key === '/' && document.activeElement !== searchInput) {
      if (searchInput) {
        e.preventDefault();
        searchInput.focus();
      }
    }
  });

  // 6. Live Interactive Filter Pills
  var filterButtons = document.querySelectorAll('.filter-pill');
  if (filterButtons.length > 0) {
    filterButtons.forEach(function(btn) {
      btn.addEventListener('click', function() {
        filterButtons.forEach(function(b) { b.classList.remove('active'); });
        this.classList.add('active');
        applyCardFilters();
      });
    });
  }

  // 7. View Counters (Article & Edition)
  function getBaselineViews(str) {
    var hash = 0;
    for (var i = 0; i < str.length; i++) {
      hash = ((hash << 5) - hash) + str.charCodeAt(i);
      hash |= 0;
    }
    return 800 + (Math.abs(hash) % 1701);
  }

  var articleElem = document.querySelector('[data-article-id]') || document.querySelector('.single-article-main');
  var articleId = articleElem ? articleElem.getAttribute('data-article-id') : null;
  if (!articleId) {
    var match = window.location.pathname.match(/\/articles\/([^\/.]+)/);
    if (match) articleId = match[1];
  }
  if (articleId) {
    var key = 'cp_views_' + articleId;
    var count = parseInt(localStorage.getItem(key), 10);
    if (isNaN(count)) {
      count = getBaselineViews(articleId) + 1;
    } else {
      count += 1;
    }
    localStorage.setItem(key, count);
    var formatted = count.toLocaleString('vi-VN') || count.toLocaleString();
    var viewEls = document.querySelectorAll('#article-views-count, #article-views');
    viewEls.forEach(function(el) {
      el.textContent = formatted;
    });
  }

  var heroElem = document.querySelector('[data-run-id]');
  var runId = heroElem ? heroElem.getAttribute('data-run-id') : null;
  if (runId) {
    var runKey = 'cp_views_edition_' + runId;
    var edCount = parseInt(localStorage.getItem(runKey), 10);
    if (isNaN(edCount)) {
      edCount = (getBaselineViews(runId) * 4) + 1;
    } else {
      edCount += 1;
    }
    localStorage.setItem(runKey, edCount);
    var edFormatted = edCount.toLocaleString('vi-VN') || edCount.toLocaleString();
    var edViewEl = document.getElementById('edition-views-count');
    if (edViewEl) {
      edViewEl.textContent = edFormatted;
    }
  }

  // 8. Font Resizer
  var fontDec = document.getElementById('font-decrease');
  var fontInc = document.getElementById('font-increase');
  var bodyText = document.querySelector('.article-body-text');
  if (bodyText && (fontDec || fontInc)) {
    var savedSize = parseInt(localStorage.getItem('cp_font_size'), 10);
    var curSize = (!isNaN(savedSize) && savedSize >= 15 && savedSize <= 22) ? savedSize : 18;
    bodyText.style.fontSize = curSize + 'px';

    if (fontDec) {
      fontDec.addEventListener('click', function() {
        if (curSize > 15) {
          curSize -= 1;
          bodyText.style.fontSize = curSize + 'px';
          localStorage.setItem('cp_font_size', curSize);
        }
      });
    }

    if (fontInc) {
      fontInc.addEventListener('click', function() {
        if (curSize < 22) {
          curSize += 1;
          bodyText.style.fontSize = curSize + 'px';
          localStorage.setItem('cp_font_size', curSize);
        }
      });
    }
  }

  // 9. Podcast Quick Button Smooth Scroll
  var podcastQuickBtn = document.querySelector('.podcast-quick-btn');
  if (podcastQuickBtn) {
    podcastQuickBtn.addEventListener('click', function(e) {
      e.preventDefault();
      var target = document.getElementById('podcast-radio-box');
      if (target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'center' });
        var playBtn = document.getElementById('podcast-play-btn');
        if (playBtn) playBtn.focus();
      }
    });
  }
});

// 5. Toast Notification & Copy Link
function copyCurrentUrl() {
  var url = window.location.href;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(url).then(showToast).catch(function() {
      fallbackCopy(url);
    });
  } else {
    fallbackCopy(url);
  }
}

function fallbackCopy(text) {
  var ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.left = '-9999px';
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  try {
    document.execCommand('copy');
    showToast();
  } catch (err) {}
  document.body.removeChild(ta);
}

function showToast() {
  var toast = document.getElementById('toast-notify');
  if (!toast) return;
  toast.classList.add('show');
  setTimeout(function() {
    toast.classList.remove('show');
  }, 2500);
}
"""


def _render_header(
    active_nav: str = "",
    base_prefix: str = "",
    sub_bar_html: str = "",
) -> str:
    """Render top semantic header with brand, utility/filter bar, search, and theme toggle.

    Eliminates broken static category anchor links (#vulnerabilities, #malware, #patches).
    ABSOLUTELY NO JSON FEED LINKS/BUTTONS.
    """
    home_link = f"{base_prefix}index.html"
    shield_icon = render_material_symbol("shield")
    search_icon = render_material_symbol("search")
    dark_icon = render_material_symbol("dark_mode", extra_class="icon-dark-mode")
    light_icon = render_material_symbol("light_mode", extra_class="icon-light-mode")

    if not sub_bar_html:
        archive_link = f"{base_prefix}archive.html"
        home_active = " active" if active_nav == "home" else ""
        archive_active = " active" if active_nav == "archive" else ""
        sub_bar_html = f"""<nav class="nav-bar">
      <a href="{home_link}" class="nav-link{home_active}">Trang chủ</a>
      <a href="{archive_link}" class="nav-link{archive_active}">Bản tin theo ngày</a>
    </nav>"""

    return f"""<header class="site-header">
  <div class="container">
    <div class="header-top">
      <a href="{home_link}" class="brand">
        <div class="brand-icon">{shield_icon}</div>
        <div class="brand-text">
          <h1>CyberPulse AI</h1>
          <p><span class="brand-live-dot" role="status"></span> Chuyên san Phân tích &amp; Tình báo An ninh Mạng</p>
        </div>
      </a>
      <div class="header-actions">
        <div class="search-box">
          <span class="search-icon-pos">{search_icon}</span>
          <input type="text" id="search-input" class="search-input" placeholder="Tìm kiếm tin bảo mật, CVE…" aria-label="Tìm kiếm">
        </div>
        <a href="{GITHUB_REPO_URL}" target="_blank" rel="noopener noreferrer" class="github-header-btn" title="Xem chi tiết &amp; mã nguồn trên GitHub">
          {GITHUB_SVG_ICON}
          <span class="github-btn-text">GitHub</span>
          {render_material_symbol("open_in_new", extra_class="icon-github-ext")}
        </a>
        <button id="theme-toggle" class="theme-toggle-btn" aria-label="Chuyển chế độ sáng/tối" title="Chuyển chế độ giao diện">
          {dark_icon}
          {light_icon}
        </button>
      </div>
    </div>
    {sub_bar_html}
  </div>
</header>"""


def _render_footer(base_prefix: str = "") -> str:
    """Render semantic footer with copyright, GitHub and CDN links (ABSOLUTELY NO JSON FEED)."""
    home_link = f"{base_prefix}index.html"
    archive_link = f"{base_prefix}archive.html"

    return f"""<footer class="site-footer">
  <div class="container footer-inner">
    <div>
      <p>&copy; 2026 CyberPulse AI. Tạp chí An ninh Mạng Tổng hợp Tự động bởi CyberPulse Agentic AI.</p>
    </div>
    <div class="footer-links">
      <a href="{home_link}" class="footer-link">Trang chủ</a>
      <a href="{archive_link}" class="footer-link">Kho lưu trữ</a>
      <a href="{GITHUB_REPO_URL}" target="_blank" rel="noopener noreferrer" class="footer-link">Mã nguồn GitHub</a>
      <a href="{GITHUB_PAGES_CDN_URL}/" target="_blank" rel="noopener noreferrer" class="footer-link">GitHub Pages CDN</a>
    </div>
  </div>
</footer>"""


def _render_sidebar(
    featured_pkg: EditorialPackage | None,
    has_audio: bool,
    audio_src: str | None,
    editions_data: list[dict[str, Any]],
    trending_stories: list[dict[str, Any]],
    active_cves: list[str],
    vulnerable_tech: list[str],
    base_prefix: str = "",
    has_video: bool = False,
    video_src: str | None = None,
    video_cover_src: str | None = None,
) -> str:
    """Render sticky sidebar with widgets (Podcast, Video Intelligence, Trending Stories, Threat Radar, Recent Editions)."""
    # 1. Widget 1: Podcast Radio Box
    podcasts_icon = render_material_symbol("podcasts")
    play_icon = render_material_symbol("play_circle", extra_class="btn-play")
    pause_icon = render_material_symbol("pause_circle", extra_class="btn-pause")

    if has_audio and audio_src:
        podcast_widget = f"""<div id="podcast-radio-box" class="podcast-radio-box sidebar-widget">
  <div class="widget-title">
    {podcasts_icon}
    <span>Podcast Radio Box</span>
    <div class="waveform-bars">
      <span class="waveform-bar"></span>
      <span class="waveform-bar"></span>
      <span class="waveform-bar"></span>
      <span class="waveform-bar"></span>
      <span class="waveform-bar"></span>
    </div>
  </div>
  <div class="podcast-badge-wrap">
    <div class="podcast-badge">Bản tin AI Voiceover</div>
    <span class="cdn-stream-badge">[CDN GITHUB PAGES]</span>
  </div>
  <audio id="podcast-audio" preload="metadata" src="{audio_src}"></audio>
  <div class="podcast-controls">
    <button id="podcast-play-btn" class="podcast-play-btn" aria-label="Phát bản tin">
      {play_icon}
      {pause_icon}
    </button>
    <div class="podcast-scrubber-wrap">
      <input type="range" id="podcast-scrubber" class="podcast-scrubber" min="0" value="0" step="1">
      <div class="podcast-time">
        <span id="podcast-time-cur">00:00</span>
        <span id="podcast-time-dur">--:--</span>
      </div>
    </div>
    <button id="podcast-speed-btn" class="speed-toggle-btn" title="Tốc độ phát">1.0x</button>
  </div>
</div>"""
    else:
        podcast_widget = f"""<div class="podcast-radio-box sidebar-widget">
  <div class="widget-title">
    {podcasts_icon}
    <span>Podcast Radio Box</span>
  </div>
  <p class="widget-empty-note">Ấn bản hiện tại ở định dạng văn bản đọc.</p>
</div>"""

    # Video Widget: CyberPulse Video Intelligence (9:16 Short-form Video Player)
    video_widget = ""
    if has_video and video_src:
        video_icon = render_material_symbol("smart_display")
        poster_attr = f' poster="{html.escape(video_cover_src)}"' if video_cover_src else ""
        video_widget = f"""<div id="video-brief-box" class="video-brief-box sidebar-widget">
  <div class="widget-title">
    {video_icon}
    <span>Video Brief 9:16</span>
    <span class="video-badge-live">HD STREAM</span>
  </div>
  <div class="video-container">
    <video id="edition-video" controls preload="metadata" playsinline src="{html.escape(video_src)}"{poster_attr} class="edition-video-player">
      Trình duyệt không hỗ trợ thẻ video HTML5.
    </video>
  </div>
  <div class="video-caption">
    <span class="video-source-badge">[CDN: GITHUB PAGES]</span>
    <p>Bản tin tóm tắt an ninh mạng định dạng video ngắn 9:16.</p>
  </div>
</div>"""

    # 2. Widget 2: Trending Stories / Tin đọc nhiều (Top 5 numbered 01-05)
    trending_icon = render_material_symbol("trending_up")
    trending_items_html: list[str] = []
    for idx, item in enumerate(trending_stories[:5], start=1):
        num_str = f"{idx:02d}"
        title = html.escape(item.get("title", ""))
        link = item.get("link", "#")
        time_str = html.escape(item.get("time_str", ""))
        trending_items_html.append(f"""<a href="{link}" class="trending-item">
  <div class="trending-num">{num_str}</div>
  <div class="trending-content">
    <h4>{title}</h4>
    <p>{time_str}</p>
  </div>
</a>""")
    trending_widget = f"""<div class="sidebar-widget">
  <div class="widget-title">
    {trending_icon}
    <span>Tin đọc nhiều / Trending</span>
  </div>
  <div class="trending-list">
    {"".join(trending_items_html)}
  </div>
</div>"""

    # 3. Widget 3: Threat Radar / CVE Tracker
    radar_icon = render_material_symbol("bug_report")
    cve_pills = "".join(f'<span class="cve-pill">{html.escape(c)}</span>' for c in active_cves[:8])
    if not cve_pills:
        cve_pills = '<span class="widget-empty-note">Đang theo dõi chuỗi khai thác 0-Day</span>'

    tech_pills = "".join(f'<span class="vendor-tag">{html.escape(t)}</span>' for t in vulnerable_tech[:8])

    threat_widget = f"""<div class="sidebar-widget threat-radar-box">
  <div class="widget-title">
    {radar_icon}
    <span>Threat Radar / CVE Tracker</span>
  </div>
  <div>
    <div class="radar-sublabel">Lỗ hổng đang theo dõi:</div>
    <div class="cve-pill-list">{cve_pills}</div>
  </div>
  <div>
    <div class="radar-sublabel">Công nghệ bị ảnh hưởng:</div>
    <div class="tech-list">{tech_pills}</div>
  </div>
</div>"""

    # 4. Widget 4: Recent Editions (Archive)
    calendar_icon = render_material_symbol("calendar_today")
    archive_items_html: list[str] = []
    for ed in editions_data[:5]:
        pkg = ed["package"]
        ed_link = f"{base_prefix}editions/{pkg.run_id}.html"
        archive_items_html.append(
            f'<a href="{ed_link}" class="archive-mini-item">'
            f'<span>{html.escape(format_vietnam_date(ed["dt"]))}</span>'
            f'<code>{html.escape(pkg.run_id[-8:])}</code>'
            f'</a>'
        )
    archive_widget = f"""<div class="sidebar-widget">
  <div class="widget-title">
    {calendar_icon}
    <span>Ấn bản gần đây (Archive)</span>
  </div>
  <div class="archive-mini-list">
    {"".join(archive_items_html)}
  </div>
</div>"""

    return f"""<aside class="portal-sidebar">
  {podcast_widget}
  {video_widget}
  {trending_widget}
  {threat_widget}
  {archive_widget}
</aside>"""


def _render_page(
    title: str,
    content_html: str,
    active_nav: str = "",
    base_prefix: str = "",
    sub_bar_html: str = "",
    edition_strip_html: str = "",
) -> str:
    """Wrap content in complete semantic HTML5 document with Google Material Symbols."""
    header_html = _render_header(active_nav=active_nav, base_prefix=base_prefix, sub_bar_html=sub_bar_html)
    footer_html = _render_footer(base_prefix=base_prefix)
    css_href = f"{base_prefix}assets/style.css"
    js_src = f"{base_prefix}assets/app.js"

    strip_section = f"\n  {edition_strip_html}" if edition_strip_html else ""

    return f"""<!DOCTYPE html>
<html lang="vi" data-theme="light">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="description" content="{html.escape(title)} · Chuyên san Phân tích và Tình báo An ninh Mạng CyberPulse AI.">
  <title>{html.escape(title)} · CyberPulse AI Reader</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wdth,wght@12..96,75..100,400..800&family=Source+Serif+4:ital,opsz,wght@0,8..60,400..700;1,8..60,400..700&display=swap">
  <link rel="stylesheet" href="{MATERIAL_SYMBOLS_STYLESHEET}">
  <link rel="stylesheet" href="{css_href}">
</head>
<body>
  <div id="reading-progress" class="reading-progress-bar"></div>
  <div id="toast-notify" class="toast-notification">Đã sao chép liên kết vào bộ nhớ tạm!</div>
  {header_html}{strip_section}
  <main class="main-content">
    <div class="container">
      {content_html}
    </div>
  </main>
  {footer_html}
  <script src="{js_src}"></script>
</body>
</html>"""


def _render_dedicated_article_page(
    article: Article,
    relevance_reason: str,
    matching_section_texts: list[str],
    podcast_snippets: list[str],
    edition_dt_str: str,
    package: EditorialPackage,
    has_audio: bool,
    audio_src: str | None,
    sidebar_html: str,
    base_prefix: str = "../",
    prev_article: Article | None = None,
    next_article: Article | None = None,
) -> str:
    """Render full standalone single article page under dist/articles/<article_id>.html."""
    home_link = f"{base_prefix}index.html"
    timer_icon = render_material_symbol("timer")
    person_icon = render_material_symbol("person")
    schedule_icon = render_material_symbol("schedule")
    share_icon = render_material_symbol("share")
    copy_icon = render_material_symbol("content_copy")
    verified_icon = render_material_symbol("verified")
    open_icon = render_material_symbol("open_in_new")
    podcasts_icon = render_material_symbol("podcasts")
    radar_icon = render_material_symbol("bug_report")
    visibility_icon = render_material_symbol("visibility")

    # Combine text corpus for analysis
    corpus = f"{article.title} {article.summary} {relevance_reason} {' '.join(matching_section_texts)}"
    badge = detect_threat_badge(corpus)
    cves = extract_cves(corpus)
    vendor_tags = extract_vendor_tags(corpus, topics=article.topics_matched)
    reading_time = calculate_reading_time(corpus)
    baseline_views = calculate_baseline_views(article.id)

    cve_str = ", ".join(cves) if cves else "Theo dõi sự cố / Chưa gán CVE"
    tech_str = ", ".join(vendor_tags) if vendor_tags else "Hạ tầng mạng & Hệ điều hành"
    severity_str = "Nghiêm trọng (CVSS 9.0 - 10.0)" if "critical" in badge.css_class else (
        "Cảnh báo cao (CVSS 7.0 - 8.9)" if "warning" in badge.css_class else "Thông tin an ninh mạng"
    )

    # In-depth paragraphs
    in_depth_paragraphs: list[str] = []
    if matching_section_texts:
        for t in matching_section_texts:
            in_depth_paragraphs.append(f"<p>{html.escape(t)}</p>")
    elif article.summary:
        in_depth_paragraphs.append(f"<p>{html.escape(article.summary)}</p>")
    else:
        in_depth_paragraphs.append("<p>Báo cáo chi tiết đang được tổng hợp và phân tích liên tục từ trung tâm giám sát an ninh mạng.</p>")

    if relevance_reason:
        in_depth_paragraphs.append(
            f"<p><strong>Đánh giá của Ban Biên tập CyberPulse:</strong> {html.escape(relevance_reason)}</p>"
        )

    # Executive TL;DR Box
    tldr_bullets = extract_tldr_bullets(article, relevance_reason, matching_section_texts)
    tldr_box_html = _render_tldr_box(tldr_bullets)

    # Article Pagination (Prev / Next)
    pagination_html = _render_article_pagination(prev_article, next_article)

    # Podcast dialogue snippet if available
    podcast_snippet_html = ""
    if podcast_snippets:
        lines_html = "".join(f"<p class='podcast-line'>&ldquo;{html.escape(s)}&rdquo;</p>" for s in podcast_snippets[:2])
        podcast_snippet_html = f"""<div class="podcast-snippet-box">
  <div class="podcast-snippet-header">
    {podcasts_icon}
    <span>Trích đoạn thảo luận trong Podcast CyberPulse:</span>
  </div>
  {lines_html}
</div>"""

    main_col = f"""<article class="single-article-main" data-article-id="{html.escape(article.id)}">
  <nav class="breadcrumb">
    <a href="{home_link}">Trang chủ</a>
    <span>&gt;</span>
    <a href="{home_link}">Lỗ hổng &amp; Sự cố</a>
    <span>&gt;</span>
    <span>{html.escape(article.id[:8])}</span>
  </nav>

  <h1 class="article-h1-title">{html.escape(article.title)}</h1>

  <div class="article-meta-bar">
    <div class="article-meta-left">
      <span class="meta-item">{person_icon} Ban Biên tập CyberPulse</span>
      <span class="meta-item">{schedule_icon} {html.escape(edition_dt_str)} (UTC+7)</span>
      <span class="meta-item">{timer_icon} {reading_time} phút đọc</span>
      <span class="meta-item views-counter">{visibility_icon} <span id="article-views">{baseline_views:,}</span> lượt xem</span>
      <span class="badge {badge.css_class}">{html.escape(badge.label)}</span>
    </div>
    <div class="article-share-group">
      <a href="{GITHUB_REPO_URL}" target="_blank" rel="noopener noreferrer" class="share-btn" title="Xem chi tiết &amp; mã nguồn trên GitHub">{GITHUB_SVG_ICON}</a>
      <button class="share-btn" onclick="copyCurrentUrl()" title="Sao chép liên kết">{copy_icon}</button>
      <button class="share-btn" onclick="copyCurrentUrl()" title="Chia sẻ">{share_icon}</button>
    </div>
  </div>

  <div class="cyber-banner">
    <div class="cyber-banner-badge">CYBER SECURITY INTELLIGENCE REPORT</div>
    <div class="cyber-banner-title">{html.escape(article.title)}</div>
  </div>

  <div class="article-lead">
    {html.escape(article.summary or relevance_reason or article.title)}
  </div>

  {tldr_box_html}

  <div class="article-body-text">
    {"".join(in_depth_paragraphs)}
  </div>

  <div class="threat-summary-box">
    <h3>{radar_icon} Thông tin kỹ thuật &amp; Chỉ số đe dọa</h3>
    <div class="threat-specs-grid">
      <div class="spec-item">
        <strong>Mã CVE / Khai thác:</strong>
        <span>{html.escape(cve_str)}</span>
      </div>
      <div class="spec-item">
        <strong>Hệ thống bị ảnh hưởng:</strong>
        <span>{html.escape(tech_str)}</span>
      </div>
      <div class="spec-item">
        <strong>Mức độ nghiêm trọng:</strong>
        <span>{html.escape(severity_str)}</span>
      </div>
      <div class="spec-item">
        <strong>Nguồn công bố:</strong>
        <span>{html.escape(article.source_name)}</span>
      </div>
    </div>
  </div>

  <div class="mitigation-box">
    <h3>{verified_icon} Khuyến nghị an ninh mạng (Mitigation)</h3>
    <ul class="mitigation-list">
      <li>Áp dụng ngay bản cập nhật hoặc giải pháp khắc phục tạm thời từ nhà cung cấp hệ thống.</li>
      <li>Kiểm tra và cô lập các điểm cuối có dấu hiệu kết nối bất thường tới địa chỉ IP độc hại.</li>
      <li>Kích hoạt giám sát hành vi trên hệ thống SIEM/EDR đối với các tiến trình thực thi lệnh lạ.</li>
      <li>Thực thi chính sách đặc quyền tối thiểu (Least Privilege) và xác thực đa yếu tố (MFA).</li>
    </ul>
  </div>

  <div class="github-intel-box">
    <div class="github-intel-info">
      {GITHUB_SVG_ICON}
      <div>
        <div class="github-intel-title">CyberPulse Agentic Intelligence Platform</div>
        <div class="github-intel-desc">Bản tin an ninh mạng được phân tích, thẩm định tự động và lưu trữ minh bạch trên GitHub Repository &amp; CDN.</div>
      </div>
    </div>
    <a href="{GITHUB_REPO_URL}" target="_blank" rel="noopener noreferrer" class="btn-github-action">
      <span>Xem chi tiết &amp; mã nguồn trên GitHub</span>
      {open_icon}
    </a>
  </div>

  {podcast_snippet_html}

  <div class="source-box">
    <div>
      <div class="source-box-title">Nguồn tin phân tích gốc &amp; Bản sao CDN:</div>
      <div class="source-box-name">{html.escape(article.source_name)} : {html.escape(article.title)}</div>
    </div>
    <div class="source-box-actions">
      <a href="{html.escape(article.url)}" target="_blank" rel="noopener noreferrer" class="source-external-link">
        <span>Đọc bài gốc trên {html.escape(article.source_name)}</span>
        {open_icon}
      </a>
      <a href="{GITHUB_PAGES_CDN_URL}/articles/{article.id}.html" target="_blank" rel="noopener noreferrer" class="source-external-link mirror-link" title="Xem bài viết trên GitHub Pages CDN">
        <span>GitHub Pages Mirror</span>
        {open_icon}
      </a>
    </div>
  </div>

  {pagination_html}
</article>"""

    grid_content = f"""<div class="portal-grid">
  <div class="portal-main">{main_col}</div>
  {sidebar_html}
</div>"""

    utility_bar_html = _render_article_utility_bar(
        parent_edition_link=f"editions/{package.run_id}.html",
        edition_date=format_vietnam_date(parse_run_timestamp(package.run_id)),
        baseline_views=baseline_views,
    )

    return _render_page(
        title=article.title,
        content_html=grid_content,
        active_nav="vuln",
        base_prefix=base_prefix,
        sub_bar_html=utility_bar_html,
    )


def build_public_portal(
    output_dir: Path,
    dist_dir: Path = Path("dist"),
    featured_run_id: str | None = None,
    cdn_media_base_url: str = GITHUB_PAGES_CDN_URL,
) -> PortalBuildResult:
    """Build static reader portal into dist_dir with Hallmark Editorial 2-column architecture.

    1. Scans editorial packages from output_dir / "editorial"/*.json.
    2. Builds homepage (dist/index.html) with Hero Breaking News & Curated Stream.
    3. Generates dedicated standalone single article pages (dist/articles/<article_id>.html).
    4. Generates archive page (dist/archive.html) and edition pages (dist/editions/<run_id>.html).
    5. Writes assets (style.css, app.js) with 24x24 Google Material Symbols.
    6. Streams podcast audio and video from GitHub Pages CDN without local host storage.
    7. Copies media files to dist for GitHub Pages deployment.
    8. ABSOLUTELY NO JSON FEED generation or references.
    """
    out_path = Path(output_dir)
    dist_path = Path(dist_dir)

    def _resolve_media_url(filename: str, local_prefix: str = "") -> str:
        """Resolve streaming media URL from GitHub Pages CDN or fallback to local relative path."""
        if cdn_media_base_url:
            return f"{cdn_media_base_url.rstrip('/')}/media/{filename}"
        return f"{local_prefix}media/{filename}"

    # Prepare directories
    assets_dir = dist_path / "assets"
    articles_dir = dist_path / "articles"
    editions_dir = dist_path / "editions"
    media_dist_dir = dist_path / "media"

    assets_dir.mkdir(parents=True, exist_ok=True)
    articles_dir.mkdir(parents=True, exist_ok=True)
    editions_dir.mkdir(parents=True, exist_ok=True)

    files_created: list[str] = []

    # 1. Write CSS and JS assets
    css_path = assets_dir / "style.css"
    css_path.write_text(_get_style_css(), encoding="utf-8")
    files_created.append("assets/style.css")

    js_path = assets_dir / "app.js"
    js_path.write_text(_get_app_js(), encoding="utf-8")
    files_created.append("assets/app.js")

    # 2. Discover editorial packages
    editorial_dir = out_path / "editorial"
    editions_data: list[dict[str, Any]] = []
    global_article_lookup: dict[str, Article] = {}
    global_research_lookup: dict[str, dict[str, Any]] = {}

    if editorial_dir.exists() and editorial_dir.is_dir():
        for f in sorted(editorial_dir.glob("*.json")):
            if f.name.endswith(".original.json"):
                continue
            try:
                raw_text = f.read_text(encoding="utf-8")
                pkg = EditorialPackage.model_validate_json(raw_text)
                dt = parse_run_timestamp(pkg.run_id, f)

                # Look up research articles if available
                article_lookup: dict[str, Article] = {}
                research_file = out_path / "research" / f"{pkg.run_id}.json"
                if research_file.exists() and research_file.is_file():
                    try:
                        res = ResearchResult.model_validate_json(research_file.read_text(encoding="utf-8"))
                        for item in res.items:
                            article_lookup[item.article.id] = item.article
                            global_article_lookup[item.article.id] = item.article
                            global_research_lookup[item.article.id] = {
                                "rank": item.rank,
                                "relevance_reason": item.relevance_reason,
                                "run_id": pkg.run_id,
                            }
                    except Exception as err:
                        logger.warning("Could not parse research file %s: %s", research_file, err)

                # Check for audio media in media/podcast or export/run_id
                src_mp3 = out_path / "media" / "podcast" / f"{pkg.run_id}.mp3"
                if not src_mp3.exists():
                    src_mp3 = out_path / "export" / pkg.run_id / "podcast.mp3"
                if not src_mp3.exists():
                    src_mp3 = Path("export") / pkg.run_id / "podcast.mp3"

                has_audio = False
                if src_mp3.exists() and src_mp3.is_file():
                    media_dist_dir.mkdir(parents=True, exist_ok=True)
                    dest_mp3 = media_dist_dir / f"{pkg.run_id}.mp3"
                    shutil.copy2(src_mp3, dest_mp3)
                    has_audio = True
                    files_created.append(f"media/{pkg.run_id}.mp3")

                # Check for video media in media/video
                src_mp4 = out_path / "media" / "video" / f"{pkg.run_id}.mp4"
                if not src_mp4.exists():
                    src_mp4 = out_path / "export" / pkg.run_id / f"{pkg.run_id}.mp4"
                if not src_mp4.exists():
                    src_mp4 = Path("export") / pkg.run_id / f"{pkg.run_id}.mp4"

                has_video = False
                if src_mp4.exists() and src_mp4.is_file():
                    media_dist_dir.mkdir(parents=True, exist_ok=True)
                    dest_mp4 = media_dist_dir / f"{pkg.run_id}.mp4"
                    shutil.copy2(src_mp4, dest_mp4)
                    has_video = True
                    files_created.append(f"media/{pkg.run_id}.mp4")

                src_cover = out_path / "media" / "video" / f"{pkg.run_id}_cover.png"
                has_cover = False
                if src_cover.exists() and src_cover.is_file():
                    media_dist_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_cover, media_dist_dir / f"{pkg.run_id}_cover.png")
                    has_cover = True
                    files_created.append(f"media/{pkg.run_id}_cover.png")

                editions_data.append({
                    "package": pkg,
                    "dt": dt,
                    "dt_vn_str": format_vietnam_time(dt),
                    "article_lookup": article_lookup,
                    "has_audio": has_audio,
                    "has_video": has_video,
                    "has_cover": has_cover,
                    "article_count": len(pkg.digest.sections),
                })
            except Exception as err:
                logger.warning("Could not parse editorial package from %s: %s", f, err)

    # Sort editions descending by timestamp
    editions_data.sort(key=lambda x: x["dt"], reverse=True)
    editions_count = len(editions_data)

    # 3. Determine featured edition
    featured_entry: dict[str, Any] | None = None
    featured_id = ""
    featured_has_audio = False

    if editions_data:
        if featured_run_id:
            featured_entry = next((e for e in editions_data if e["package"].run_id == featured_run_id), None)
        if not featured_entry:
            featured_entry = editions_data[0]

        featured_id = featured_entry["package"].run_id
        featured_has_audio = featured_entry["has_audio"]

    # 4. Generate all cited Single Article Pages (dist/articles/<article_id>.html)
    # Collect all cited article IDs across editions
    articles_generated: set[str] = set()

    for ed in editions_data:
        pkg = ed["package"]
        ed_dt_str = ed["dt_vn_str"]
        audio_src_rel = _resolve_media_url(f"{pkg.run_id}.mp3", "../") if ed["has_audio"] else None
        video_src_rel = _resolve_media_url(f"{pkg.run_id}.mp4", "../") if ed["has_video"] else None
        video_cover_rel = _resolve_media_url(f"{pkg.run_id}_cover.png", "../") if ed.get("has_cover") else None

        # Gather trending, active CVEs, and tech for this edition
        trending_stories: list[dict[str, Any]] = []
        all_cves: list[str] = []
        all_tech: list[str] = []

        for t_idx, sec in enumerate(pkg.digest.sections, start=1):
            sec_corpus = f"{sec.heading} {sec.body.text}"
            sec_cves = extract_cves(sec_corpus)
            all_cves.extend(sec_cves)
            sec_tags = extract_vendor_tags(sec_corpus)
            all_tech.extend(sec_tags)
            sec_badge = detect_threat_badge(sec_corpus)
            sec_rt = calculate_reading_time(sec_corpus)
            primary_aid = sec.body.cited_article_ids[0] if sec.body.cited_article_ids else ""
            trending_stories.append({
                "title": sec.heading,
                "link": f"{primary_aid}.html" if primary_aid else "#",
                "time_str": f"Chuyên mục #{t_idx} · {sec_badge.text}",
            })

        # Remove duplicates while preserving order
        unique_cves = list(dict.fromkeys(all_cves))
        unique_tech = list(dict.fromkeys(all_tech))

        sidebar_article_html = _render_sidebar(
            featured_pkg=pkg,
            has_audio=ed["has_audio"],
            audio_src=audio_src_rel,
            editions_data=editions_data,
            trending_stories=trending_stories,
            active_cves=unique_cves,
            vulnerable_tech=unique_tech,
            base_prefix="../",
            has_video=ed["has_video"],
            video_src=video_src_rel,
            video_cover_src=video_cover_rel,
        )

        # Gather ordered list of articles for this edition to enable pagination
        edition_articles: list[Article] = []
        for sec in pkg.digest.sections:
            for aid in sec.body.cited_article_ids:
                if not any(a.id == aid for a in edition_articles):
                    art = ed["article_lookup"].get(aid) or global_article_lookup.get(aid)
                    if not art:
                        art = Article(
                            id=aid,
                            source_name="The Hacker News",
                            title=f"Phân tích chuyên sâu An ninh mạng : Mã sự cố #{aid[:8]}",
                            url="https://thehackernews.com/",
                            published_at=ed["dt"],
                            fetched_at=ed["dt"],
                            summary=f"Bản phân tích tình báo an ninh mạng cho sự cố #{aid[:8]} trong ấn bản {pkg.digest.title}.",
                        )
                    edition_articles.append(art)

        # Include any remaining cited IDs not in section order
        for aid in pkg.all_cited_ids():
            if not any(a.id == aid for a in edition_articles):
                art = ed["article_lookup"].get(aid) or global_article_lookup.get(aid)
                if not art:
                    art = Article(
                        id=aid,
                        source_name="The Hacker News",
                        title=f"Phân tích chuyên sâu An ninh mạng : Mã sự cố #{aid[:8]}",
                        url="https://thehackernews.com/",
                        published_at=ed["dt"],
                        fetched_at=ed["dt"],
                        summary=f"Bản phân tích tình báo an ninh mạng cho sự cố #{aid[:8]} trong ấn bản {pkg.digest.title}.",
                    )
                edition_articles.append(art)

        for idx, art in enumerate(edition_articles):
            aid = art.id
            if aid in articles_generated:
                continue

            prev_art = edition_articles[idx - 1] if idx > 0 else None
            next_art = edition_articles[idx + 1] if idx < len(edition_articles) - 1 else None

            res_info = global_research_lookup.get(aid, {})
            relevance_reason = res_info.get("relevance_reason", "")

            # Matching digest sections
            matching_texts = [
                f"{sec.heading}: {sec.body.text}"
                for sec in pkg.digest.sections
                if aid in sec.body.cited_article_ids
            ]

            # Matching podcast segments
            matching_podcast = [
                f"{seg.speaker}: {seg.line.text}"
                for seg in pkg.podcast.segments
                if aid in seg.line.cited_article_ids
            ]

            article_page_html = _render_dedicated_article_page(
                article=art,
                relevance_reason=relevance_reason,
                matching_section_texts=matching_texts,
                podcast_snippets=matching_podcast,
                edition_dt_str=ed_dt_str,
                package=pkg,
                has_audio=ed["has_audio"],
                audio_src=audio_src_rel,
                sidebar_html=sidebar_article_html,
                base_prefix="../",
                prev_article=prev_art,
                next_article=next_art,
            )

            art_file = articles_dir / f"{aid}.html"
            art_file.write_text(article_page_html, encoding="utf-8")
            files_created.append(f"articles/{aid}.html")
            articles_generated.add(aid)

    # 5. Generate index.html (Hallmark Editorial 2-Column Homepage)
    if featured_entry:
        featured_pkg = featured_entry["package"]
        featured_dt_str = featured_entry["dt_vn_str"]
        audio_src_home = _resolve_media_url(f"{featured_pkg.run_id}.mp3", "") if featured_entry["has_audio"] else None
        video_src_home = _resolve_media_url(f"{featured_pkg.run_id}.mp4", "") if featured_entry.get("has_video") else None
        video_cover_home = _resolve_media_url(f"{featured_pkg.run_id}_cover.png", "") if featured_entry.get("has_cover") else None

        # Build trending, cves, and tech for featured edition
        trending_home: list[dict[str, Any]] = []
        home_cves: list[str] = []
        home_tech: list[str] = []

        for t_idx, sec in enumerate(featured_pkg.digest.sections, start=1):
            sec_corpus = f"{sec.heading} {sec.body.text}"
            home_cves.extend(extract_cves(sec_corpus))
            home_tech.extend(extract_vendor_tags(sec_corpus))
            sec_badge = detect_threat_badge(sec_corpus)
            sec_rt = calculate_reading_time(sec_corpus)
            primary_aid = sec.body.cited_article_ids[0] if sec.body.cited_article_ids else ""
            trending_home.append({
                "title": _excerpt_text(sec.heading, 42),
                "link": f"articles/{primary_aid}.html" if primary_aid else "#",
                "time_str": f"#{t_idx} · {sec_badge.text}",
            })

        unique_home_cves = list(dict.fromkeys(home_cves))
        unique_home_tech = list(dict.fromkeys(home_tech))

        sidebar_home_html = _render_sidebar(
            featured_pkg=featured_pkg,
            has_audio=featured_entry["has_audio"],
            audio_src=audio_src_home,
            editions_data=editions_data,
            trending_stories=trending_home,
            active_cves=unique_home_cves,
            vulnerable_tech=unique_home_tech,
            base_prefix="",
            has_video=featured_entry.get("has_video", False),
            video_src=video_src_home,
            video_cover_src=video_cover_home,
        )

        # Counts for interactive filter pills
        sec_badges = [detect_threat_badge(f"{sec.heading} {sec.body.text}") for sec in featured_pkg.digest.sections]
        total_cnt = len(featured_pkg.digest.sections)
        crit_cnt = sum(1 for b in sec_badges if "critical" in b.css_class)
        warn_cnt = sum(1 for b in sec_badges if "warning" in b.css_class or "high" in b.css_class)
        patch_cnt = sum(1 for b in sec_badges if "patch" in b.css_class)
        filter_bar_home = _render_filter_bar(total_cnt, crit_cnt, warn_cnt, patch_cnt)

        # Edition strip
        edition_strip_home = _render_edition_strip(editions_data, active_run_id=featured_pkg.run_id, base_prefix="")

        # Baseline view count for edition
        edition_aids = featured_pkg.all_cited_ids()
        total_edition_views = sum(calculate_baseline_views(aid) for aid in edition_aids) if edition_aids else calculate_baseline_views(featured_pkg.run_id) * 4
        formatted_edition_views = f"{total_edition_views:,}"

        # Breaking News Hero Card (Top story)
        first_sec = featured_pkg.digest.sections[0]
        hero_badge = detect_threat_badge(f"{first_sec.heading} {first_sec.body.text}")
        hero_sev = "critical" if "critical" in hero_badge.css_class else (
            "warning" if "warning" in hero_badge.css_class else (
                "patch" if "patch" in hero_badge.css_class else "info"
            )
        )
        hero_reading_time = calculate_reading_time(f"{first_sec.heading} {first_sec.body.text}")
        hero_aid = first_sec.body.cited_article_ids[0] if first_sec.body.cited_article_ids else ""
        hero_link = f"articles/{hero_aid}.html" if hero_aid else "#"

        visibility_icon = render_material_symbol("visibility")
        timer_icon = render_material_symbol("timer")
        person_icon = render_material_symbol("person")
        schedule_icon = render_material_symbol("schedule")
        forward_icon = render_material_symbol("arrow_forward")

        hero_html = f"""<section class="hero-card filterable-card threat-card" data-severity="{hero_sev}" data-run-id="{html.escape(featured_pkg.run_id)}">
  <div class="hero-meta">
    <span class="badge {hero_badge.css_class}">{html.escape(hero_badge.label)}</span>
    <span class="meta-item">{schedule_icon} {html.escape(featured_dt_str)} (UTC+7)</span>
    <span class="meta-item">{timer_icon} {hero_reading_time} phút đọc</span>
    <span class="meta-item views-counter">{visibility_icon} <span id="edition-views-count">{formatted_edition_views}</span> lượt xem</span>
    <span class="meta-item">{person_icon} Ban Biên tập CyberPulse</span>
  </div>
  <h2 class="hero-title"><a href="{hero_link}">{html.escape(first_sec.heading)}</a></h2>
  <p class="hero-lead">{html.escape(_excerpt_text(first_sec.body.text, 75))}</p>
  <a href="{hero_link}" class="btn-read-more">
    <span>Đọc tiếp</span>
    {forward_icon}
  </a>
</section>"""

        # Curated Stories Feed (Remaining stories)
        stories_html: list[str] = []
        for s_idx, sec in enumerate(featured_pkg.digest.sections[1:], start=2):
            badge = detect_threat_badge(f"{sec.heading} {sec.body.text}")
            sev_key = "critical" if "critical" in badge.css_class else (
                "warning" if "warning" in badge.css_class else (
                    "patch" if "patch" in badge.css_class else "info"
                )
            )
            reading_time = calculate_reading_time(f"{sec.heading} {sec.body.text}")
            primary_aid = sec.body.cited_article_ids[0] if sec.body.cited_article_ids else ""
            story_link = f"articles/{primary_aid}.html" if primary_aid else "#"
            vendor_tags = extract_vendor_tags(f"{sec.heading} {sec.body.text}")
            vendor_tags_html = "".join(f'<span class="vendor-tag">{html.escape(vt)}</span>' for vt in vendor_tags)

            stories_html.append(f"""<article class="story-card filterable-card threat-card" data-severity="{sev_key}">
  <div class="story-header">
    <span class="badge {badge.css_class}">{html.escape(badge.label)}</span>
    <div class="vendor-tags">{vendor_tags_html}</div>
  </div>
  <h3 class="story-title"><a href="{story_link}">{html.escape(sec.heading)}</a></h3>
  <p class="story-body">{html.escape(_excerpt_text(sec.body.text, 65))}</p>
  <div class="story-footer">
    <div class="story-footer-meta">
      <span class="meta-item">{schedule_icon} Mục #{s_idx}</span>
      <span class="meta-item">{timer_icon} {reading_time} phút đọc</span>
    </div>
    <a href="{story_link}" class="story-read-link">
      <span>Đọc tiếp</span>
      {forward_icon}
    </a>
  </div>
</article>""")

        if not stories_html:
            # If only 1 section existed, render it as story too
            stories_html.append(f"""<article class="story-card filterable-card threat-card" data-severity="{hero_sev}">
  <h3 class="story-title"><a href="{hero_link}">{html.escape(first_sec.heading)}</a></h3>
  <p class="story-body">{html.escape(first_sec.body.text)}</p>
</article>""")

        radar_icon = render_material_symbol("bug_report")
        feed_content = f"""<div class="portal-main">
  {hero_html}
  <div class="feed-header">
    <h2 class="feed-title">
      {radar_icon}
      <span>Tiêu điểm an ninh mạng (Curated Threats)</span>
    </h2>
  </div>
  <div class="stories-stream">
    {"".join(stories_html)}
  </div>
</div>"""

        home_grid = f"""<div class="portal-grid">
  {feed_content}
  {sidebar_home_html}
</div>"""

        index_html = _render_page(
            title=featured_pkg.digest.title,
            content_html=home_grid,
            active_nav="home",
            base_prefix="",
            sub_bar_html=filter_bar_home,
            edition_strip_html=edition_strip_home,
        )
    else:
        empty_html = (
            '<div class="sidebar-widget empty-state-box">'
            '<h2>Chưa có ấn bản nào</h2>'
            '<p class="empty-state-desc">Chưa có gói biên tập nào được tìm thấy trong thư mục editorial. '
            'Vui lòng chạy pipeline hoặc lệnh <code>cyberpulse editorial</code> để tạo bản tin.</p>'
            '</div>'
        )
        index_html = _render_page(
            title="Trang chủ",
            content_html=empty_html,
            active_nav="home",
            base_prefix="",
        )

    (dist_path / "index.html").write_text(index_html, encoding="utf-8")
    files_created.append("index.html")

    # 6. Generate editions/<run_id>.html permalinks
    for ed in editions_data:
        pkg = ed["package"]
        ed_dt_str = ed["dt_vn_str"]
        audio_src_ed = _resolve_media_url(f"{pkg.run_id}.mp3", "../") if ed["has_audio"] else None
        video_src_ed = _resolve_media_url(f"{pkg.run_id}.mp4", "../") if ed["has_video"] else None
        video_cover_ed = _resolve_media_url(f"{pkg.run_id}_cover.png", "../") if ed.get("has_cover") else None

        # Counts for interactive filter pills
        sec_badges = [detect_threat_badge(f"{sec.heading} {sec.body.text}") for sec in pkg.digest.sections]
        total_cnt = len(pkg.digest.sections)
        crit_cnt = sum(1 for b in sec_badges if "critical" in b.css_class)
        warn_cnt = sum(1 for b in sec_badges if "warning" in b.css_class or "high" in b.css_class)
        patch_cnt = sum(1 for b in sec_badges if "patch" in b.css_class)
        filter_bar_ed = _render_filter_bar(total_cnt, crit_cnt, warn_cnt, patch_cnt)

        # Edition strip
        edition_strip_ed = _render_edition_strip(editions_data, active_run_id=pkg.run_id, base_prefix="../")

        # Baseline view count for edition
        edition_aids = pkg.all_cited_ids()
        total_edition_views = sum(calculate_baseline_views(aid) for aid in edition_aids) if edition_aids else calculate_baseline_views(pkg.run_id) * 4
        formatted_edition_views = f"{total_edition_views:,}"

        trending_ed: list[dict[str, Any]] = []
        ed_cves: list[str] = []
        ed_tech: list[str] = []

        for t_idx, sec in enumerate(pkg.digest.sections, start=1):
            sec_corpus = f"{sec.heading} {sec.body.text}"
            ed_cves.extend(extract_cves(sec_corpus))
            ed_tech.extend(extract_vendor_tags(sec_corpus))
            sec_badge = detect_threat_badge(sec_corpus)
            sec_rt = calculate_reading_time(sec_corpus)
            primary_aid = sec.body.cited_article_ids[0] if sec.body.cited_article_ids else ""
            trending_ed.append({
                "title": sec.heading,
                "link": f"../articles/{primary_aid}.html" if primary_aid else "#",
                "time_str": f"Chuyên mục #{t_idx} · {sec_badge.text}",
            })

        unique_ed_cves = list(dict.fromkeys(ed_cves))
        unique_ed_tech = list(dict.fromkeys(ed_tech))

        sidebar_ed_html = _render_sidebar(
            featured_pkg=pkg,
            has_audio=ed["has_audio"],
            audio_src=audio_src_ed,
            editions_data=editions_data,
            trending_stories=trending_ed,
            active_cves=unique_ed_cves,
            vulnerable_tech=unique_ed_tech,
            base_prefix="../",
            has_video=ed["has_video"],
            video_src=video_src_ed,
            video_cover_src=video_cover_ed,
        )

        ed_stories_html: list[str] = []
        forward_icon = render_material_symbol("arrow_forward")
        schedule_icon = render_material_symbol("schedule")
        timer_icon = render_material_symbol("timer")
        person_icon = render_material_symbol("person")
        visibility_icon = render_material_symbol("visibility")

        for s_idx, sec in enumerate(pkg.digest.sections, start=1):
            badge = detect_threat_badge(f"{sec.heading} {sec.body.text}")
            sev_key = "critical" if "critical" in badge.css_class else (
                "warning" if "warning" in badge.css_class else (
                    "patch" if "patch" in badge.css_class else "info"
                )
            )
            reading_time = calculate_reading_time(f"{sec.heading} {sec.body.text}")
            primary_aid = sec.body.cited_article_ids[0] if sec.body.cited_article_ids else ""
            story_link = f"../articles/{primary_aid}.html" if primary_aid else "#"
            vendor_tags = extract_vendor_tags(f"{sec.heading} {sec.body.text}")
            vendor_tags_html = "".join(f'<span class="vendor-tag">{html.escape(vt)}</span>' for vt in vendor_tags)

            ed_stories_html.append(f"""<article class="story-card filterable-card threat-card" data-severity="{sev_key}">
  <div class="story-header">
    <span class="badge {badge.css_class}">{html.escape(badge.label)}</span>
    <div class="vendor-tags">{vendor_tags_html}</div>
  </div>
  <h3 class="story-title"><a href="{story_link}">{html.escape(sec.heading)}</a></h3>
  <p class="story-body">{html.escape(sec.body.text)}</p>
  <div class="story-footer">
    <div class="story-footer-meta">
      <span class="meta-item">{schedule_icon} Mục #{s_idx} · {html.escape(ed_dt_str)}</span>
      <span class="meta-item">{timer_icon} {reading_time} phút đọc</span>
      <span class="meta-item">{person_icon} Ban Biên tập CyberPulse</span>
    </div>
    <a href="{story_link}" class="story-read-link">
      <span>Đọc tiếp</span>
      {forward_icon}
    </a>
  </div>
</article>""")

        back_icon = render_material_symbol("arrow_back")
        archive_icon = render_material_symbol("calendar_today")
        top_bar = f"""<div class="edition-top-bar">
  <a href="../index.html" class="nav-link edition-top-link">
    {back_icon} Về trang chủ
  </a>
  <a href="../archive.html" class="nav-link edition-top-link">
    {archive_icon} Bản tin theo ngày
  </a>
</div>"""

        ed_main_col = f"""<div class="portal-main">
  {top_bar}
  <div class="hero-card" data-run-id="{html.escape(pkg.run_id)}">
    <div class="hero-meta">
      <span class="meta-item">{schedule_icon} {html.escape(ed_dt_str)} (UTC+7)</span>
      <span class="meta-item views-counter">{visibility_icon} <span id="edition-views-count">{formatted_edition_views}</span> lượt xem</span>
      <span class="meta-item">Mã phiên: <code>{html.escape(pkg.run_id)}</code></span>
    </div>
    <h1 class="hero-title">{html.escape(pkg.digest.title)}</h1>
    <p class="hero-lead">{html.escape(pkg.digest.intro)}</p>
  </div>
  <div class="stories-stream">
    {"".join(ed_stories_html)}
  </div>
</div>"""

        ed_grid = f"""<div class="portal-grid">
  {ed_main_col}
  {sidebar_ed_html}
</div>"""

        ed_page_html = _render_page(
            title=pkg.digest.title,
            content_html=ed_grid,
            active_nav="",
            base_prefix="../",
            sub_bar_html=filter_bar_ed,
            edition_strip_html=edition_strip_ed,
        )
        ed_file = editions_dir / f"{pkg.run_id}.html"
        ed_file.write_text(ed_page_html, encoding="utf-8")
        files_created.append(f"editions/{pkg.run_id}.html")

    # 7. Generate archive.html (Clean list of past editions, NO JSON FEED)
    archive_cards: list[str] = []
    calendar_icon = render_material_symbol("calendar_today")
    podcasts_icon = render_material_symbol("podcasts")

    for ed in editions_data:
        pkg = ed["package"]
        audio_label = (
            f'<span class="badge badge-patch">{podcasts_icon} Podcast Audio</span>'
            if ed["has_audio"]
            else '<span class="badge badge-info">Bản tin đọc</span>'
        )
        archive_cards.append(f"""<a href="editions/{pkg.run_id}.html" class="archive-card filterable-card">
  <div class="archive-info">
    <h3>{html.escape(pkg.digest.title)}</h3>
    <div class="archive-meta">
      <span class="meta-item">{calendar_icon} {html.escape(ed["dt_vn_str"])} (UTC+7)</span>
      <span>Số bài phân tích: <strong>{ed["article_count"]}</strong></span>
      <span>Mã: <code>{html.escape(pkg.run_id)}</code></span>
    </div>
  </div>
  <div>
    {audio_label}
  </div>
</a>""")

    archive_list_html = "\n".join(archive_cards) if archive_cards else "<p>Chưa có ấn bản lưu trữ nào.</p>"
    archive_content = f"""<section class="archive-section">
  <div class="feed-header">
    <h2 class="feed-title">
      {calendar_icon}
      <span>Kho lưu trữ các ấn bản ({editions_count} bản tin)</span>
    </h2>
  </div>
  <div class="archive-grid">
    {archive_list_html}
  </div>
</section>"""

    archive_strip = _render_edition_strip(editions_data, active_run_id="", base_prefix="")
    archive_html = _render_page(
        title="Kho lưu trữ bản tin",
        content_html=archive_content,
        active_nav="archive",
        base_prefix="",
        edition_strip_html=archive_strip,
    )
    (dist_path / "archive.html").write_text(archive_html, encoding="utf-8")
    files_created.append("archive.html")

    files_created.sort()

    return PortalBuildResult(
        dist_dir=dist_path,
        files_created=files_created,
        editions_count=editions_count,
        featured_run_id=featured_id,
        has_audio=featured_has_audio,
    )
