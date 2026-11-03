"""Hermetic unit tests for CyberPulse Reader static portal generation engine (Task T9.2)."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil

import pytest

from cyberpulse.models import EditorialPackage, ResearchResult
from cyberpulse.portal import (
    MATERIAL_FALLBACK_SVGS,
    MATERIAL_SYMBOLS_STYLESHEET,
    PortalBuildResult,
    build_public_portal,
    calculate_reading_time,
    detect_threat_badge,
    extract_cves,
    extract_vendor_tags,
    format_vietnam_time,
    parse_run_timestamp,
    render_material_symbol,
)
from tests.test_ui_data import ID_1, ID_2, make_sample_editorial, make_sample_research


def _setup_mock_run(
    output_dir: Path,
    run_id: str,
    title_suffix: str = "",
    with_audio: bool = False,
) -> tuple[Path, Path]:
    """Helper to set up research and editorial files for a run."""
    r_dir = output_dir / "research"
    e_dir = output_dir / "editorial"
    r_dir.mkdir(parents=True, exist_ok=True)
    e_dir.mkdir(parents=True, exist_ok=True)

    res = make_sample_research(run_id)
    pkg = make_sample_editorial(run_id)
    if title_suffix:
        pkg.digest.title = f"{pkg.digest.title} - {title_suffix}"

    res_file = r_dir / f"{run_id}.json"
    ed_file = e_dir / f"{run_id}.json"
    res_file.write_text(res.model_dump_json(), encoding="utf-8")
    ed_file.write_text(pkg.model_dump_json(), encoding="utf-8")

    if with_audio:
        audio_dir = output_dir / "media" / "podcast"
        audio_dir.mkdir(parents=True, exist_ok=True)
        (audio_dir / f"{run_id}.mp3").write_bytes(b"MOCK_MP3_DATA_FOR_TESTING")

    return res_file, ed_file


def test_build_public_portal_creates_required_files(tmp_path: Path) -> None:
    """AC 1: build_public_portal creates index.html, archive.html, dedicated article pages, and NO feed.json."""
    output_dir = tmp_path / "output"
    dist_dir = tmp_path / "dist"
    run_id = "run-20261102T020834Z-180381df"

    _setup_mock_run(output_dir, run_id)

    result = build_public_portal(output_dir=output_dir, dist_dir=dist_dir)

    assert isinstance(result, PortalBuildResult)
    assert result.dist_dir == dist_dir
    assert result.editions_count == 1
    assert result.featured_run_id == run_id

    # Verify all expected files exist on disk
    assert (dist_dir / "index.html").is_file()
    assert (dist_dir / "archive.html").is_file()
    assert (dist_dir / "assets" / "style.css").is_file()
    assert (dist_dir / "assets" / "app.js").is_file()
    assert (dist_dir / "editions" / f"{run_id}.html").is_file()

    # Verify dedicated single article pages exist
    assert (dist_dir / "articles" / f"{ID_1}.html").is_file()
    assert (dist_dir / "articles" / f"{ID_2}.html").is_file()

    # ABSOLUTELY NO JSON FEED
    assert not (dist_dir / "feed.json").exists()
    assert "feed.json" not in result.files_created

    # Check files_created list
    expected_files = {
        "index.html",
        "archive.html",
        "assets/style.css",
        "assets/app.js",
        f"editions/{run_id}.html",
        f"articles/{ID_1}.html",
        f"articles/{ID_2}.html",
    }
    assert expected_files.issubset(set(result.files_created))


def test_index_html_contains_the_hacker_news_2_column_layout(tmp_path: Path) -> None:
    """AC 2: index.html contains The Hacker News 2-column grid, Hero Breaking News, and sidebar widgets."""
    output_dir = tmp_path / "output"
    dist_dir = tmp_path / "dist"
    run_id = "run-20261101T160345Z-4cea6728"

    _setup_mock_run(output_dir, run_id, with_audio=True)
    build_public_portal(output_dir=output_dir, dist_dir=dist_dir)

    index_html = (dist_dir / "index.html").read_text(encoding="utf-8")

    # Semantic HTML5
    assert "<!DOCTYPE html>" in index_html
    assert 'name="viewport"' in index_html
    assert "<header" in index_html
    assert "</header>" in index_html
    assert "<article" in index_html
    assert "<aside" in index_html
    assert "</aside>" in index_html
    assert "<footer" in index_html
    assert "</footer>" in index_html

    # 2-Column Grid Architecture
    assert "portal-grid" in index_html
    assert "portal-main" in index_html
    assert "portal-sidebar" in index_html

    # Main Column Components: Breaking News Hero Card & Curated Threats
    assert "hero-card" in index_html
    assert "Ban Biên tập CyberPulse" in index_html
    assert "btn-read-more" in index_html
    assert "Đọc tiếp" in index_html
    assert "Tiêu điểm an ninh mạng (Curated Threats)" in index_html
    assert "story-card" in index_html

    # Sticky Sidebar: 4 Widgets
    assert "podcast-radio-box" in index_html
    assert "Podcast Radio Box" in index_html
    assert "waveform-bars" in index_html
    assert "Tin đọc nhiều / Trending" in index_html
    assert "trending-num" in index_html
    assert "01" in index_html
    assert "Threat Radar / CVE Tracker" in index_html
    assert "Ấn bản gần đây (Archive)" in index_html


def test_dedicated_single_article_pages(tmp_path: Path) -> None:
    """AC 3: Full standalone article pages with breadcrumb, meta bar, cyber banner, threat box, and mitigation."""
    output_dir = tmp_path / "output"
    dist_dir = tmp_path / "dist"
    run_id = "run-20261101T160345Z-article-test"

    _setup_mock_run(output_dir, run_id, with_audio=True)
    build_public_portal(output_dir=output_dir, dist_dir=dist_dir)

    art_file = dist_dir / "articles" / f"{ID_1}.html"
    assert art_file.is_file()
    content = art_file.read_text(encoding="utf-8")

    # Breadcrumb
    assert "breadcrumb" in content
    assert "Trang chủ" in content
    assert "Lỗ hổng &amp; Sự cố" in content

    # Headline and Meta Bar
    assert "article-h1-title" in content
    assert "SAMPLE — Flaw 1 Disclosed" in content
    assert "Ban Biên tập CyberPulse" in content
    assert "phút đọc" in content
    assert "copyCurrentUrl()" in content

    # Cyber Security Graphic Banner
    assert "cyber-banner" in content
    assert "CYBER SECURITY INTELLIGENCE REPORT" in content

    # In-depth Analytical Body & Research Relevance
    assert "Lý do chọn bài 1 (mẫu)" in content
    assert "Nội dung mục 1 (mẫu)" in content

    # Technical Threat Box
    assert "threat-summary-box" in content
    assert "Thông tin kỹ thuật &amp; Chỉ số đe dọa" in content
    assert "Mức độ nghiêm trọng:" in content

    # Defense & Mitigation Box
    assert "mitigation-box" in content
    assert "Khuyến nghị an ninh mạng (Mitigation)" in content
    assert "Áp dụng ngay bản cập nhật" in content

    # Podcast Snippet
    assert "podcast-snippet-box" in content
    assert "Minh" in content or "Lan" in content

    # Source Attribution Box
    assert "source-box" in content
    assert "The Hacker News" in content
    assert "https://example.com/sample/1" in content

    # 2-Column layout with Sidebar
    assert "portal-grid" in content
    assert "portal-sidebar" in content
    assert "podcast-radio-box" in content


def test_google_material_symbols_24px_sizing(tmp_path: Path) -> None:
    """AC 4: Google Material Symbols Outlined stylesheet embedded and strictly sized at 24x24 px."""
    output_dir = tmp_path / "output"
    dist_dir = tmp_path / "dist"
    run_id = "run-20261101T160345Z-icons-test"

    _setup_mock_run(output_dir, run_id, with_audio=True)
    build_public_portal(output_dir=output_dir, dist_dir=dist_dir)

    index_html = (dist_dir / "index.html").read_text(encoding="utf-8")
    css_content = (dist_dir / "assets" / "style.css").read_text(encoding="utf-8")
    art_html = (dist_dir / "articles" / f"{ID_1}.html").read_text(encoding="utf-8")

    # 1. Stylesheet link in HTML
    assert MATERIAL_SYMBOLS_STYLESHEET in index_html
    assert MATERIAL_SYMBOLS_STYLESHEET in art_html

    # 2. Strict 24x24 px rule in CSS
    expected_rule_parts = [
        ".material-symbols-outlined",
        "font-size: 24px;",
        "width: 24px;",
        "height: 24px;",
    ]
    for part in expected_rule_parts:
        assert part in css_content, f"Missing {part} in style.css"

    # 3. Google Material Symbols used across portal pages
    required_symbols = [
        "shield",
        "dark_mode",
        "light_mode",
        "timer",
        "person",
        "schedule",
        "share",
        "content_copy",
        "verified",
        "open_in_new",
        "play_circle",
        "pause_circle",
        "trending_up",
        "bug_report",
        "calendar_today",
        "search",
    ]
    combined_html = index_html + art_html
    for sym in required_symbols:
        assert sym in combined_html, f"Required Google Material Symbol '{sym}' missing from pages"
        assert f'style="font-size: 24px; width: 24px; height: 24px;"' in combined_html


def test_json_feed_strictly_absent_from_portal(tmp_path: Path) -> None:
    """AC 5: ABSOLUTELY NO JSON FEED anywhere in generated files, navigation, header, or footer."""
    output_dir = tmp_path / "output"
    dist_dir = tmp_path / "dist"
    run_id = "run-20261102T020834Z-no-json-feed"

    _setup_mock_run(output_dir, run_id)
    build_public_portal(output_dir=output_dir, dist_dir=dist_dir)

    # 1. feed.json file must NOT exist on disk
    assert not (dist_dir / "feed.json").exists()

    # 2. No links or references to feed.json in any HTML or JS file
    for html_file in dist_dir.rglob("*.html"):
        text = html_file.read_text(encoding="utf-8")
        assert "feed.json" not in text, f"Found feed.json reference in {html_file.name}"
        assert 'title="JSON Feed"' not in text
        assert ">JSON Feed<" not in text
        assert ">Feed<" not in text

    js_text = (dist_dir / "assets" / "app.js").read_text(encoding="utf-8")
    assert "feed.json" not in js_text


def test_theme_toggle_js_and_css_variables(tmp_path: Path) -> None:
    """AC 6: Theme toggle JavaScript and CSS variables are present in generated assets."""
    output_dir = tmp_path / "output"
    dist_dir = tmp_path / "dist"
    run_id = "run-20261031T033708Z-d0db13d2"

    _setup_mock_run(output_dir, run_id)
    build_public_portal(output_dir=output_dir, dist_dir=dist_dir)

    # 1. CSS verification
    css_content = (dist_dir / "assets" / "style.css").read_text(encoding="utf-8")
    for var in [
        "--bg-primary",
        "--bg-card",
        "--text-primary",
        "--accent-cyber",
        "--border-color",
    ]:
        assert var in css_content, f"CSS missing required variable: {var}"

    assert 'data-theme="dark"' in css_content
    assert 'data-theme="light"' in css_content
    assert "waveAnim" in css_content
    assert "reading-progress-bar" in css_content

    # 2. JS verification
    js_content = (dist_dir / "assets" / "app.js").read_text(encoding="utf-8")
    assert "localStorage" in js_content
    assert "cyberpulse-theme" in js_content
    assert "theme-toggle" in js_content
    assert "playbackRate" in js_content
    assert "search-input" in js_content
    assert "reading-progress" in js_content
    assert "copyCurrentUrl" in js_content


def test_podcast_audio_handling_when_present_vs_absent(tmp_path: Path) -> None:
    """AC 7: Podcast audio copying when file exists vs. clean degradation when missing."""
    output_dir = tmp_path / "output"
    dist_dir = tmp_path / "dist"

    run_audio = "run-20261102T020834Z-with-audio"
    run_no_audio = "run-20261101T160345Z-no-audio"

    _setup_mock_run(output_dir, run_audio, with_audio=True)
    _setup_mock_run(output_dir, run_no_audio, with_audio=False)

    # Test with audio featured
    res_audio = build_public_portal(output_dir=output_dir, dist_dir=dist_dir, featured_run_id=run_audio)
    assert res_audio.has_audio is True
    assert (dist_dir / "media" / f"{run_audio}.mp3").is_file()
    assert f"media/{run_audio}.mp3" in res_audio.files_created

    index_html_audio = (dist_dir / "index.html").read_text(encoding="utf-8")
    assert f"src=\"media/{run_audio}.mp3\"" in index_html_audio
    assert "podcast-play-btn" in index_html_audio

    # Test without audio featured
    dist_dir_no_audio = tmp_path / "dist_no_audio"
    res_no_audio = build_public_portal(output_dir=output_dir, dist_dir=dist_dir_no_audio, featured_run_id=run_no_audio)
    assert res_no_audio.has_audio is False

    index_html_no_audio = (dist_dir_no_audio / "index.html").read_text(encoding="utf-8")
    assert "định dạng văn bản đọc" in index_html_no_audio
    assert not (dist_dir_no_audio / "media" / f"{run_no_audio}.mp3").exists()


def test_empty_or_missing_editorial_directory_handled_gracefully(tmp_path: Path) -> None:
    """AC 8: Handles empty or missing editorial directories gracefully without crashing."""
    output_dir_empty = tmp_path / "empty_output"
    output_dir_empty.mkdir()
    dist_dir_1 = tmp_path / "dist_empty"

    res_1 = build_public_portal(output_dir=output_dir_empty, dist_dir=dist_dir_1)
    assert res_1.editions_count == 0
    assert res_1.featured_run_id == ""
    assert res_1.has_audio is False
    assert (dist_dir_1 / "index.html").is_file()
    assert (dist_dir_1 / "archive.html").is_file()
    assert not (dist_dir_1 / "feed.json").exists()

    # Missing directory completely
    output_dir_missing = tmp_path / "does_not_exist"
    dist_dir_2 = tmp_path / "dist_missing"

    res_2 = build_public_portal(output_dir=output_dir_missing, dist_dir=dist_dir_2)
    assert res_2.editions_count == 0
    assert (dist_dir_2 / "index.html").is_file()


def test_featured_run_id_selection(tmp_path: Path) -> None:
    """Test specifying featured_run_id features the designated edition."""
    output_dir = tmp_path / "output"
    dist_dir = tmp_path / "dist"

    run_old = "run-20261020T100000Z-old00001"
    run_new = "run-20261102T100000Z-new00002"

    _setup_mock_run(output_dir, run_old, title_suffix="Old Edition")
    _setup_mock_run(output_dir, run_new, title_suffix="New Edition")

    # Default: newest should be featured
    res_default = build_public_portal(output_dir=output_dir, dist_dir=dist_dir)
    assert res_default.featured_run_id == run_new

    # Explicitly feature older run
    dist_dir_custom = tmp_path / "dist_custom"
    res_custom = build_public_portal(
        output_dir=output_dir,
        dist_dir=dist_dir_custom,
        featured_run_id=run_old,
    )
    assert res_custom.featured_run_id == run_old
    index_html = (dist_dir_custom / "index.html").read_text(encoding="utf-8")
    assert "Old Edition" in index_html


def test_threat_badge_detection_vendor_tags_and_cves() -> None:
    """Test threat level badge detection, software vendor entity extraction, and CVE extraction."""
    # Critical CVSS 10
    b1 = detect_threat_badge("Lỗ hổng nghiêm trọng CVSS 10.0 trong LXD và MicroCloud")
    assert b1.label == "[Khẩn cấp]"
    assert b1.css_class == "badge-critical"

    # High / exploit warning
    b2 = detect_threat_badge("Cảnh báo chuỗi lỗ hổng Ivanti Connect Secure bị kẻ tấn công khai thác")
    assert b2.label == "[Cảnh báo cao]"
    assert b2.css_class == "badge-warning"

    # Patch / update
    b3 = detect_threat_badge("Phát hành bản vá bảo mật khẩn cấp cho trình duyệt Chrome")
    assert b3.label == "[Bản vá]"
    assert b3.css_class == "badge-patch"

    # Vendor tags extraction
    tags = extract_vendor_tags("Phát hiện lỗ hổng trên VMware, Palo Alto Networks và Cisco router", topics=["linux"])
    assert "VMware" in tags
    assert "Palo Alto" in tags
    assert "Cisco" in tags
    assert "Linux" in tags

    # CVE extraction
    cves = extract_cves("Phát hiện CVE-2026-1234 và cve-2026-56789 trong hệ thống")
    assert "CVE-2026-1234" in cves
    assert "CVE-2026-56789" in cves

    # Reading time calculation
    rt = calculate_reading_time("word " * 500)
    assert rt >= 2


def test_zero_ai_emojis_in_generated_files(tmp_path: Path) -> None:
    """CRITICAL DESIGN RULE: Absolutely NO generic AI emojis in generated files."""
    output_dir = tmp_path / "output"
    dist_dir = tmp_path / "dist"
    run_id = "run-20261102T020834Z-emoji-check"

    _setup_mock_run(output_dir, run_id, with_audio=True)
    build_public_portal(output_dir=output_dir, dist_dir=dist_dir)

    forbidden_emojis = ["🎙", "🎧", "🟢", "🟡", "🔴", "⚠️", "✅", "📅", "🎉", "📦", "🚀"]

    for path in dist_dir.rglob("*"):
        if path.is_file() and path.suffix in [".html", ".css", ".js"]:
            content = path.read_text(encoding="utf-8")
            for emoji in forbidden_emojis:
                assert emoji not in content, f"Forbidden emoji '{emoji}' found in {path.name}"
            # Also check for high surrogate emoji block
            emoji_matches = re.findall(r"[\U00010000-\U0010ffff]", content)
            assert not emoji_matches, f"Unicode emojis {emoji_matches} found in {path.name}"


def test_interactive_filter_taskbar_on_homepage_and_edition(tmp_path: Path) -> None:
    """Task T9.3: Filter taskbar replaces broken anchor links with live category count pills."""
    output_dir = tmp_path / "output"
    dist_dir = tmp_path / "dist"
    run_id = "run-20261102T020834Z-filter-test"

    _setup_mock_run(output_dir, run_id, with_audio=True)
    build_public_portal(output_dir=output_dir, dist_dir=dist_dir)

    index_html = (dist_dir / "index.html").read_text(encoding="utf-8")
    ed_html = (dist_dir / "editions" / f"{run_id}.html").read_text(encoding="utf-8")

    for html_doc in [index_html, ed_html]:
        # 1. Filter taskbar and pills presence
        assert "filter-taskbar" in html_doc
        assert "filter-pills" in html_doc
        assert 'data-filter="all"' in html_doc
        assert 'data-filter="critical"' in html_doc
        assert 'data-filter="warning"' in html_doc
        assert 'data-filter="patch"' in html_doc

        # 2. Live counts rendered
        assert "Tất cả (" in html_doc
        assert "Khẩn cấp (" in html_doc
        assert "Cảnh báo cao (" in html_doc
        assert "Bản vá (" in html_doc

        # 3. Status dots
        assert "status-dot dot-critical" in html_doc
        assert "status-dot dot-warning" in html_doc
        assert "status-dot dot-patch" in html_doc

        # 4. Podcast quick button
        assert "podcast-quick-btn" in html_doc
        assert 'href="#podcast-radio-box"' in html_doc
        assert "Nghe Podcast số này" in html_doc

        # 5. Elimination of broken static category anchors
        assert 'href="index.html#vulnerabilities"' not in html_doc
        assert 'href="index.html#malware"' not in html_doc
        assert 'href="index.html#patches"' not in html_doc
        assert 'href="../index.html#vulnerabilities"' not in html_doc
        assert 'href="../index.html#malware"' not in html_doc
        assert 'href="../index.html#patches"' not in html_doc

        # 6. Cards have threat-card class and data-severity attribute
        assert "threat-card" in html_doc
        assert 'data-severity="' in html_doc


def test_horizontal_daily_edition_strip(tmp_path: Path) -> None:
    """Task T9.3: Horizontal Daily Edition Strip rendered below main header with 1-click date switching."""
    output_dir = tmp_path / "output"
    dist_dir = tmp_path / "dist"

    run_1 = "run-20261030T100000Z-edition1"
    run_2 = "run-20261101T100000Z-edition2"
    run_3 = "run-20261102T100000Z-edition3"

    _setup_mock_run(output_dir, run_1)
    _setup_mock_run(output_dir, run_2)
    _setup_mock_run(output_dir, run_3)

    build_public_portal(output_dir=output_dir, dist_dir=dist_dir)

    index_html = (dist_dir / "index.html").read_text(encoding="utf-8")
    archive_html = (dist_dir / "archive.html").read_text(encoding="utf-8")
    ed3_html = (dist_dir / "editions" / f"{run_3}.html").read_text(encoding="utf-8")

    # 1. Strip container and Material Symbol
    for html_doc in [index_html, archive_html, ed3_html]:
        assert "edition-strip-wrap" in html_doc
        assert "edition-strip-inner" in html_doc
        assert "strip-label" in html_doc
        assert "calendar_month" in html_doc
        assert "Chọn ngày xem tin:" in html_doc
        assert "strip-dates" in html_doc
        assert "strip-date-all" in html_doc
        assert "Tất cả các ngày &rarr;" in html_doc

    # 2. Chronological date pills present
    assert "02/11/2026 (Mới nhất)" in index_html
    assert "01/11/2026" in index_html
    assert "30/10/2026" in index_html

    # 3. Active highlight on edition page
    assert 'class="strip-date-pill active"' in ed3_html
    assert 'aria-current="page"' in ed3_html


def test_article_reading_utility_bar_and_views_counter(tmp_path: Path) -> None:
    """Task T9.3: Article reading utility bar, font resizer, and visibility view counters."""
    output_dir = tmp_path / "output"
    dist_dir = tmp_path / "dist"
    run_id = "run-20261102T020834Z-utility-test"

    _setup_mock_run(output_dir, run_id, with_audio=True)
    build_public_portal(output_dir=output_dir, dist_dir=dist_dir)

    art_html = (dist_dir / "articles" / f"{ID_1}.html").read_text(encoding="utf-8")
    index_html = (dist_dir / "index.html").read_text(encoding="utf-8")

    # 1. Article utility bar on article page
    assert "article-utility-bar" in art_html
    assert "back-edition-btn" in art_html
    assert f"editions/{run_id}.html" in art_html
    assert "Về bản tin ngày" in art_html

    # 2. Font resizer buttons A- and A+
    assert "font-resizer" in art_html
    assert 'id="font-decrease"' in art_html
    assert 'id="font-increase"' in art_html
    assert ">A-<" in art_html
    assert ">A+<" in art_html

    # 3. View counter on article page (utility bar & meta bar)
    assert 'id="article-views-count"' in art_html
    assert 'id="article-views"' in art_html
    assert "lượt xem" in art_html
    assert "visibility" in art_html

    # 4. Total edition views on homepage hero card
    assert 'id="edition-views-count"' in index_html
    assert "visibility" in index_html


def test_article_continuous_reading_pagination(tmp_path: Path) -> None:
    """Task T9.3: Two-column navigation cards for continuous reading across articles in the same edition."""
    output_dir = tmp_path / "output"
    dist_dir = tmp_path / "dist"
    run_id = "run-20261102T020834Z-pagination-test"

    _setup_mock_run(output_dir, run_id)
    build_public_portal(output_dir=output_dir, dist_dir=dist_dir)

    art1_html = (dist_dir / "articles" / f"{ID_1}.html").read_text(encoding="utf-8")
    art2_html = (dist_dir / "articles" / f"{ID_2}.html").read_text(encoding="utf-8")

    # 1. Article 1 is first article -> has next link pointing to Article 2
    assert "article-pagination" in art1_html
    assert f'href="{ID_2}.html"' in art1_html
    assert 'rel="next"' in art1_html
    assert "Bài kế tiếp" in art1_html
    assert "arrow_forward" in art1_html

    # 2. Article 2 is second article -> has prev link pointing to Article 1
    assert "article-pagination" in art2_html
    assert f'href="{ID_1}.html"' in art2_html
    assert 'rel="prev"' in art2_html
    assert "Bài trước" in art2_html
    assert "arrow_back" in art2_html


def test_executive_tldr_box(tmp_path: Path) -> None:
    """Task T9.3: Executive TL;DR Box below lead paragraph with bolt icon and 3 takeaways."""
    output_dir = tmp_path / "output"
    dist_dir = tmp_path / "dist"
    run_id = "run-20261102T020834Z-tldr-test"

    _setup_mock_run(output_dir, run_id)
    build_public_portal(output_dir=output_dir, dist_dir=dist_dir)

    art_html = (dist_dir / "articles" / f"{ID_1}.html").read_text(encoding="utf-8")

    # 1. TL;DR box container and bolt icon
    assert "tldr-box" in art_html
    assert "tldr-header" in art_html
    assert "bolt" in art_html
    assert "Tóm tắt nhanh (TL;DR)" in art_html

    # 2. Exactly 3 list items
    assert "tldr-list" in art_html
    tldr_matches = re.findall(r"<ul class=\"tldr-list\">(.*?)</ul>", art_html, re.DOTALL)
    assert len(tldr_matches) == 1
    items = re.findall(r"<li>(.*?)</li>", tldr_matches[0])
    assert len(items) == 3
    for it in items:
        assert len(it.strip()) > 10

    # 3. Placement: after article-lead and before article-body-text
    lead_idx = art_html.find("article-lead")
    tldr_idx = art_html.find("tldr-box")
    body_idx = art_html.find("article-body-text")
    assert lead_idx < tldr_idx < body_idx


def test_all_new_material_symbols_strictly_24px(tmp_path: Path) -> None:
    """Task T9.3: All newly added icons use Google Material Symbols sized strictly at 24x24 px."""
    output_dir = tmp_path / "output"
    dist_dir = tmp_path / "dist"
    run_id = "run-20261102T020834Z-symbols-test"

    _setup_mock_run(output_dir, run_id, with_audio=True)
    build_public_portal(output_dir=output_dir, dist_dir=dist_dir)

    index_html = (dist_dir / "index.html").read_text(encoding="utf-8")
    art_html = (dist_dir / "articles" / f"{ID_1}.html").read_text(encoding="utf-8")
    combined = index_html + art_html

    new_symbols = [
        "visibility",
        "arrow_back",
        "arrow_forward",
        "bolt",
        "calendar_month",
        "podcasts",
    ]

    for sym in new_symbols:
        assert sym in combined, f"Google Material Symbol '{sym}' missing"
        # Verify 24x24 inline styling
        pattern = rf'<span class="material-symbols-outlined[^"]*" style="font-size: 24px; width: 24px; height: 24px;" aria-hidden="true">.*?{sym}</span>'
        assert re.search(pattern, combined, re.DOTALL), f"Symbol '{sym}' not strictly 24x24 px"


def test_vanilla_js_ux_features(tmp_path: Path) -> None:
    """Task T9.3: Vanilla JS contains live pill filtering, localStorage view counter, and font resizer."""
    output_dir = tmp_path / "output"
    dist_dir = tmp_path / "dist"
    run_id = "run-20261102T020834Z-js-test"

    _setup_mock_run(output_dir, run_id)
    build_public_portal(output_dir=output_dir, dist_dir=dist_dir)

    js_code = (dist_dir / "assets" / "app.js").read_text(encoding="utf-8")

    # 1. Live Filter Pills
    assert "filter-pill" in js_code
    assert "applyCardFilters" in js_code
    assert "data-severity" in js_code

    # 2. LocalStorage View Counter
    assert "cp_views_" in js_code
    assert "getBaselineViews" in js_code
    assert "article-views-count" in js_code
    assert "edition-views-count" in js_code

    # 3. Font Resizer
    assert "font-decrease" in js_code
    assert "font-increase" in js_code
    assert "article-body-text" in js_code
    assert "cp_font_size" in js_code

    # 4. Podcast Quick Button
    assert "podcast-quick-btn" in js_code
    assert "podcast-radio-box" in js_code
