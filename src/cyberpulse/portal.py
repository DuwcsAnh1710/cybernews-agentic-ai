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


def _get_style_css() -> str:
    """Return standalone CSS styled after The Hacker News with 2-column layout and 24x24 icons."""
    return """/* CyberPulse Reader Static Portal Stylesheet - The Hacker News Style */
:root, [data-theme="dark"] {
  --bg-primary: #0b0f19;
  --bg-surface: #111827;
  --bg-card: #162032;
  --bg-card-hover: #1e293b;
  --text-primary: #f8fafc;
  --text-secondary: #94a3b8;
  --text-muted: #64748b;
  --accent-cyber: #00f0ff;
  --accent-cyber-glow: rgba(0, 240, 255, 0.12);
  --border-color: #27354d;
  --badge-critical-bg: #450a0a;
  --badge-critical-text: #f87171;
  --badge-critical-border: #991b1b;
  --badge-warning-bg: #422006;
  --badge-warning-text: #fbbf24;
  --badge-warning-border: #92400e;
  --badge-patch-bg: #064e3b;
  --badge-patch-text: #34d399;
  --badge-patch-border: #047857;
  --badge-info-bg: #1e3a8a;
  --badge-info-text: #60a5fa;
  --badge-info-border: #1d4ed8;
  --tag-bg: #1e293b;
  --tag-text: #cbd5e1;
  --shadow: 0 4px 20px rgba(0, 0, 0, 0.35);
}

[data-theme="light"] {
  --bg-primary: #f8fafc;
  --bg-surface: #ffffff;
  --bg-card: #ffffff;
  --bg-card-hover: #f1f5f9;
  --text-primary: #0f172a;
  --text-secondary: #475569;
  --text-muted: #94a3b8;
  --accent-cyber: #0284c7;
  --accent-cyber-glow: rgba(2, 132, 199, 0.08);
  --border-color: #e2e8f0;
  --badge-critical-bg: #fee2e2;
  --badge-critical-text: #b91c1c;
  --badge-critical-border: #fca5a5;
  --badge-warning-bg: #fef3c7;
  --badge-warning-text: #b45309;
  --badge-warning-border: #fde68a;
  --badge-patch-bg: #d1fae5;
  --badge-patch-text: #047857;
  --badge-patch-border: #a7f3d0;
  --badge-info-bg: #dbeafe;
  --badge-info-text: #1d4ed8;
  --badge-info-border: #bfdbfe;
  --tag-bg: #e2e8f0;
  --tag-text: #334155;
  --shadow: 0 2px 12px rgba(0, 0, 0, 0.06);
}

* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  background-color: var(--bg-primary);
  color: var(--text-primary);
  line-height: 1.6;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  transition: background-color 0.25s ease, color 0.25s ease;
}

.container {
  width: 100%;
  max-width: 1200px;
  margin: 0 auto;
  padding: 0 20px;
}

/* Google Material Symbols (Strictly 24x24 px) */
.material-symbols-outlined {
  font-family: 'Material Symbols Outlined';
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

/* Site Header */
.site-header {
  background-color: var(--bg-surface);
  border-bottom: 1px solid var(--border-color);
  position: sticky;
  top: 0;
  z-index: 100;
  backdrop-filter: blur(8px);
}

.header-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 0;
  gap: 20px;
  border-bottom: 1px solid var(--border-color);
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  text-decoration: none;
  color: var(--text-primary);
}

.brand-icon {
  width: 32px;
  height: 32px;
  color: var(--accent-cyber);
  display: flex;
  align-items: center;
  justify-content: center;
}

.brand-text h1 {
  font-size: 1.25rem;
  font-weight: 800;
  letter-spacing: -0.02em;
  color: var(--text-primary);
}

.brand-text p {
  font-size: 0.75rem;
  color: var(--text-muted);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 14px;
}

.search-box {
  position: relative;
  min-width: 240px;
}

.search-input {
  width: 100%;
  padding: 8px 12px 8px 36px;
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  color: var(--text-primary);
  font-size: 0.88rem;
  outline: none;
  transition: border-color 0.15s ease;
}

.search-input:focus {
  border-color: var(--accent-cyber);
}

.search-icon-pos {
  position: absolute;
  left: 8px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-muted);
  pointer-events: none;
}

.theme-toggle-btn {
  background: none;
  border: 1px solid var(--border-color);
  color: var(--text-primary);
  width: 38px;
  height: 38px;
  border-radius: 8px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: border-color 0.15s ease, background-color 0.15s ease;
}

.theme-toggle-btn:hover {
  border-color: var(--accent-cyber);
  background-color: var(--accent-cyber-glow);
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
  padding: 10px 0;
  overflow-x: auto;
  white-space: nowrap;
}

.nav-link {
  color: var(--text-secondary);
  text-decoration: none;
  font-size: 0.88rem;
  font-weight: 600;
  padding: 6px 14px;
  border-radius: 6px;
  transition: color 0.15s ease, background-color 0.15s ease;
}

.nav-link:hover, .nav-link.active {
  color: var(--accent-cyber);
  background-color: var(--accent-cyber-glow);
}

/* The Hacker News 2-Column Grid Architecture */
.portal-grid {
  display: grid;
  grid-template-columns: 1fr 340px;
  gap: 32px;
  margin-top: 28px;
  margin-bottom: 60px;
}

.portal-main {
  min-width: 0;
}

.portal-sidebar {
  display: flex;
  flex-direction: column;
  gap: 24px;
  position: sticky;
  top: 80px;
  align-self: start;
}

/* Breaking News Hero Card */
.hero-card {
  background-color: var(--bg-surface);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 28px;
  margin-bottom: 32px;
  box-shadow: var(--shadow);
  position: relative;
  overflow: hidden;
}

.hero-card::before {
  content: "";
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 4px;
  background: linear-gradient(90deg, var(--accent-cyber), #3b82f6);
}

.hero-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 16px;
  font-size: 0.85rem;
  color: var(--text-muted);
  margin-bottom: 14px;
}

.meta-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.hero-title {
  font-size: 1.85rem;
  font-weight: 800;
  line-height: 1.28;
  margin-bottom: 16px;
  color: var(--text-primary);
}

.hero-title a {
  color: inherit;
  text-decoration: none;
}

.hero-title a:hover {
  color: var(--accent-cyber);
}

.hero-lead {
  font-size: 1.05rem;
  color: var(--text-secondary);
  line-height: 1.65;
  margin-bottom: 22px;
}

.btn-read-more {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background-color: var(--accent-cyber);
  color: #0b0f19;
  font-weight: 700;
  font-size: 0.92rem;
  padding: 10px 20px;
  border-radius: 6px;
  text-decoration: none;
  transition: transform 0.15s ease, filter 0.15s ease;
}

.btn-read-more:hover {
  filter: brightness(1.15);
  transform: translateX(2px);
}

/* Curated Stories Feed */
.feed-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
  padding-bottom: 12px;
  border-bottom: 2px solid var(--border-color);
}

.feed-title {
  font-size: 1.35rem;
  font-weight: 800;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 10px;
}

.stories-stream {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.story-card {
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  padding: 22px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  box-shadow: var(--shadow);
  transition: transform 0.2s ease, border-color 0.2s ease, background-color 0.2s ease;
}

.story-card:hover {
  transform: translateY(-2px);
  border-color: var(--accent-cyber);
  background-color: var(--bg-card-hover);
}

.story-header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.badge {
  font-size: 0.75rem;
  font-weight: 700;
  padding: 3px 8px;
  border-radius: 4px;
  border: 1px solid transparent;
  letter-spacing: 0.02em;
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
  gap: 6px;
}

.vendor-tag {
  background-color: var(--tag-bg);
  color: var(--tag-text);
  font-size: 0.72rem;
  font-weight: 600;
  padding: 2px 7px;
  border-radius: 4px;
}

.story-title {
  font-size: 1.25rem;
  font-weight: 700;
  line-height: 1.35;
  color: var(--text-primary);
}

.story-title a {
  color: inherit;
  text-decoration: none;
}

.story-title a:hover {
  color: var(--accent-cyber);
}

.story-body {
  font-size: 0.94rem;
  color: var(--text-secondary);
  line-height: 1.6;
}

.story-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 6px;
  padding-top: 10px;
  border-top: 1px solid var(--border-color);
  font-size: 0.85rem;
  color: var(--text-muted);
}

.story-read-link {
  color: var(--accent-cyber);
  font-weight: 600;
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.story-read-link:hover {
  text-decoration: underline;
}

/* Sidebar Widgets */
.sidebar-widget {
  background-color: var(--bg-surface);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  padding: 20px;
  box-shadow: var(--shadow);
}

.widget-title {
  font-size: 1.05rem;
  font-weight: 700;
  color: var(--text-primary);
  margin-bottom: 16px;
  display: flex;
  align-items: center;
  gap: 8px;
  border-bottom: 1px solid var(--border-color);
  padding-bottom: 10px;
}

.widget-title .material-symbols-outlined {
  color: var(--accent-cyber);
}

/* Widget 1: Podcast Radio Box */
.podcast-radio-box {
  background: linear-gradient(180deg, var(--bg-card) 0%, var(--bg-surface) 100%);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  padding: 20px;
}

.podcast-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 0.72rem;
  font-weight: 700;
  background-color: var(--accent-cyber-glow);
  color: var(--accent-cyber);
  border: 1px solid var(--accent-cyber);
  padding: 3px 8px;
  border-radius: 20px;
  margin-bottom: 12px;
}

.podcast-controls {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
}

.podcast-play-btn {
  background: var(--accent-cyber);
  color: #0b0f19;
  border: none;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: transform 0.15s ease, filter 0.15s ease;
  flex-shrink: 0;
}

.podcast-play-btn:hover {
  filter: brightness(1.15);
  transform: scale(1.05);
}

.podcast-play-btn .btn-pause { display: none; }
.podcast-play-btn.playing .btn-play { display: none; }
.podcast-play-btn.playing .btn-pause { display: inline-flex; }

.podcast-scrubber-wrap {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.podcast-scrubber {
  width: 100%;
  accent-color: var(--accent-cyber);
  height: 5px;
  cursor: pointer;
}

.podcast-time {
  font-size: 0.75rem;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
  display: flex;
  justify-content: space-between;
}

.speed-toggle-btn {
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  color: var(--text-secondary);
  font-size: 0.75rem;
  font-weight: 700;
  padding: 4px 8px;
  border-radius: 6px;
  cursor: pointer;
  transition: border-color 0.15s ease, color 0.15s ease;
}

.speed-toggle-btn:hover {
  border-color: var(--accent-cyber);
  color: var(--accent-cyber);
}

/* Waveform Animation */
.waveform-bars {
  display: inline-flex;
  align-items: flex-end;
  gap: 3px;
  height: 20px;
  margin-left: auto;
}

.waveform-bar {
  width: 3px;
  height: 4px;
  background-color: var(--accent-cyber);
  border-radius: 2px;
  transition: height 0.2s ease;
}

.is-playing .waveform-bar:nth-child(1) { animation: waveAnim 0.7s ease-in-out infinite 0.1s; }
.is-playing .waveform-bar:nth-child(2) { animation: waveAnim 0.7s ease-in-out infinite 0.3s; }
.is-playing .waveform-bar:nth-child(3) { animation: waveAnim 0.7s ease-in-out infinite 0.2s; }
.is-playing .waveform-bar:nth-child(4) { animation: waveAnim 0.7s ease-in-out infinite 0.4s; }
.is-playing .waveform-bar:nth-child(5) { animation: waveAnim 0.7s ease-in-out infinite 0.15s; }

@keyframes waveAnim {
  0%, 100% { height: 4px; }
  50% { height: 18px; }
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
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border-color);
  text-decoration: none;
  color: var(--text-primary);
  transition: transform 0.15s ease;
}

.trending-item:last-child {
  border-bottom: none;
  padding-bottom: 0;
}

.trending-item:hover {
  transform: translateX(3px);
}

.trending-num {
  font-size: 1.2rem;
  font-weight: 800;
  color: var(--accent-cyber);
  font-family: monospace;
  line-height: 1;
  min-width: 26px;
}

.trending-content h4 {
  font-size: 0.88rem;
  font-weight: 600;
  line-height: 1.35;
  margin-bottom: 4px;
  color: var(--text-primary);
}

.trending-content p {
  font-size: 0.75rem;
  color: var(--text-muted);
}

/* Widget 3: Threat Radar / CVE Tracker */
.threat-radar-box {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.cve-pill-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.cve-pill {
  font-family: monospace;
  font-size: 0.75rem;
  font-weight: 700;
  background-color: var(--badge-critical-bg);
  color: var(--badge-critical-text);
  border: 1px solid var(--badge-critical-border);
  padding: 2px 7px;
  border-radius: 4px;
}

.tech-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}

/* Widget 4: Recent Editions */
.archive-mini-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.archive-mini-item {
  font-size: 0.85rem;
  color: var(--text-secondary);
  text-decoration: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 0;
  border-bottom: 1px solid var(--border-color);
  transition: color 0.15s ease;
}

.archive-mini-item:last-child {
  border-bottom: none;
}

.archive-mini-item:hover {
  color: var(--accent-cyber);
}

/* Dedicated Single Article Page Styles */
.reading-progress-bar {
  position: fixed;
  top: 0;
  left: 0;
  height: 3px;
  background: var(--accent-cyber);
  z-index: 9999;
  width: 0%;
  transition: width 0.1s linear;
}

.breadcrumb {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.85rem;
  color: var(--text-muted);
  margin-bottom: 16px;
}

.breadcrumb a {
  color: var(--text-secondary);
  text-decoration: none;
}

.breadcrumb a:hover {
  color: var(--accent-cyber);
}

.article-h1-title {
  font-size: 2.2rem;
  font-weight: 800;
  line-height: 1.25;
  color: var(--text-primary);
  margin-bottom: 16px;
}

.article-meta-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 0;
  border-top: 1px solid var(--border-color);
  border-bottom: 1px solid var(--border-color);
  margin-bottom: 24px;
  font-size: 0.88rem;
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
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  color: var(--text-secondary);
  width: 34px;
  height: 34px;
  border-radius: 6px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: border-color 0.15s ease, color 0.15s ease;
}

.share-btn:hover {
  border-color: var(--accent-cyber);
  color: var(--accent-cyber);
}

/* Cyber Security Graphic Banner */
.cyber-banner {
  background: linear-gradient(135deg, #0f172a 0%, #1e293b 50%, #0369a1 100%);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  padding: 32px 24px;
  margin-bottom: 28px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  position: relative;
  overflow: hidden;
}

.cyber-banner::after {
  content: "";
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  width: 40%;
  background: radial-gradient(circle at center, rgba(0, 240, 255, 0.15) 0%, transparent 70%);
  pointer-events: none;
}

.cyber-banner-badge {
  align-self: flex-start;
  font-size: 0.75rem;
  font-weight: 700;
  background-color: rgba(0, 240, 255, 0.2);
  color: var(--accent-cyber);
  border: 1px solid var(--accent-cyber);
  padding: 3px 10px;
  border-radius: 4px;
}

.cyber-banner-title {
  font-size: 1.3rem;
  font-weight: 700;
  color: #ffffff;
  max-width: 80%;
}

/* Article Content Body */
.article-lead {
  font-size: 1.15rem;
  font-weight: 500;
  line-height: 1.7;
  color: var(--text-primary);
  margin-bottom: 24px;
  border-left: 3px solid var(--accent-cyber);
  padding-left: 16px;
}

.article-body-text {
  font-size: 1rem;
  line-height: 1.8;
  color: var(--text-secondary);
  margin-bottom: 28px;
}

.article-body-text p {
  margin-bottom: 16px;
}

/* Threat Box & Defense Mitigation Box */
.threat-summary-box {
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-left: 4px solid var(--badge-critical-text);
  border-radius: 8px;
  padding: 20px;
  margin-bottom: 28px;
}

.threat-summary-box h3 {
  font-size: 1.1rem;
  font-weight: 700;
  color: var(--text-primary);
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.threat-specs-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 14px;
  font-size: 0.88rem;
}

.spec-item strong {
  display: block;
  font-size: 0.75rem;
  color: var(--text-muted);
  text-transform: uppercase;
  margin-bottom: 2px;
}

.mitigation-box {
  background-color: var(--bg-card);
  border: 1px solid var(--badge-patch-border);
  border-left: 4px solid var(--badge-patch-text);
  border-radius: 8px;
  padding: 20px;
  margin-bottom: 28px;
}

.mitigation-box h3 {
  font-size: 1.1rem;
  font-weight: 700;
  color: var(--badge-patch-text);
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.mitigation-list {
  padding-left: 20px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 0.92rem;
  color: var(--text-secondary);
}

/* Podcast Snippet Box */
.podcast-snippet-box {
  background-color: var(--bg-surface);
  border: 1px dashed var(--border-color);
  border-radius: 8px;
  padding: 18px;
  margin-bottom: 28px;
}

.podcast-snippet-header {
  font-size: 0.95rem;
  font-weight: 700;
  color: var(--text-primary);
  margin-bottom: 10px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.podcast-line {
  font-size: 0.9rem;
  color: var(--text-secondary);
  font-style: italic;
  line-height: 1.5;
}

/* Source Attribution Box */
.source-box {
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 16px 20px;
  margin-bottom: 28px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.source-box-title {
  font-size: 0.9rem;
  color: var(--text-muted);
}

.source-box-name {
  font-size: 1rem;
  font-weight: 700;
  color: var(--text-primary);
}

.source-external-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--accent-cyber);
  font-weight: 600;
  font-size: 0.9rem;
  text-decoration: none;
}

.source-external-link:hover {
  text-decoration: underline;
}

/* Toast Notification */
.toast-notification {
  position: fixed;
  bottom: 24px;
  right: 24px;
  background-color: var(--bg-surface);
  color: var(--text-primary);
  border: 1px solid var(--accent-cyber);
  padding: 10px 18px;
  border-radius: 8px;
  box-shadow: var(--shadow);
  opacity: 0;
  transform: translateY(10px);
  transition: opacity 0.25s ease, transform 0.25s ease;
  pointer-events: none;
  z-index: 10000;
  font-size: 0.88rem;
  font-weight: 600;
}

.toast-notification.show {
  opacity: 1;
  transform: translateY(0);
}

/* Site Footer */
.site-footer {
  background-color: var(--bg-surface);
  border-top: 1px solid var(--border-color);
  padding: 32px 0;
  margin-top: auto;
  color: var(--text-muted);
  font-size: 0.88rem;
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
  gap: 16px;
}

.footer-link {
  color: var(--text-secondary);
  text-decoration: none;
}

.footer-link:hover {
  color: var(--accent-cyber);
}

/* Archive List Layout */
.archive-grid {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.archive-card {
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  padding: 20px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  text-decoration: none;
  color: var(--text-primary);
  transition: transform 0.15s ease, border-color 0.15s ease, background-color 0.15s ease;
}

.archive-card:hover {
  transform: translateX(4px);
  border-color: var(--accent-cyber);
  background-color: var(--bg-card-hover);
}

.archive-info h3 {
  font-size: 1.15rem;
  font-weight: 700;
  margin-bottom: 6px;
  color: var(--text-primary);
}

.archive-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  font-size: 0.82rem;
  color: var(--text-muted);
}

/* Responsive adjustments */
@media (max-width: 900px) {
  .portal-grid {
    grid-template-columns: 1fr;
  }
  .portal-sidebar {
    position: static;
  }
}

@media (max-width: 640px) {
  .header-top {
    flex-direction: column;
    align-items: flex-start;
  }
  .header-actions {
    width: 100%;
    justify-content: space-between;
  }
  .search-box {
    width: 100%;
  }
  .hero-title {
    font-size: 1.45rem;
  }
  .article-h1-title {
    font-size: 1.6rem;
  }
  .footer-inner {
    flex-direction: column;
    align-items: flex-start;
  }
}
"""


def _get_app_js() -> str:
    """Return vanilla JS with theme toggle, sticky podcast player, reading progress, and search filter."""
    return """/* CyberPulse Reader Static Portal Script - The Hacker News Style */
(function() {
  // Theme initialization before DOM paint
  var saved = localStorage.getItem('cyberpulse-theme') || 'dark';
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
      var current = document.documentElement.getAttribute('data-theme') || 'dark';
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
        speedBtn.textContent = newSpeed.toFixed(2).replace(/\\.00$/, '') + 'x';
      });
    }
  }

  // 4. Search Filter
  var searchInput = document.getElementById('search-input');
  if (searchInput) {
    searchInput.addEventListener('input', function() {
      var query = searchInput.value.toLowerCase().trim();
      var cards = document.querySelectorAll('.filterable-card');
      cards.forEach(function(card) {
        var text = card.textContent.toLowerCase();
        if (!query || text.indexOf(query) !== -1) {
          card.style.display = '';
        } else {
          card.style.display = 'none';
        }
      });
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
    active_nav: str,
    base_prefix: str = "",
) -> str:
    """Render top semantic header with brand, categories navigation, search, and theme toggle.

    ABSOLUTELY NO JSON FEED LINKS/BUTTONS.
    """
    home_link = f"{base_prefix}index.html"
    archive_link = f"{base_prefix}archive.html"

    home_active = " active" if active_nav == "home" else ""
    archive_active = " active" if active_nav == "archive" else ""
    vuln_active = " active" if active_nav == "vuln" else ""
    malware_active = " active" if active_nav == "malware" else ""
    patches_active = " active" if active_nav == "patches" else ""
    podcast_active = " active" if active_nav == "podcast" else ""

    shield_icon = render_material_symbol("shield")
    search_icon = render_material_symbol("search")
    dark_icon = render_material_symbol("dark_mode", extra_class="icon-dark-mode")
    light_icon = render_material_symbol("light_mode", extra_class="icon-light-mode")

    return f"""<header class="site-header">
  <div class="container">
    <div class="header-top">
      <a href="{home_link}" class="brand">
        <div class="brand-icon">{shield_icon}</div>
        <div class="brand-text">
          <h1>CyberPulse AI</h1>
          <p>Tạp chí An ninh Mạng Chuyên sâu</p>
        </div>
      </a>
      <div class="header-actions">
        <div class="search-box">
          <span class="search-icon-pos">{search_icon}</span>
          <input type="text" id="search-input" class="search-input" placeholder="Tìm kiếm tin bảo mật, CVE..." aria-label="Tìm kiếm">
        </div>
        <button id="theme-toggle" class="theme-toggle-btn" aria-label="Chuyển chế độ sáng/tối" title="Chuyển chế độ giao diện">
          {dark_icon}
          {light_icon}
        </button>
      </div>
    </div>
    <nav class="nav-bar">
      <a href="{home_link}" class="nav-link{home_active}">Trang chủ</a>
      <a href="{home_link}#vulnerabilities" class="nav-link{vuln_active}">Lỗ hổng (Vulnerabilities)</a>
      <a href="{home_link}#malware" class="nav-link{malware_active}">Mã độc &amp; Tấn công</a>
      <a href="{home_link}#patches" class="nav-link{patches_active}">Bản vá hệ thống</a>
      <a href="{home_link}#podcast" class="nav-link{podcast_active}">Podcast AI</a>
      <a href="{archive_link}" class="nav-link{archive_active}">Kho lưu trữ</a>
    </nav>
  </div>
</header>"""


def _render_footer(base_prefix: str = "") -> str:
    """Render semantic footer with copyright and navigation links (ABSOLUTELY NO JSON FEED)."""
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
) -> str:
    """Render sticky sidebar with 4 widgets (Podcast, Trending Stories, Threat Radar, Recent Editions)."""
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
  <div class="podcast-badge">Bản tin AI Voiceover</div>
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
  <p style="font-size:0.85rem; color:var(--text-muted);">Ấn bản hiện tại ở định dạng văn bản đọc.</p>
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
        cve_pills = '<span style="font-size:0.8rem; color:var(--text-muted);">Đang theo dõi chuỗi khai thác 0-Day</span>'

    tech_pills = "".join(f'<span class="vendor-tag">{html.escape(t)}</span>' for t in vulnerable_tech[:8])

    threat_widget = f"""<div class="sidebar-widget threat-radar-box">
  <div class="widget-title">
    {radar_icon}
    <span>Threat Radar / CVE Tracker</span>
  </div>
  <div>
    <div style="font-size:0.78rem; font-weight:700; color:var(--text-muted); margin-bottom:6px;">LỖ HỔNG ĐANG THEO DÕI:</div>
    <div class="cve-pill-list">{cve_pills}</div>
  </div>
  <div>
    <div style="font-size:0.78rem; font-weight:700; color:var(--text-muted); margin-bottom:6px;">CÔNG NGHỆ BỊ ẢNH HƯỞNG:</div>
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
            f'<span>{html.escape(ed["dt_vn_str"])}</span>'
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
  {trending_widget}
  {threat_widget}
  {archive_widget}
</aside>"""


def _render_page(
    title: str,
    content_html: str,
    active_nav: str,
    base_prefix: str = "",
) -> str:
    """Wrap content in complete semantic HTML5 document with Google Material Symbols."""
    header_html = _render_header(active_nav=active_nav, base_prefix=base_prefix)
    footer_html = _render_footer(base_prefix=base_prefix)
    css_href = f"{base_prefix}assets/style.css"
    js_src = f"{base_prefix}assets/app.js"

    return f"""<!DOCTYPE html>
<html lang="vi" data-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(title)} — CyberPulse AI Reader</title>
  <link rel="stylesheet" href="{MATERIAL_SYMBOLS_STYLESHEET}">
  <link rel="stylesheet" href="{css_href}">
</head>
<body>
  <div id="reading-progress" class="reading-progress-bar"></div>
  <div id="toast-notify" class="toast-notification">Đã sao chép liên kết vào bộ nhớ tạm!</div>
  {header_html}
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

    # Combine text corpus for analysis
    corpus = f"{article.title} {article.summary} {relevance_reason} {' '.join(matching_section_texts)}"
    badge = detect_threat_badge(corpus)
    cves = extract_cves(corpus)
    vendor_tags = extract_vendor_tags(corpus, topics=article.topics_matched)
    reading_time = calculate_reading_time(corpus)

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

    main_col = f"""<article class="single-article-main">
  <nav class="breadcrumb">
    <a href="{home_link}">Trang chủ</a>
    <span>&gt;</span>
    <a href="{home_link}#vulnerabilities">Lỗ hổng &amp; Sự cố</a>
    <span>&gt;</span>
    <span>{html.escape(article.id[:8])}</span>
  </nav>

  <h1 class="article-h1-title">{html.escape(article.title)}</h1>

  <div class="article-meta-bar">
    <div class="article-meta-left">
      <span class="meta-item">{person_icon} Ban Biên tập CyberPulse</span>
      <span class="meta-item">{schedule_icon} {html.escape(edition_dt_str)} (UTC+7)</span>
      <span class="meta-item">{timer_icon} {reading_time} phút đọc</span>
      <span class="badge {badge.css_class}">{html.escape(badge.label)}</span>
    </div>
    <div class="article-share-group">
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

  {podcast_snippet_html}

  <div class="source-box">
    <div>
      <div class="source-box-title">Nguồn tin phân tích gốc:</div>
      <div class="source-box-name">{html.escape(article.source_name)} — {html.escape(article.title)}</div>
    </div>
    <a href="{html.escape(article.url)}" target="_blank" rel="noopener noreferrer" class="source-external-link">
      <span>Đọc bài gốc trên {html.escape(article.source_name)}</span>
      {open_icon}
    </a>
  </div>
</article>"""

    grid_content = f"""<div class="portal-grid">
  <div class="portal-main">{main_col}</div>
  {sidebar_html}
</div>"""

    return _render_page(
        title=article.title,
        content_html=grid_content,
        active_nav="vuln",
        base_prefix=base_prefix,
    )


def build_public_portal(
    output_dir: Path,
    dist_dir: Path = Path("dist"),
    featured_run_id: str | None = None,
) -> PortalBuildResult:
    """Build static reader portal into dist_dir with The Hacker News 2-column architecture.

    1. Scans editorial packages from output_dir / "editorial"/*.json.
    2. Builds homepage (dist/index.html) with Hero Breaking News & Curated Stream.
    3. Generates dedicated standalone single article pages (dist/articles/<article_id>.html).
    4. Generates archive page (dist/archive.html) and edition pages (dist/editions/<run_id>.html).
    5. Writes assets (style.css, app.js) with 24x24 Google Material Symbols.
    6. Copies podcast media files when available.
    7. ABSOLUTELY NO JSON FEED generation or references.
    """
    out_path = Path(output_dir)
    dist_path = Path(dist_dir)

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

                editions_data.append({
                    "package": pkg,
                    "dt": dt,
                    "dt_vn_str": format_vietnam_time(dt),
                    "article_lookup": article_lookup,
                    "has_audio": has_audio,
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
        audio_src_rel = f"../media/{pkg.run_id}.mp3" if ed["has_audio"] else None

        # Gather trending, active CVEs, and tech for this edition
        trending_stories: list[dict[str, Any]] = []
        all_cves: list[str] = []
        all_tech: list[str] = []

        for sec in pkg.digest.sections:
            sec_cves = extract_cves(f"{sec.heading} {sec.body.text}")
            all_cves.extend(sec_cves)
            sec_tags = extract_vendor_tags(f"{sec.heading} {sec.body.text}")
            all_tech.extend(sec_tags)
            primary_aid = sec.body.cited_article_ids[0] if sec.body.cited_article_ids else ""
            trending_stories.append({
                "title": sec.heading,
                "link": f"{primary_aid}.html" if primary_aid else "#",
                "time_str": ed_dt_str,
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
        )

        for aid in pkg.all_cited_ids():
            if aid in articles_generated:
                continue

            art = ed["article_lookup"].get(aid) or global_article_lookup.get(aid)
            if not art:
                # Synthesize fallback Article model if research result not present
                art = Article(
                    id=aid,
                    source_name="The Hacker News",
                    title=f"Phân tích chuyên sâu An ninh mạng — Mã sự cố #{aid[:8]}",
                    url="https://thehackernews.com/",
                    published_at=ed["dt"],
                    fetched_at=ed["dt"],
                    summary=f"Bản phân tích tình báo an ninh mạng cho sự cố #{aid[:8]} trong ấn bản {pkg.digest.title}.",
                )

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
            )

            art_file = articles_dir / f"{aid}.html"
            art_file.write_text(article_page_html, encoding="utf-8")
            files_created.append(f"articles/{aid}.html")
            articles_generated.add(aid)

    # 5. Generate index.html (The Hacker News 2-Column Homepage)
    if featured_entry:
        featured_pkg = featured_entry["package"]
        featured_dt_str = featured_entry["dt_vn_str"]
        audio_src_home = f"media/{featured_pkg.run_id}.mp3" if featured_entry["has_audio"] else None

        # Build trending, cves, and tech for featured edition
        trending_home: list[dict[str, Any]] = []
        home_cves: list[str] = []
        home_tech: list[str] = []

        for sec in featured_pkg.digest.sections:
            home_cves.extend(extract_cves(f"{sec.heading} {sec.body.text}"))
            home_tech.extend(extract_vendor_tags(f"{sec.heading} {sec.body.text}"))
            primary_aid = sec.body.cited_article_ids[0] if sec.body.cited_article_ids else ""
            trending_home.append({
                "title": sec.heading,
                "link": f"articles/{primary_aid}.html" if primary_aid else "#",
                "time_str": featured_dt_str,
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
        )

        # Breaking News Hero Card (Top story)
        first_sec = featured_pkg.digest.sections[0]
        hero_badge = detect_threat_badge(f"{first_sec.heading} {first_sec.body.text}")
        hero_reading_time = calculate_reading_time(f"{first_sec.heading} {first_sec.body.text}")
        hero_aid = first_sec.body.cited_article_ids[0] if first_sec.body.cited_article_ids else ""
        hero_link = f"articles/{hero_aid}.html" if hero_aid else "#"

        timer_icon = render_material_symbol("timer")
        person_icon = render_material_symbol("person")
        schedule_icon = render_material_symbol("schedule")
        forward_icon = render_material_symbol("arrow_forward")

        hero_html = f"""<section class="hero-card filterable-card">
  <div class="hero-meta">
    <span class="badge {hero_badge.css_class}">{html.escape(hero_badge.label)}</span>
    <span class="meta-item">{schedule_icon} {html.escape(featured_dt_str)} (UTC+7)</span>
    <span class="meta-item">{timer_icon} {hero_reading_time} phút đọc</span>
    <span class="meta-item">{person_icon} Ban Biên tập CyberPulse</span>
  </div>
  <h2 class="hero-title"><a href="{hero_link}">{html.escape(first_sec.heading)}</a></h2>
  <p class="hero-lead">{html.escape(first_sec.body.text[:220].rstrip())}...</p>
  <a href="{hero_link}" class="btn-read-more">
    <span>Đọc tiếp</span>
    {forward_icon}
  </a>
</section>"""

        # Curated Stories Feed (Remaining stories)
        stories_html: list[str] = []
        for sec in featured_pkg.digest.sections[1:]:
            badge = detect_threat_badge(f"{sec.heading} {sec.body.text}")
            reading_time = calculate_reading_time(f"{sec.heading} {sec.body.text}")
            primary_aid = sec.body.cited_article_ids[0] if sec.body.cited_article_ids else ""
            story_link = f"articles/{primary_aid}.html" if primary_aid else "#"
            vendor_tags = extract_vendor_tags(f"{sec.heading} {sec.body.text}")
            vendor_tags_html = "".join(f'<span class="vendor-tag">{html.escape(vt)}</span>' for vt in vendor_tags)

            stories_html.append(f"""<article class="story-card filterable-card">
  <div class="story-header">
    <span class="badge {badge.css_class}">{html.escape(badge.label)}</span>
    <div class="vendor-tags">{vendor_tags_html}</div>
  </div>
  <h3 class="story-title"><a href="{story_link}">{html.escape(sec.heading)}</a></h3>
  <p class="story-body">{html.escape(sec.body.text)}</p>
  <div class="story-footer">
    <div style="display:flex; align-items:center; gap:12px;">
      <span class="meta-item">{schedule_icon} {html.escape(featured_dt_str)}</span>
      <span class="meta-item">{timer_icon} {reading_time} phút</span>
      <span class="meta-item">{person_icon} CyberPulse</span>
    </div>
    <a href="{story_link}" class="story-read-link">
      <span>Đọc tiếp</span>
      {forward_icon}
    </a>
  </div>
</article>""")

        if not stories_html:
            # If only 1 section existed, render it as story too
            stories_html.append(f"""<article class="story-card filterable-card">
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
        )
    else:
        empty_html = (
            '<div class="sidebar-widget" style="margin-top: 40px; text-align: center; padding: 40px;">'
            '<h2>Chưa có ấn bản nào</h2>'
            '<p style="color:var(--text-muted); margin-top: 10px;">Chưa có gói biên tập nào được tìm thấy trong thư mục editorial. '
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
        audio_src_ed = f"../media/{pkg.run_id}.mp3" if ed["has_audio"] else None

        trending_ed: list[dict[str, Any]] = []
        ed_cves: list[str] = []
        ed_tech: list[str] = []

        for sec in pkg.digest.sections:
            ed_cves.extend(extract_cves(f"{sec.heading} {sec.body.text}"))
            ed_tech.extend(extract_vendor_tags(f"{sec.heading} {sec.body.text}"))
            primary_aid = sec.body.cited_article_ids[0] if sec.body.cited_article_ids else ""
            trending_ed.append({
                "title": sec.heading,
                "link": f"../articles/{primary_aid}.html" if primary_aid else "#",
                "time_str": ed_dt_str,
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
        )

        ed_stories_html: list[str] = []
        forward_icon = render_material_symbol("arrow_forward")
        schedule_icon = render_material_symbol("schedule")
        timer_icon = render_material_symbol("timer")
        person_icon = render_material_symbol("person")

        for sec in pkg.digest.sections:
            badge = detect_threat_badge(f"{sec.heading} {sec.body.text}")
            reading_time = calculate_reading_time(f"{sec.heading} {sec.body.text}")
            primary_aid = sec.body.cited_article_ids[0] if sec.body.cited_article_ids else ""
            story_link = f"../articles/{primary_aid}.html" if primary_aid else "#"
            vendor_tags = extract_vendor_tags(f"{sec.heading} {sec.body.text}")
            vendor_tags_html = "".join(f'<span class="vendor-tag">{html.escape(vt)}</span>' for vt in vendor_tags)

            ed_stories_html.append(f"""<article class="story-card filterable-card">
  <div class="story-header">
    <span class="badge {badge.css_class}">{html.escape(badge.label)}</span>
    <div class="vendor-tags">{vendor_tags_html}</div>
  </div>
  <h3 class="story-title"><a href="{story_link}">{html.escape(sec.heading)}</a></h3>
  <p class="story-body">{html.escape(sec.body.text)}</p>
  <div class="story-footer">
    <div style="display:flex; align-items:center; gap:12px;">
      <span class="meta-item">{schedule_icon} {html.escape(ed_dt_str)}</span>
      <span class="meta-item">{timer_icon} {reading_time} phút</span>
      <span class="meta-item">{person_icon} CyberPulse</span>
    </div>
    <a href="{story_link}" class="story-read-link">
      <span>Đọc tiếp</span>
      {forward_icon}
    </a>
  </div>
</article>""")

        back_icon = render_material_symbol("arrow_back")
        archive_icon = render_material_symbol("calendar_today")
        top_bar = f"""<div style="margin-bottom: 20px; display:flex; gap:12px;">
  <a href="../index.html" class="nav-link" style="display:inline-flex; align-items:center; gap:6px;">
    {back_icon} Về trang chủ
  </a>
  <a href="../archive.html" class="nav-link" style="display:inline-flex; align-items:center; gap:6px;">
    {archive_icon} Kho lưu trữ
  </a>
</div>"""

        ed_main_col = f"""<div class="portal-main">
  {top_bar}
  <div class="hero-card">
    <div class="hero-meta">
      <span class="meta-item">{schedule_icon} {html.escape(ed_dt_str)} (UTC+7)</span>
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
            f'<span class="badge badge-patch" style="display:inline-flex;align-items:center;gap:4px;">{podcasts_icon} Podcast Audio</span>'
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
    archive_content = f"""<section style="margin-top: 24px; margin-bottom: 60px;">
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

    archive_html = _render_page(
        title="Kho lưu trữ bản tin",
        content_html=archive_content,
        active_nav="archive",
        base_prefix="",
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
