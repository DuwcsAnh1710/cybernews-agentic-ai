"""Offline unit tests for normalization, filtering, topic matching, and deduplication."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import pytest

from cyberpulse.config import ConfigError, NormalizeConfig, Settings, load_settings
from cyberpulse.fetcher import RawItem
from cyberpulse.models import make_article_id
from cyberpulse.normalize import (
    DropReason,
    canonicalize_url,
    clean_text,
    match_topics,
    normalize_items,
    normalize_title_key,
)
from cyberpulse.storage import connect, init_db, upsert_article

FIXED_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def default_settings() -> Settings:
    return Settings(
        project_name="CyberPulse Test",
        topics=["cybersecurity", "ransomware", "vulnerability", "AI security"],
        items_per_run=5,
        lookback_hours=48,
        normalize=NormalizeConfig(
            title_similarity=0.9,
            summary_max_chars=1000,
            future_tolerance_minutes=60,
        ),
    )


# 1. utm_source + fbclid removed; other params kept and sorted
def test_canonicalize_url_tracking_params_removed_and_sorted() -> None:
    url = "https://example.com/sample/item?b=2&utm_source=feed&a=1&fbclid=xyz&utm_campaign=winter"
    canonical = canonicalize_url(url)
    assert canonical == "https://example.com/sample/item?a=1&b=2"


# 2. https://WWW.Example.com:443/a/b/?x=1#frag -> https://example.com/a/b?x=1
def test_canonicalize_url_www_default_port_fragment_and_trailing_slash() -> None:
    url = "https://WWW.Example.com:443/a/b/?x=1#frag"
    canonical = canonicalize_url(url)
    assert canonical == "https://example.com/a/b?x=1"


# 3. http://example.com/ keeps path "/"; scheme not changed to https
def test_canonicalize_url_preserves_root_and_http_scheme() -> None:
    url = "http://example.com/"
    canonical = canonicalize_url(url)
    assert canonical == "http://example.com/"


# 4. ftp://x, mailto:a@b, not a url, https:///path -> None
@pytest.mark.parametrize(
    "invalid_url",
    [
        "ftp://x",
        "mailto:a@b",
        "not a url",
        "https:///path",
        "",
        "   ",
    ],
)
def test_canonicalize_url_invalid(invalid_url: str) -> None:
    assert canonicalize_url(invalid_url) is None


# 5. Same article with and without utm params -> same canonical URL and same id
def test_canonicalize_url_utm_same_canonical_and_id() -> None:
    url1 = "https://example.com/sample/advisory-101"
    url2 = "https://example.com/sample/advisory-101?utm_medium=rss&utm_source=partner&ref=home"
    c1 = canonicalize_url(url1)
    c2 = canonicalize_url(url2)
    assert c1 is not None and c2 is not None
    assert c1 == c2
    assert make_article_id(c1) == make_article_id(c2)


# 6. Leading/trailing whitespace in link handled
def test_canonicalize_url_whitespace_trimmed() -> None:
    raw = "   https://www.example.org/sample/report/  \n\t "
    canonical = canonicalize_url(raw)
    assert canonical == "https://example.org/sample/report"


# 7. clean_text: <p>Hello&nbsp;<b>world</b></p>\n\n  again -> Hello world again; truncation ends with "…" and is <= max_chars + 1
def test_clean_text() -> None:
    raw_html = "<p>Hello&nbsp;<b>world</b></p>\n\n  again"
    assert clean_text(raw_html, 300) == "Hello world again"

    long_text = "SAMPLE — Multi-state investigation targets major international ransomware syndicate operations"
    truncated = clean_text(long_text, 25)
    assert truncated.endswith("…")
    assert len(truncated) <= 26
    assert truncated == "SAMPLE — Multi-state…"

    # Single word longer than max_chars
    single_word = "Supercalifragilisticexpialidocious"
    word_truncated = clean_text(single_word, 10)
    assert word_truncated.endswith("…")
    assert len(word_truncated) == 11
    assert word_truncated == "Supercalif…"


# 8. match_topics: "ransomware" matches "New Ransomware gang"; "AI security" does not match "email security"; no topics -> [] and item still kept
def test_match_topics(default_settings: Settings) -> None:
    topics = default_settings.topics
    assert match_topics("SAMPLE — New Ransomware gang", "Summary details", topics) == ["ransomware"]
    assert match_topics("SAMPLE — Email security update", "Summary details", topics) == []
    assert match_topics("SAMPLE — AI security guidelines", "Summary details", topics) == ["AI security"]

    # Item with no matching topics is still kept in normalize_items
    raw = RawItem(
        source_name="Sample Source A",
        title="SAMPLE — General Tech News",
        link="https://example.com/sample/general",
        published_at=FIXED_NOW,
        fetched_at=FIXED_NOW,
    )
    report = normalize_items([raw], default_settings, now=FIXED_NOW)
    assert len(report.articles) == 1
    assert report.articles[0].topics_matched == []


# 9. One test per DropReason (8 tests or one parametrized test)
@pytest.mark.parametrize(
    ("raw_item", "expected_reason"),
    [
        (
            RawItem(
                source_name="Sample Source A",
                title="SAMPLE — Missing Link",
                link=None,
                published_at=FIXED_NOW,
                fetched_at=FIXED_NOW,
            ),
            DropReason.missing_link,
        ),
        (
            RawItem(
                source_name="Sample Source A",
                title="SAMPLE — Invalid URL",
                link="ftp://sample.com/invalid",
                published_at=FIXED_NOW,
                fetched_at=FIXED_NOW,
            ),
            DropReason.invalid_url,
        ),
        (
            RawItem(
                source_name="Sample Source A",
                title="   ",
                link="https://example.com/sample/no-title",
                published_at=FIXED_NOW,
                fetched_at=FIXED_NOW,
            ),
            DropReason.missing_title,
        ),
        (
            RawItem(
                source_name="Sample Source A",
                title="SAMPLE — Missing Date",
                link="https://example.com/sample/no-date",
                published_at=None,
                fetched_at=FIXED_NOW,
            ),
            DropReason.missing_date,
        ),
        (
            RawItem(
                source_name="Sample Source A",
                title="SAMPLE — Too Old Item",
                link="https://example.com/sample/old",
                published_at=FIXED_NOW - timedelta(hours=49),
                fetched_at=FIXED_NOW,
            ),
            DropReason.too_old,
        ),
        (
            RawItem(
                source_name="Sample Source A",
                title="SAMPLE — Future Date Item",
                link="https://example.com/sample/future",
                published_at=FIXED_NOW + timedelta(minutes=65),
                fetched_at=FIXED_NOW,
            ),
            DropReason.future_date,
        ),
    ],
)
def test_drop_reasons_single_item(
    default_settings: Settings,
    raw_item: RawItem,
    expected_reason: DropReason,
) -> None:
    report = normalize_items([raw_item], default_settings, now=FIXED_NOW)
    assert len(report.articles) == 0
    assert len(report.dropped) == 1
    assert report.dropped[0].reason == expected_reason


# 10. Exact duplicate across two sources -> earliest kept, other dropped with duplicate_of
def test_exact_duplicate_earliest_kept(default_settings: Settings) -> None:
    earlier_item = RawItem(
        source_name="Sample Source B",
        title="SAMPLE — Critical Flaw Patch Released",
        link="https://example.com/sample/flaw-patch?utm_source=b",
        published_at=FIXED_NOW - timedelta(hours=4),
        fetched_at=FIXED_NOW,
    )
    later_item = RawItem(
        source_name="Sample Source A",
        title="SAMPLE — Critical Flaw Patch Released",
        link="https://example.com/sample/flaw-patch?utm_source=a",
        published_at=FIXED_NOW - timedelta(hours=2),
        fetched_at=FIXED_NOW,
    )

    # Input in order [later, earlier] to verify sorting picks earlier
    report = normalize_items([later_item, earlier_item], default_settings, now=FIXED_NOW)
    assert len(report.articles) == 1
    assert len(report.dropped) == 1
    kept = report.articles[0]
    dropped = report.dropped[0]

    assert kept.source_name == "Sample Source B"
    assert kept.published_at == FIXED_NOW - timedelta(hours=4)
    assert dropped.reason == DropReason.duplicate_url
    assert dropped.source_name == "Sample Source A"
    assert dropped.duplicate_of == kept.id


# 11. Near-duplicate titles (ratio >= 0.9) -> dropped as duplicate_title; clearly different titles both kept
def test_near_duplicate_titles(default_settings: Settings) -> None:
    item_original = RawItem(
        source_name="Sample Source A",
        title="SAMPLE — Critical Zero-Day Vulnerability in Linux Kernel Discovered",
        link="https://example.com/sample/linux-flaw-1",
        published_at=FIXED_NOW - timedelta(hours=6),
        fetched_at=FIXED_NOW,
    )
    item_near_dupe = RawItem(
        source_name="Sample Source B",
        title="SAMPLE — Critical Zero Day Vulnerability in Linux Kernel Discovered",
        link="https://example.org/sample/linux-flaw-2",
        published_at=FIXED_NOW - timedelta(hours=3),
        fetched_at=FIXED_NOW,
    )
    item_different = RawItem(
        source_name="Sample Source A",
        title="SAMPLE — Unrelated Ransomware Attack on Healthcare Hospital",
        link="https://example.com/sample/hospital-ransom",
        published_at=FIXED_NOW - timedelta(hours=5),
        fetched_at=FIXED_NOW,
    )

    report = normalize_items([item_original, item_near_dupe, item_different], default_settings, now=FIXED_NOW)
    assert len(report.articles) == 2
    assert len(report.dropped) == 1

    dropped = report.dropped[0]
    assert dropped.reason == DropReason.duplicate_title
    assert dropped.duplicate_of == make_article_id("https://example.com/sample/linux-flaw-1")
    assert float(dropped.detail) >= 0.90


# 12. Count invariant on a mixed batch of >= 10 items
def test_count_invariant(default_settings: Settings) -> None:
    items: list[RawItem] = [
        # 1. Valid item 1
        RawItem(
            source_name="Sample Source A",
            title="SAMPLE — Article Alpha",
            link="https://example.com/sample/alpha",
            published_at=FIXED_NOW - timedelta(hours=1),
            fetched_at=FIXED_NOW,
        ),
        # 2. Valid item 2
        RawItem(
            source_name="Sample Source B",
            title="SAMPLE — Article Beta",
            link="https://example.com/sample/beta",
            published_at=FIXED_NOW - timedelta(hours=2),
            fetched_at=FIXED_NOW,
        ),
        # 3. Missing link
        RawItem(
            source_name="Sample Source A",
            title="SAMPLE — Missing Link",
            link=None,
            published_at=FIXED_NOW - timedelta(hours=3),
            fetched_at=FIXED_NOW,
        ),
        # 4. Invalid url
        RawItem(
            source_name="Sample Source A",
            title="SAMPLE — Bad URL",
            link="ftp://sample.com/invalid",
            published_at=FIXED_NOW - timedelta(hours=4),
            fetched_at=FIXED_NOW,
        ),
        # 5. Missing title
        RawItem(
            source_name="Sample Source B",
            title="",
            link="https://example.com/sample/no-title",
            published_at=FIXED_NOW - timedelta(hours=5),
            fetched_at=FIXED_NOW,
        ),
        # 6. Missing date
        RawItem(
            source_name="Sample Source A",
            title="SAMPLE — No Date",
            link="https://example.com/sample/no-date",
            published_at=None,
            fetched_at=FIXED_NOW,
        ),
        # 7. Too old
        RawItem(
            source_name="Sample Source B",
            title="SAMPLE — Too Old",
            link="https://example.com/sample/too-old",
            published_at=FIXED_NOW - timedelta(hours=50),
            fetched_at=FIXED_NOW,
        ),
        # 8. Future date
        RawItem(
            source_name="Sample Source A",
            title="SAMPLE — Future Date",
            link="https://example.com/sample/future-item",
            published_at=FIXED_NOW + timedelta(minutes=90),
            fetched_at=FIXED_NOW,
        ),
        # 9. Exact duplicate of item 1 (later timestamp)
        RawItem(
            source_name="Sample Source B",
            title="SAMPLE — Article Alpha Copied",
            link="https://example.com/sample/alpha?utm_source=twitter",
            published_at=FIXED_NOW - timedelta(minutes=30),
            fetched_at=FIXED_NOW,
        ),
        # 10. Near duplicate of item 2 (later timestamp)
        RawItem(
            source_name="Sample Source A",
            title="SAMPLE — Article Beta!",
            link="https://example.com/sample/beta-diff-url",
            published_at=FIXED_NOW - timedelta(minutes=45),
            fetched_at=FIXED_NOW,
        ),
        # 11. Valid item 3
        RawItem(
            source_name="Sample Source B",
            title="SAMPLE — Article Gamma",
            link="https://example.com/sample/gamma",
            published_at=FIXED_NOW - timedelta(hours=6),
            fetched_at=FIXED_NOW,
        ),
    ]

    report = normalize_items(items, default_settings, now=FIXED_NOW)
    assert report.input_count == len(items)
    assert report.input_count == len(report.articles) + len(report.dropped)
    assert len(report.articles) == 3
    assert len(report.dropped) == 8


# 13. Kept articles sorted by published_at descending; ids match make_article_id(url)
def test_kept_articles_sorting_and_ids(default_settings: Settings) -> None:
    items = [
        RawItem(
            source_name="Sample Source A",
            title="SAMPLE — Zero-Day Exploit Detected in Browser",
            link="https://example.com/sample/browser-exploit",
            published_at=FIXED_NOW - timedelta(hours=10),
            fetched_at=FIXED_NOW,
        ),
        RawItem(
            source_name="Sample Source A",
            title="SAMPLE — Ransomware Campaign Targets Healthcare Sector",
            link="https://example.com/sample/ransomware-health",
            published_at=FIXED_NOW - timedelta(hours=2),
            fetched_at=FIXED_NOW,
        ),
        RawItem(
            source_name="Sample Source A",
            title="SAMPLE — Cloud Storage Misconfiguration Exposes Sensitive Records",
            link="https://example.com/sample/cloud-misconfig",
            published_at=FIXED_NOW - timedelta(hours=5),
            fetched_at=FIXED_NOW,
        ),
    ]

    report = normalize_items(items, default_settings, now=FIXED_NOW)
    assert len(report.articles) == 3

    # Must be sorted descending by published_at
    dates = [a.published_at for a in report.articles]
    assert dates == sorted(dates, reverse=True)

    # Ids must match make_article_id(url)
    for article in report.articles:
        assert article.id == make_article_id(article.url)


# 14. Upsert collect output twice into a tmp DB -> SELECT COUNT(*) FROM articles unchanged
def test_upsert_collect_output_idempotent(tmp_path: Path, default_settings: Settings) -> None:
    db_file = tmp_path / "test_articles.db"
    conn = connect(db_file)
    init_db(conn)

    items = [
        RawItem(
            source_name="Sample Source A",
            title="SAMPLE — First Independent Threat Advisory",
            link="https://example.com/sample/persist1",
            published_at=FIXED_NOW - timedelta(hours=4),
            fetched_at=FIXED_NOW,
        ),
        RawItem(
            source_name="Sample Source B",
            title="SAMPLE — Second Unrelated Breach Notification",
            link="https://example.com/sample/persist2",
            published_at=FIXED_NOW - timedelta(hours=3),
            fetched_at=FIXED_NOW,
        ),
    ]

    report = normalize_items(items, default_settings, now=FIXED_NOW)
    assert len(report.articles) == 2

    # First upsert batch
    with conn:
        for article in report.articles:
            upsert_article(conn, article)

    count_1 = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    assert count_1 == 2

    # Second upsert batch
    with conn:
        for article in report.articles:
            upsert_article(conn, article)

    count_2 = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    assert count_2 == 2
    conn.close()


# 16. Invalid [normalize] values -> ConfigError
@pytest.mark.parametrize(
    ("toml_content", "err_keyword"),
    [
        (
            """
            project_name = "CyberPulse"
            topics = ["security"]
            [normalize]
            title_similarity = 0.4
            """,
            "title_similarity",
        ),
        (
            """
            project_name = "CyberPulse"
            topics = ["security"]
            [normalize]
            title_similarity = 1.1
            """,
            "title_similarity",
        ),
        (
            """
            project_name = "CyberPulse"
            topics = ["security"]
            [normalize]
            summary_max_chars = 99
            """,
            "summary_max_chars",
        ),
        (
            """
            project_name = "CyberPulse"
            topics = ["security"]
            [normalize]
            summary_max_chars = 5001
            """,
            "summary_max_chars",
        ),
        (
            """
            project_name = "CyberPulse"
            topics = ["security"]
            [normalize]
            future_tolerance_minutes = -1
            """,
            "future_tolerance_minutes",
        ),
        (
            """
            project_name = "CyberPulse"
            topics = ["security"]
            [normalize]
            future_tolerance_minutes = 1500
            """,
            "future_tolerance_minutes",
        ),
    ],
)
def test_invalid_normalize_config(tmp_path: Path, toml_content: str, err_keyword: str) -> None:
    cfg_file = tmp_path / "invalid_normalize.toml"
    cfg_file.write_text(toml_content, encoding="utf-8")
    with pytest.raises(ConfigError) as exc_info:
        load_settings(cfg_file)
    assert err_keyword in str(exc_info.value)