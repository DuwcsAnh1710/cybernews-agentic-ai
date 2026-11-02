"""Hermetic unit tests for CyberPulse AI multi-channel export engine (src/cyberpulse/export.py)."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest

from cyberpulse.export import ExportResult, create_export_zip, export_editorial_bundle
from cyberpulse.models import (
    Article,
    Caption,
    CitedText,
    Digest,
    DigestSection,
    EditorialPackage,
    PodcastScript,
    PodcastSegment,
    ResearchItem,
    ResearchResult,
    VideoScene,
    VideoScript,
)
from tests.test_ui_data import make_sample_editorial, make_sample_research


def test_export_editorial_bundle_complete(tmp_path: Path) -> None:
    """Test generating complete editorial bundle with all required files."""
    run_id = "test-export-run-001"
    output_dir = tmp_path / "output"
    export_root = tmp_path / "export"

    r_dir = output_dir / "research"
    r_dir.mkdir(parents=True)
    res = make_sample_research(run_id)
    (r_dir / f"{run_id}.json").write_text(res.model_dump_json(), encoding="utf-8")

    pkg = make_sample_editorial(run_id)

    result = export_editorial_bundle(
        package=pkg,
        run_id=run_id,
        output_dir=output_dir,
        export_root=export_root,
    )

    assert isinstance(result, ExportResult)
    assert result.run_id == run_id
    assert result.export_dir == export_root / run_id
    assert result.article_count == 2
    assert not result.has_podcast_audio
    assert not result.has_podcast_srt

    expected_files = {
        "digest.md",
        "digest.html",
        "social_facebook.txt",
        "social_youtube.txt",
        "social_tiktok.txt",
        "huong_dan_dang_bai.md",
        "manifest.json",
    }
    assert expected_files.issubset(set(result.files_created))

    for fname in expected_files:
        f_path = result.export_dir / fname
        assert f_path.exists()
        assert f_path.stat().st_size > 0


def test_digest_html_structure_and_inline_css(tmp_path: Path) -> None:
    """Test HTML digest renders valid HTML5 structure, viewport meta, and self-contained inline CSS."""
    run_id = "test-export-run-html"
    output_dir = tmp_path / "output"
    export_root = tmp_path / "export"
    output_dir.mkdir(parents=True)

    pkg = make_sample_editorial(run_id)
    result = export_editorial_bundle(
        package=pkg,
        run_id=run_id,
        output_dir=output_dir,
        export_root=export_root,
    )

    html_file = result.export_dir / "digest.html"
    content = html_file.read_text(encoding="utf-8")

    assert "<!DOCTYPE html>" in content
    assert '<html lang="vi">' in content
    assert '<meta name="viewport" content="width=device-width, initial-scale=1.0">' in content
    assert "<style>" in content
    assert "</style>" in content
    # Verify no external CDN or font imports
    assert "cdn." not in content
    assert "googleapis.com" not in content
    assert "cloudflare.com" not in content
    # Verify content elements
    assert pkg.digest.title in content
    assert pkg.digest.intro in content
    for sec in pkg.digest.sections:
        assert sec.heading in content


def test_social_files_content_and_formatting(tmp_path: Path) -> None:
    """Test social text files for Facebook, YouTube, TikTok contain required fields."""
    run_id = "test-export-social"
    output_dir = tmp_path / "output"
    export_root = tmp_path / "export"
    output_dir.mkdir(parents=True)

    pkg = make_sample_editorial(run_id)
    result = export_editorial_bundle(
        package=pkg,
        run_id=run_id,
        output_dir=output_dir,
        export_root=export_root,
    )

    # 1. Facebook
    fb_text = (result.export_dir / "social_facebook.txt").read_text(encoding="utf-8")
    assert pkg.digest.title in fb_text
    assert "TỔNG HỢP ĐIỂM TIN NỔI BẬT:" in fb_text
    assert "#" in fb_text

    # 2. YouTube
    yt_text = (result.export_dir / "social_youtube.txt").read_text(encoding="utf-8")
    assert pkg.video.title in yt_text
    assert "TIMESTAMPS" in yt_text
    assert "00:00 - Giới thiệu bản tin" in yt_text
    assert "NGUỒN TIN THAM KHẢO & TRÍCH DẪN:" in yt_text

    # 3. TikTok
    tt_text = (result.export_dir / "social_tiktok.txt").read_text(encoding="utf-8")
    assert pkg.video.title in tt_text
    assert "HOOK MỞ ĐẦU" in tt_text
    assert "KỊCH BẢN TỪNG CẢNH" in tt_text
    assert "KÊU GỌI HÀNH ĐỘNG" in tt_text


def test_manifest_sha256_checksums_and_articles(tmp_path: Path) -> None:
    """Test manifest.json contains valid SHA256 hashes matching actual files."""
    run_id = "test-export-manifest"
    output_dir = tmp_path / "output"
    export_root = tmp_path / "export"

    r_dir = output_dir / "research"
    r_dir.mkdir(parents=True)
    res = make_sample_research(run_id)
    (r_dir / f"{run_id}.json").write_text(res.model_dump_json(), encoding="utf-8")

    pkg = make_sample_editorial(run_id)
    result = export_editorial_bundle(
        package=pkg,
        run_id=run_id,
        output_dir=output_dir,
        export_root=export_root,
    )

    manifest_file = result.export_dir / "manifest.json"
    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))

    assert manifest_data["run_id"] == run_id
    assert manifest_data["article_count"] == 2
    assert len(manifest_data["source_articles"]) == 2
    assert "exported_at" in manifest_data

    # Check that each file recorded in manifest has a valid SHA256 matching disk
    files_list = manifest_data["files"]
    assert len(files_list) >= 6

    for finfo in files_list:
        file_path = result.export_dir / finfo["name"]
        assert file_path.exists()
        computed_sha256 = hashlib.sha256(file_path.read_bytes()).hexdigest()
        assert finfo["sha256"] == computed_sha256
        assert finfo["bytes"] == file_path.stat().st_size


def test_podcast_media_copied_when_present_vs_absent(tmp_path: Path) -> None:
    """Test podcast MP3 and SRT are copied when present, handled gracefully when absent."""
    run_id = "test-export-podcast-copy"
    output_dir = tmp_path / "output"
    export_root = tmp_path / "export"
    media_dir = output_dir / "media" / "podcast"
    media_dir.mkdir(parents=True)

    pkg = make_sample_editorial(run_id)

    # 1. When files are absent
    res_absent = export_editorial_bundle(
        package=pkg,
        run_id=run_id,
        output_dir=output_dir,
        export_root=export_root,
    )
    assert not res_absent.has_podcast_audio
    assert not res_absent.has_podcast_srt
    assert not (res_absent.export_dir / "podcast.mp3").exists()
    assert not (res_absent.export_dir / "podcast.srt").exists()

    # 2. When files are present
    dummy_mp3 = b"\xff\xf3\x44\x00" + b"\x00" * 200
    dummy_srt = "1\n00:00:00,000 --> 00:00:02,000\nChào quý thính giả.\n"

    (media_dir / f"{run_id}.mp3").write_bytes(dummy_mp3)
    (media_dir / f"{run_id}.srt").write_text(dummy_srt, encoding="utf-8")

    res_present = export_editorial_bundle(
        package=pkg,
        run_id=run_id,
        output_dir=output_dir,
        export_root=export_root,
    )
    assert res_present.has_podcast_audio
    assert res_present.has_podcast_srt
    assert (res_present.export_dir / "podcast.mp3").read_bytes() == dummy_mp3
    assert (res_present.export_dir / "podcast.srt").read_text(encoding="utf-8") == dummy_srt


def test_create_export_zip_valid_archive(tmp_path: Path) -> None:
    """Test create_export_zip creates a valid, readable ZIP archive in memory."""
    bundle_dir = tmp_path / "bundle_to_zip"
    bundle_dir.mkdir()

    (bundle_dir / "test1.txt").write_text("Hello CyberPulse", encoding="utf-8")
    (bundle_dir / "test2.json").write_text('{"key": "value"}', encoding="utf-8")

    zip_bytes = create_export_zip(bundle_dir)
    assert isinstance(zip_bytes, bytes)
    assert len(zip_bytes) > 0

    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        namelist = zf.namelist()
        assert "test1.txt" in namelist
        assert "test2.json" in namelist
        assert zf.read("test1.txt").decode("utf-8") == "Hello CyberPulse"
        assert zf.read("test2.json").decode("utf-8") == '{"key": "value"}'
