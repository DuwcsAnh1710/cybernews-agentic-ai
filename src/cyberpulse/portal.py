"""Public reader portal and static site generation engine for CyberPulse AI.

Generates a standalone, high-performance, responsive cybersecurity magazine website
("CyberPulse Reader") with zero external CDN dependencies, dark/light theme toggle,
interactive podcast audio player, curated threat feed, archive index, and JSON feed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import html
import json
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


# Clean SVG icons (Zero external CDNs, Zero AI emojis)
SVG_ICONS: dict[str, str] = {
    "shield": (
        '<svg class="icon icon-shield" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>'
        '</svg>'
    ),
    "sun": (
        '<svg class="icon icon-sun" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="5"></circle>'
        '<line x1="12" y1="1" x2="12" y2="3"></line>'
        '<line x1="12" y1="21" x2="12" y2="23"></line>'
        '<line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line>'
        '<line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line>'
        '<line x1="1" y1="12" x2="3" y2="12"></line>'
        '<line x1="21" y1="12" x2="23" y2="12"></line>'
        '<line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line>'
        '<line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line>'
        '</svg>'
    ),
    "moon": (
        '<svg class="icon icon-moon" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>'
        '</svg>'
    ),
    "play": (
        '<svg class="icon icon-play" viewBox="0 0 24 24" fill="currentColor">'
        '<polygon points="5 3 19 12 5 21 5 3"></polygon>'
        '</svg>'
    ),
    "pause": (
        '<svg class="icon icon-pause" viewBox="0 0 24 24" fill="currentColor">'
        '<rect x="6" y="4" width="4" height="16"></rect>'
        '<rect x="14" y="4" width="4" height="16"></rect>'
        '</svg>'
    ),
    "headphones": (
        '<svg class="icon icon-headphones" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M3 18v-6a9 9 0 0 1 18 0v6"></path>'
        '<path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"></path>'
        '</svg>'
    ),
    "search": (
        '<svg class="icon icon-search" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="11" cy="11" r="8"></circle>'
        '<line x1="21" y1="21" x2="16.65" y2="16.65"></line>'
        '</svg>'
    ),
    "external-link": (
        '<svg class="icon icon-link" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>'
        '<polyline points="15 3 21 3 21 9"></polyline>'
        '<line x1="10" y1="14" x2="21" y2="3"></line>'
        '</svg>'
    ),
    "calendar": (
        '<svg class="icon icon-cal" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect>'
        '<line x1="16" y1="2" x2="16" y2="6"></line>'
        '<line x1="8" y1="2" x2="8" y2="6"></line>'
        '<line x1="3" y1="10" x2="21" y2="10"></line>'
        '</svg>'
    ),
    "feed": (
        '<svg class="icon icon-feed" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M4 11a9 9 0 0 1 9 9"></path>'
        '<path d="M4 4a16 16 0 0 1 16 16"></path>'
        '<circle cx="5" cy="19" r="1"></circle>'
        '</svg>'
    ),
    "arrow-left": (
        '<svg class="icon icon-back" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<line x1="19" y1="12" x2="5" y2="12"></line>'
        '<polyline points="12 19 5 12 12 5"></polyline>'
        '</svg>'
    ),
    "archive": (
        '<svg class="icon icon-archive" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<polyline points="21 8 21 21 3 21 3 8"></polyline>'
        '<rect x="1" y="3" width="22" height="5"></rect>'
        '<line x1="10" y1="12" x2="14" y2="12"></line>'
        '</svg>'
    ),
}

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
    """Return clean standalone CSS with required CSS variables and responsive grid."""
    return """/* CyberPulse Reader Static Portal Stylesheet */
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
  max-width: 1160px;
  margin: 0 auto;
  padding: 0 20px;
}

/* Site Header */
.site-header {
  background-color: var(--bg-surface);
  border-bottom: 1px solid var(--border-color);
  position: sticky;
  top: 0;
  z-index: 50;
  backdrop-filter: blur(8px);
}

.header-inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 0;
  gap: 16px;
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
}

.brand-text h1 {
  font-size: 1.15rem;
  font-weight: 700;
  letter-spacing: -0.01em;
  color: var(--text-primary);
}

.brand-text p {
  font-size: 0.78rem;
  color: var(--text-muted);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 16px;
}

.site-nav {
  display: flex;
  gap: 12px;
}

.nav-link {
  color: var(--text-secondary);
  text-decoration: none;
  font-size: 0.9rem;
  font-weight: 500;
  padding: 6px 12px;
  border-radius: 6px;
  transition: color 0.15s ease, background-color 0.15s ease;
}

.nav-link:hover, .nav-link.active {
  color: var(--accent-cyber);
  background-color: var(--accent-cyber-glow);
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

[data-theme="dark"] .icon-moon { display: none; }
[data-theme="dark"] .icon-sun { display: block; }
[data-theme="light"] .icon-sun { display: none; }
[data-theme="light"] .icon-moon { display: block; }

.icon {
  width: 18px;
  height: 18px;
  vertical-align: middle;
}

/* Main Content Area */
.main-content {
  flex: 1;
  padding: 32px 0 60px 0;
}

/* Edition Hero */
.edition-hero {
  background-color: var(--bg-surface);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 28px;
  margin-bottom: 32px;
  box-shadow: var(--shadow);
}

.edition-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  font-size: 0.85rem;
  color: var(--text-muted);
  margin-bottom: 12px;
}

.edition-meta-item {
  display: flex;
  align-items: center;
  gap: 6px;
}

.edition-title {
  font-size: 1.85rem;
  font-weight: 800;
  line-height: 1.25;
  margin-bottom: 20px;
  color: var(--text-primary);
}

/* Audio Player */
.audio-player-box {
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  padding: 16px 20px;
  margin-top: 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.audio-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.audio-title-group {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--text-primary);
}

.audio-title-group .icon {
  color: var(--accent-cyber);
}

.audio-controls {
  display: flex;
  align-items: center;
  gap: 14px;
}

.audio-play-btn {
  background-color: var(--accent-cyber);
  color: #0b0f19;
  border: none;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: transform 0.15s ease, filter 0.15s ease;
  flex-shrink: 0;
}

.audio-play-btn:hover {
  filter: brightness(1.15);
  transform: scale(1.05);
}

.audio-play-btn .icon-pause { display: none; }
.audio-play-btn.playing .icon-play { display: none; }
.audio-play-btn.playing .icon-pause { display: block; }

.audio-scrubber-wrap {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 10px;
}

.audio-scrubber {
  flex: 1;
  accent-color: var(--accent-cyber);
  height: 6px;
  cursor: pointer;
}

.audio-time {
  font-size: 0.8rem;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
  min-width: 80px;
  text-align: right;
}

.speed-btn {
  background-color: var(--bg-surface);
  border: 1px solid var(--border-color);
  color: var(--text-secondary);
  font-size: 0.8rem;
  font-weight: 600;
  padding: 4px 10px;
  border-radius: 6px;
  cursor: pointer;
  transition: border-color 0.15s ease, color 0.15s ease;
}

.speed-btn:hover {
  border-color: var(--accent-cyber);
  color: var(--accent-cyber);
}

.audio-placeholder {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 0.88rem;
  color: var(--text-muted);
  background-color: var(--bg-card);
  padding: 12px 16px;
  border-radius: 8px;
  border: 1px dashed var(--border-color);
  margin-top: 16px;
}

/* 60-Second Brief */
.brief-section {
  background: var(--bg-surface);
  border-left: 4px solid var(--accent-cyber);
  border-radius: 8px;
  padding: 24px;
  margin-bottom: 36px;
  box-shadow: var(--shadow);
}

.brief-section h2 {
  font-size: 1.25rem;
  font-weight: 700;
  margin-bottom: 12px;
  color: var(--accent-cyber);
  display: flex;
  align-items: center;
  gap: 8px;
}

.brief-intro {
  font-size: 1rem;
  color: var(--text-primary);
  margin-bottom: 16px;
  line-height: 1.7;
}

.brief-list {
  padding-left: 20px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.brief-list li {
  font-size: 0.95rem;
  color: var(--text-secondary);
  line-height: 1.6;
}

.brief-list li strong {
  color: var(--text-primary);
}

/* Curated Threats Feed */
.threats-header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 24px;
}

.threats-title {
  font-size: 1.45rem;
  font-weight: 700;
  color: var(--text-primary);
}

.search-box {
  position: relative;
  min-width: 260px;
}

.search-input {
  width: 100%;
  padding: 9px 12px 9px 36px;
  background-color: var(--bg-surface);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  color: var(--text-primary);
  font-size: 0.9rem;
  outline: none;
  transition: border-color 0.15s ease;
}

.search-input:focus {
  border-color: var(--accent-cyber);
}

.search-icon-pos {
  position: absolute;
  left: 10px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-muted);
  pointer-events: none;
}

/* Responsive Threat Grid */
.threat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(330px, 1fr));
  gap: 24px;
}

.threat-card {
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  padding: 22px;
  display: flex;
  flex-direction: column;
  transition: transform 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
  box-shadow: var(--shadow);
}

.threat-card:hover {
  transform: translateY(-3px);
  border-color: var(--accent-cyber);
  background-color: var(--bg-card-hover);
}

.card-top {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 12px;
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

.threat-card-title {
  font-size: 1.12rem;
  font-weight: 700;
  line-height: 1.35;
  color: var(--text-primary);
  margin-bottom: 12px;
}

.threat-card-body {
  font-size: 0.92rem;
  color: var(--text-secondary);
  line-height: 1.6;
  margin-bottom: 18px;
  flex: 1;
}

.threat-citations {
  font-size: 0.82rem;
  color: var(--text-muted);
  border-top: 1px solid var(--border-color);
  padding-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.citation-link {
  color: var(--accent-cyber);
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  word-break: break-all;
}

.citation-link:hover {
  text-decoration: underline;
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

/* Responsive adjustments (Mobile-first) */
@media (max-width: 640px) {
  .header-inner {
    flex-direction: column;
    align-items: flex-start;
  }
  .header-actions {
    width: 100%;
    justify-content: space-between;
  }
  .edition-title {
    font-size: 1.45rem;
  }
  .threat-grid {
    grid-template-columns: 1fr;
  }
  .archive-card {
    flex-direction: column;
    align-items: flex-start;
  }
  .footer-inner {
    flex-direction: column;
    align-items: flex-start;
  }
}
"""


def _get_app_js() -> str:
    """Return vanilla JS with theme toggle, audio player controls, and search filter."""
    return """/* CyberPulse Reader Static Portal Script */
(function() {
  // Theme initialization before DOM paint to prevent flicker
  var saved = localStorage.getItem('cyberpulse-theme') || 'dark';
  document.documentElement.setAttribute('data-theme', saved);
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

  // 2. Interactive Audio Player
  var audio = document.getElementById('podcast-audio');
  var playBtn = document.getElementById('audio-play-btn');
  var scrubber = document.getElementById('audio-scrubber');
  var curTime = document.getElementById('audio-time-current');
  var durTime = document.getElementById('audio-time-duration');
  var speedBtn = document.getElementById('audio-speed-btn');

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
      playBtn.setAttribute('aria-label', 'Tạm dừng');
    });

    audio.addEventListener('pause', function() {
      playBtn.classList.remove('playing');
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

  // 3. Search Filter
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
"""


def _render_header(
    active_nav: str,
    base_prefix: str = "",
    edition_dt_str: str | None = None,
) -> str:
    """Render top semantic header with brand, navigation, and theme toggle."""
    home_link = f"{base_prefix}index.html"
    archive_link = f"{base_prefix}archive.html"
    feed_link = f"{base_prefix}feed.json"

    home_active = ' active' if active_nav == "home" else ''
    archive_active = ' active' if active_nav == "archive" else ''

    meta_date_html = ""
    if edition_dt_str:
        meta_date_html = (
            f'<div class="edition-meta-item">'
            f'{SVG_ICONS["calendar"]} '
            f'<span>Ấn bản: {html.escape(edition_dt_str)} (Giờ Việt Nam)</span>'
            f'</div>'
        )

    return f"""<header class="site-header">
  <div class="container header-inner">
    <a href="{home_link}" class="brand">
      <div class="brand-icon">{SVG_ICONS["shield"]}</div>
      <div class="brand-text">
        <h1>CyberPulse AI</h1>
        <p>Tạp chí An ninh Mạng Hàng ngày</p>
      </div>
    </a>
    <div class="header-actions">
      <nav class="site-nav">
        <a href="{home_link}" class="nav-link{home_active}">Trang chủ</a>
        <a href="{archive_link}" class="nav-link{archive_active}">Kho lưu trữ</a>
        <a href="{feed_link}" class="nav-link" title="JSON Feed" target="_blank">Feed</a>
      </nav>
      <button id="theme-toggle" class="theme-toggle-btn" aria-label="Chuyển chế độ sáng/tối" title="Chuyển chế độ giao diện">
        {SVG_ICONS["sun"]}
        {SVG_ICONS["moon"]}
      </button>
    </div>
  </div>
</header>"""


def _render_footer(base_prefix: str = "") -> str:
    """Render semantic footer with copyright and feed links."""
    archive_link = f"{base_prefix}archive.html"
    feed_link = f"{base_prefix}feed.json"

    return f"""<footer class="site-footer">
  <div class="container footer-inner">
    <div>
      <p>&copy; 2026 CyberPulse AI. Bản tin tổng hợp tự động bởi CyberPulse Agentic AI.</p>
    </div>
    <div class="footer-links">
      <a href="{archive_link}" class="footer-link">Kho lưu trữ</a>
      <a href="{feed_link}" class="footer-link" target="_blank">JSON Feed</a>
    </div>
  </div>
</footer>"""


def _render_audio_player(has_audio: bool, audio_src: str | None = None) -> str:
    """Render audio player hero section or graceful degradation note."""
    if has_audio and audio_src:
        return f"""<div class="audio-player-box">
  <div class="audio-header">
    <div class="audio-title-group">
      {SVG_ICONS["headphones"]}
      <span>Bản tin âm thanh Podcast (AI Voiceover)</span>
    </div>
    <button id="audio-speed-btn" class="speed-btn" title="Tốc độ phát">1.0x</button>
  </div>
  <audio id="podcast-audio" preload="metadata" src="{audio_src}"></audio>
  <div class="audio-controls">
    <button id="audio-play-btn" class="audio-play-btn" aria-label="Phát bản tin">
      {SVG_ICONS["play"]}
      {SVG_ICONS["pause"]}
    </button>
    <div class="audio-scrubber-wrap">
      <input type="range" id="audio-scrubber" class="audio-scrubber" min="0" value="0" step="1">
      <div class="audio-time">
        <span id="audio-time-current">00:00</span> / <span id="audio-time-duration">--:--</span>
      </div>
    </div>
  </div>
</div>"""
    return (
        f'<div class="audio-placeholder">'
        f'{SVG_ICONS["headphones"]} '
        f'<span>Bản tin dạng đọc</span>'
        f'</div>'
    )


def _render_edition_article(
    package: EditorialPackage,
    dt_vn_str: str,
    article_lookup: dict[str, Article],
    has_audio: bool,
    audio_src: str | None,
    base_prefix: str = "",
) -> str:
    """Render complete semantic article content for an edition."""
    # 60-Second Brief items
    brief_bullets: list[str] = []
    for sec in package.digest.sections:
        brief_bullets.append(
            f'<li><strong>{html.escape(sec.heading)}:</strong> '
            f'{html.escape(sec.body.text[:140].rstrip())}...</li>'
        )
    brief_bullets_html = "\n".join(brief_bullets)

    # Curated Threats Cards
    threat_cards: list[str] = []
    for sec in package.digest.sections:
        badge = detect_threat_badge(f"{sec.heading} {sec.body.text}")

        # Collect topics and citations
        topics: list[str] = []
        citations_html: list[str] = []
        for aid in sec.body.cited_article_ids:
            art = article_lookup.get(aid)
            if art:
                topics.extend(art.topics_matched)
                src_name = art.source_name
                title = art.title
                url = art.url
                citations_html.append(
                    f'<a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer" class="citation-link">'
                    f'{SVG_ICONS["external-link"]} {html.escape(src_name)}: {html.escape(title)}'
                    f'</a>'
                )
            else:
                citations_html.append(
                    f'<span class="citation-link">{SVG_ICONS["external-link"]} Trích dẫn ID: {html.escape(aid)}</span>'
                )

        vendor_tags = extract_vendor_tags(f"{sec.heading} {sec.body.text}", topics=topics)
        tags_html = "".join(f'<span class="vendor-tag">{html.escape(vt)}</span>' for vt in vendor_tags)
        cites_block = "".join(citations_html)

        threat_cards.append(f"""<article class="threat-card filterable-card">
  <div class="card-top">
    <span class="badge {badge.css_class}">{html.escape(badge.label)}</span>
    <div class="vendor-tags">
      {tags_html}
    </div>
  </div>
  <h3 class="threat-card-title">{html.escape(sec.heading)}</h3>
  <p class="threat-card-body">{html.escape(sec.body.text)}</p>
  <div class="threat-citations">
    {cites_block}
  </div>
</article>""")

    threat_cards_html = "\n".join(threat_cards)
    audio_player_html = _render_audio_player(has_audio=has_audio, audio_src=audio_src)

    return f"""<article class="edition-content">
  <section class="edition-hero">
    <div class="edition-meta">
      <div class="edition-meta-item">
        {SVG_ICONS["calendar"]}
        <span>{html.escape(dt_vn_str)} (Giờ Việt Nam)</span>
      </div>
      <div class="edition-meta-item">
        <span>Phiên chạy: <code>{html.escape(package.run_id)}</code></span>
      </div>
    </div>
    <h1 class="edition-title">{html.escape(package.digest.title)}</h1>
    {audio_player_html}
  </section>

  <section class="brief-section">
    <h2>Điểm tin nhanh (60-Second Brief)</h2>
    <p class="brief-intro">{html.escape(package.digest.intro)}</p>
    <ul class="brief-list">
      {brief_bullets_html}
    </ul>
  </section>

  <section class="threats-section">
    <div class="threats-header">
      <h2 class="threats-title">Tiêu điểm an ninh mạng (Curated Threats)</h2>
      <div class="search-box">
        <span class="search-icon-pos">{SVG_ICONS["search"]}</span>
        <input type="text" id="search-input" class="search-input" placeholder="Lọc theo tên, lỗ hổng, hãng..." aria-label="Tìm kiếm tin tức">
      </div>
    </div>
    <div class="threat-grid">
      {threat_cards_html}
    </div>
  </section>
</article>"""


def _render_page(
    title: str,
    content_html: str,
    active_nav: str,
    base_prefix: str = "",
    edition_dt_str: str | None = None,
) -> str:
    """Wrap content in complete semantic HTML5 document."""
    header_html = _render_header(active_nav=active_nav, base_prefix=base_prefix, edition_dt_str=edition_dt_str)
    footer_html = _render_footer(base_prefix=base_prefix)
    css_href = f"{base_prefix}assets/style.css"
    js_src = f"{base_prefix}assets/app.js"

    return f"""<!DOCTYPE html>
<html lang="vi" data-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(title)} — CyberPulse AI Reader</title>
  <link rel="stylesheet" href="{css_href}">
</head>
<body>
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


def _build_feed_json(editions_info: list[dict[str, Any]]) -> str:
    """Generate standard JSON Feed format v1.1 for news aggregators."""
    items: list[dict[str, Any]] = []
    for ed in editions_info:
        pkg: EditorialPackage = ed["package"]
        dt: datetime = ed["dt"]
        run_id = pkg.run_id
        items.append({
            "id": run_id,
            "url": f"editions/{run_id}.html",
            "title": pkg.digest.title,
            "summary": pkg.digest.intro,
            "content_text": f"{pkg.digest.intro}\n\n" + "\n\n".join(
                f"{sec.heading}: {sec.body.text}" for sec in pkg.digest.sections
            ),
            "date_published": dt.isoformat(),
        })

    payload = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": "CyberPulse AI — Tạp chí An ninh Mạng Hàng ngày",
        "home_page_url": "index.html",
        "feed_url": "feed.json",
        "description": "Bản tin tổng hợp tự động bởi CyberPulse Agentic AI",
        "items": items,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def build_public_portal(
    output_dir: Path,
    dist_dir: Path = Path("dist"),
    featured_run_id: str | None = None,
) -> PortalBuildResult:
    """Build static reader portal into dist_dir.

    1. Scans editorial packages from output_dir / "editorial"/*.json.
    2. Sorts packages newest first.
    3. Generates index.html, archive.html, editions/<run_id>.html, assets, and feed.json.
    4. Copies podcast media files when available.
    """
    out_path = Path(output_dir)
    dist_path = Path(dist_dir)

    # Prepare directories
    assets_dir = dist_path / "assets"
    editions_dir = dist_path / "editions"
    media_dist_dir = dist_path / "media"
    assets_dir.mkdir(parents=True, exist_ok=True)
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

    # 4. Generate index.html
    if featured_entry:
        featured_pkg = featured_entry["package"]
        audio_src = f"media/{featured_pkg.run_id}.mp3" if featured_entry["has_audio"] else None
        index_article = _render_edition_article(
            package=featured_pkg,
            dt_vn_str=featured_entry["dt_vn_str"],
            article_lookup=featured_entry["article_lookup"],
            has_audio=featured_entry["has_audio"],
            audio_src=audio_src,
            base_prefix="",
        )
        index_html = _render_page(
            title=featured_pkg.digest.title,
            content_html=index_article,
            active_nav="home",
            base_prefix="",
            edition_dt_str=featured_entry["dt_vn_str"],
        )
    else:
        empty_html = (
            '<div class="brief-section">'
            '<h2>Chưa có ấn bản nào</h2>'
            '<p class="brief-intro">Chưa có gói biên tập nào được tìm thấy trong thư mục editorial. '
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

    # 5. Generate editions/<run_id>.html permalinks
    for ed in editions_data:
        pkg = ed["package"]
        audio_src = f"../media/{pkg.run_id}.mp3" if ed["has_audio"] else None
        ed_article = _render_edition_article(
            package=pkg,
            dt_vn_str=ed["dt_vn_str"],
            article_lookup=ed["article_lookup"],
            has_audio=ed["has_audio"],
            audio_src=audio_src,
            base_prefix="../",
        )
        # Add back link at the top of standalone permalink
        back_bar = (
            f'<div style="margin-bottom: 20px;">'
            f'<a href="../index.html" class="nav-link" style="display:inline-flex; align-items:center; gap:6px;">'
            f'{SVG_ICONS["arrow-left"]} Về trang chủ'
            f'</a> '
            f'<a href="../archive.html" class="nav-link" style="display:inline-flex; align-items:center; gap:6px;">'
            f'{SVG_ICONS["archive"]} Kho lưu trữ'
            f'</a>'
            f'</div>'
        )
        ed_page_html = _render_page(
            title=pkg.digest.title,
            content_html=f"{back_bar}\n{ed_article}",
            active_nav="",
            base_prefix="../",
            edition_dt_str=ed["dt_vn_str"],
        )
        ed_file = editions_dir / f"{pkg.run_id}.html"
        ed_file.write_text(ed_page_html, encoding="utf-8")
        files_created.append(f"editions/{pkg.run_id}.html")

    # 6. Generate archive.html
    archive_cards: list[str] = []
    for ed in editions_data:
        pkg = ed["package"]
        audio_label = (
            f'<span class="badge badge-patch">{SVG_ICONS["headphones"]} Podcast Audio</span>'
            if ed["has_audio"]
            else '<span class="badge badge-info">Bản tin đọc</span>'
        )
        archive_cards.append(f"""<a href="editions/{pkg.run_id}.html" class="archive-card filterable-card">
  <div class="archive-info">
    <h3>{html.escape(pkg.digest.title)}</h3>
    <div class="archive-meta">
      <span class="edition-meta-item">{SVG_ICONS["calendar"]} {html.escape(ed["dt_vn_str"])} (Giờ Việt Nam)</span>
      <span>Số bài phân tích: <strong>{ed["article_count"]}</strong></span>
      <span>Mã: <code>{html.escape(pkg.run_id)}</code></span>
    </div>
  </div>
  <div>
    {audio_label}
  </div>
</a>""")

    archive_list_html = "\n".join(archive_cards) if archive_cards else "<p>Chưa có ấn bản lưu trữ nào.</p>"
    archive_content = f"""<section>
  <div class="threats-header">
    <h2 class="threats-title">Kho lưu trữ các ấn bản ({editions_count} bản tin)</h2>
    <div class="search-box">
      <span class="search-icon-pos">{SVG_ICONS["search"]}</span>
      <input type="text" id="search-input" class="search-input" placeholder="Tìm theo tiêu đề, ngày, mã phiên..." aria-label="Tìm kiếm ấn bản">
    </div>
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

    # 7. Generate feed.json
    feed_json_str = _build_feed_json(editions_data)
    (dist_path / "feed.json").write_text(feed_json_str, encoding="utf-8")
    files_created.append("feed.json")

    files_created.sort()

    return PortalBuildResult(
        dist_dir=dist_path,
        files_created=files_created,
        editions_count=editions_count,
        featured_run_id=featured_id,
        has_audio=featured_has_audio,
    )
