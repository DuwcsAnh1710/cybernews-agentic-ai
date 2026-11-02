"""Hermetic unit tests for CyberPulse Reader static portal generation engine."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil

import pytest

from cyberpulse.models import EditorialPackage, ResearchResult
from cyberpulse.portal import (
    PortalBuildResult,
    build_public_portal,
    detect_threat_badge,
    extract_vendor_tags,
    format_vietnam_time,
    parse_run_timestamp,
)
from tests.test_ui_data import make_sample_editorial, make_sample_research


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
    """AC 1: build_public_portal creates index.html, archive.html, editions/<run_id>.html, assets, and feed.json."""
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
    assert (dist_dir / "feed.json").is_file()
    assert (dist_dir / "assets" / "style.css").is_file()
    assert (dist_dir / "assets" / "app.js").is_file()
    assert (dist_dir / "editions" / f"{run_id}.html").is_file()

    # Check files_created list
    expected_files = {
        "index.html",
        "archive.html",
        "feed.json",
        "assets/style.css",
        "assets/app.js",
        f"editions/{run_id}.html",
    }
    assert expected_files.issubset(set(result.files_created))


def test_index_html_contains_semantic_html5_tags(tmp_path: Path) -> None:
    """AC 2: index.html contains semantic HTML5 tags: <!DOCTYPE html>, <meta name="viewport">, <header>, <article>, <footer>."""
    output_dir = tmp_path / "output"
    dist_dir = tmp_path / "dist"
    run_id = "run-20261101T160345Z-4cea6728"

    _setup_mock_run(output_dir, run_id)
    build_public_portal(output_dir=output_dir, dist_dir=dist_dir)

    index_html = (dist_dir / "index.html").read_text(encoding="utf-8")

    assert "<!DOCTYPE html>" in index_html
    assert 'name="viewport"' in index_html
    assert "<header" in index_html
    assert "</header>" in index_html
    assert "<article" in index_html
    assert "</article>" in index_html
    assert "<footer" in index_html
    assert "</footer>" in index_html
    assert "CyberPulse AI" in index_html
    assert "Điểm tin nhanh (60-Second Brief)" in index_html
    assert "Tiêu điểm an ninh mạng (Curated Threats)" in index_html


def test_theme_toggle_js_and_css_variables(tmp_path: Path) -> None:
    """AC 3: Theme toggle JavaScript and CSS variables are present in generated assets."""
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

    # Verify dark and light themes declared
    assert 'data-theme="dark"' in css_content
    assert 'data-theme="light"' in css_content

    # 2. JS verification
    js_content = (dist_dir / "assets" / "app.js").read_text(encoding="utf-8")
    assert "localStorage" in js_content
    assert "cyberpulse-theme" in js_content
    assert "theme-toggle" in js_content
    assert "playbackRate" in js_content
    assert "search-input" in js_content


def test_podcast_audio_handling_when_present_vs_absent(tmp_path: Path) -> None:
    """AC 4: Podcast audio copying when file exists vs. clean degradation when missing."""
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
    assert "audio-play-btn" in index_html_audio

    # Test without audio featured
    dist_dir_no_audio = tmp_path / "dist_no_audio"
    res_no_audio = build_public_portal(output_dir=output_dir, dist_dir=dist_dir_no_audio, featured_run_id=run_no_audio)
    assert res_no_audio.has_audio is False

    index_html_no_audio = (dist_dir_no_audio / "index.html").read_text(encoding="utf-8")
    assert "Bản tin dạng đọc" in index_html_no_audio
    assert not (dist_dir_no_audio / "media" / f"{run_no_audio}.mp3").exists()


def test_feed_json_valid_format_and_items(tmp_path: Path) -> None:
    """AC 5: feed.json complies with valid JSON Feed v1.1 and contains required items."""
    output_dir = tmp_path / "output"
    dist_dir = tmp_path / "dist"

    run_1 = "run-20261101T100000Z-aaa11111"
    run_2 = "run-20261102T100000Z-bbb22222"

    _setup_mock_run(output_dir, run_1, title_suffix="Day 1")
    _setup_mock_run(output_dir, run_2, title_suffix="Day 2")

    build_public_portal(output_dir=output_dir, dist_dir=dist_dir)

    feed_path = dist_dir / "feed.json"
    assert feed_path.is_file()

    feed_data = json.loads(feed_path.read_text(encoding="utf-8"))
    assert feed_data["version"] == "https://jsonfeed.org/version/1.1"
    assert "CyberPulse AI" in feed_data["title"]
    assert len(feed_data["items"]) == 2

    item_ids = {it["id"] for it in feed_data["items"]}
    assert item_ids == {run_1, run_2}

    for it in feed_data["items"]:
        assert "url" in it
        assert "title" in it
        assert "summary" in it
        assert "date_published" in it


def test_empty_or_missing_editorial_directory_handled_gracefully(tmp_path: Path) -> None:
    """AC 6: Handles empty or missing editorial directories gracefully without crashing."""
    output_dir_empty = tmp_path / "empty_output"
    output_dir_empty.mkdir()
    dist_dir_1 = tmp_path / "dist_empty"

    res_1 = build_public_portal(output_dir=output_dir_empty, dist_dir=dist_dir_1)
    assert res_1.editions_count == 0
    assert res_1.featured_run_id == ""
    assert res_1.has_audio is False
    assert (dist_dir_1 / "index.html").is_file()
    assert (dist_dir_1 / "archive.html").is_file()
    assert (dist_dir_1 / "feed.json").is_file()

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
    assert run_old in index_html
    assert "Old Edition" in index_html


def test_threat_badge_detection_and_vendor_tags() -> None:
    """Test threat level badge detection and software vendor entity extraction."""
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


def test_zero_ai_emojis_in_generated_files(tmp_path: Path) -> None:
    """CRITICAL DESIGN RULE: Absolutely NO generic AI emojis in generated files."""
    output_dir = tmp_path / "output"
    dist_dir = tmp_path / "dist"
    run_id = "run-20261102T020834Z-emoji-check"

    _setup_mock_run(output_dir, run_id, with_audio=True)
    build_public_portal(output_dir=output_dir, dist_dir=dist_dir)

    forbidden_emojis = ["🎙", "🎧", "🟢", "🟡", "🔴", "⚠️", "✅", "📅", "🎉", "📦", "🚀"]

    for path in dist_dir.rglob("*"):
        if path.is_file() and path.suffix in [".html", ".css", ".js", ".json"]:
            content = path.read_text(encoding="utf-8")
            for emoji in forbidden_emojis:
                assert emoji not in content, f"Forbidden emoji '{emoji}' found in {path.name}"
            # Also check for high surrogate emoji block
            emoji_matches = re.findall(r"[\U00010000-\U0010ffff]", content)
            assert not emoji_matches, f"Unicode emojis {emoji_matches} found in {path.name}"
