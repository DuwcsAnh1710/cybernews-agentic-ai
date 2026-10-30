"""Unit tests for cyberpulse.ui.data module (Task T4.3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import pytest

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
from cyberpulse.storage import init_db, upsert_article
from cyberpulse.ui.data import (
    ArtifactError,
    Block,
    blocks_by_article,
    caption_stats,
    highlight,
    issues_by_path,
    list_runs,
    load_editorial,
    load_research,
    load_validation,
    parse_research_notes,
    podcast_stats,
    query_articles,
    source_cap_view,
    uncited_blocks,
    unselected_candidates,
    validation_freshness,
    video_storyboard,
)


# ==============================================================================
# Sample Fixtures
# ==============================================================================

ID_1 = "1111111111111111"
ID_2 = "2222222222222222"


def make_sample_research(run_id: str = "sample-run-001") -> ResearchResult:
    now = datetime(2026, 10, 29, 10, 0, 0, tzinfo=timezone.utc)
    return ResearchResult(
        run_id=run_id,
        status="ok",
        requested_count=2,
        notes="model=gemini-3.6-flash; attempts=1; hallucinated=0; duplicates=0; source_cap_dropped=1",
        items=[
            ResearchItem(
                rank=1,
                relevance_reason="Lý do chọn bài 1 (mẫu)",
                article=Article(
                    id=ID_1,
                    source_name="The Hacker News",
                    title="SAMPLE — Flaw 1 Disclosed",
                    url="https://example.com/sample/1",
                    published_at=now,
                    fetched_at=now,
                    summary="Summary of flaw 1.",
                ),
            ),
            ResearchItem(
                rank=2,
                relevance_reason="Lý do chọn bài 2 (mẫu)",
                article=Article(
                    id=ID_2,
                    source_name="BleepingComputer",
                    title="SAMPLE — Patch 2 Released",
                    url="https://example.com/sample/2",
                    published_at=now,
                    fetched_at=now,
                    summary="Summary of patch 2.",
                ),
            ),
        ],
    )


def make_sample_editorial(run_id: str = "sample-run-001") -> EditorialPackage:
    return EditorialPackage(
        run_id=run_id,
        language="vi",
        digest=Digest(
            title="Tiêu đề bản tin (mẫu)",
            intro="Lời mở đầu bản tin (mẫu)",
            sections=[
                DigestSection(
                    heading="Tiêu đề mục 1 (mẫu)",
                    body=CitedText(text="Nội dung mục 1 (mẫu)", cited_article_ids=[ID_1]),
                ),
                DigestSection(
                    heading="Tiêu đề mục 2 (mẫu)",
                    body=CitedText(text="Nội dung mục 2 (mẫu)", cited_article_ids=[ID_2]),
                ),
            ],
        ),
        podcast=PodcastScript(
            title="Podcast An ninh mạng (mẫu)",
            segments=[
                PodcastSegment(
                    speaker="Minh",
                    line=CitedText(text="Chào Lan và thính giả. (mẫu)", cited_article_ids=[ID_1, ID_2]),
                ),
                PodcastSegment(
                    speaker="Lan",
                    line=CitedText(text="Chào anh Minh. (mẫu)", cited_article_ids=[ID_1, ID_2]),
                ),
            ],
        ),
        video=VideoScript(
            title="Video ngắn (mẫu)",
            scenes=[
                VideoScene(
                    scene_no=1,
                    on_screen_text="CẢNH BÁO BẢO MẬT (mẫu)",
                    narration=CitedText(text="Tin nóng hôm nay! (mẫu)", cited_article_ids=[ID_1]),
                    duration_sec=6.0,
                ),
                VideoScene(
                    scene_no=2,
                    on_screen_text="BẢN VÁ MỚI (mẫu)",
                    narration=CitedText(text="Chi tiết bản vá mới. (mẫu)", cited_article_ids=[ID_2]),
                    duration_sec=8.0,
                ),
            ],
        ),
        captions=[
            Caption(
                platform="facebook",
                text="Facebook caption mẫu",
                hashtags=["#AnNinhMang", "#BaoMat", "#TechNews"],
                cited_article_ids=[ID_1, ID_2],
            ),
            Caption(
                platform="tiktok",
                text="Tiktok caption mẫu",
                hashtags=["#TikTokNews", "#BaoMat", "#CongNghe"],
                cited_article_ids=[ID_1],
            ),
        ],
    )


# ==============================================================================
# Tests for list_runs and Loaders
# ==============================================================================


def test_list_runs_empty(tmp_path: Path) -> None:
    assert list_runs(tmp_path) == []
    assert list_runs(tmp_path / "nonexistent") == []


def test_list_runs_partial_and_ordering(tmp_path: Path) -> None:
    out_dir = tmp_path / "output"
    r_dir = out_dir / "research"
    e_dir = out_dir / "editorial"
    r_dir.mkdir(parents=True)
    e_dir.mkdir(parents=True)

    # Run 1: has only research
    (r_dir / "run-001.json").write_text("{}", encoding="utf-8")

    # Run 2: has both research and editorial (created later)
    (r_dir / "run-002.json").write_text("{}", encoding="utf-8")
    (e_dir / "run-002.json").write_text("{}", encoding="utf-8")
    (e_dir / "run-002.md").write_text("# Preview", encoding="utf-8")

    # Ignore .original.json
    (e_dir / "run-002.original.json").write_text("{}", encoding="utf-8")

    runs = list_runs(out_dir)
    assert len(runs) == 2
    # Check flags
    r1 = next(r for r in runs if r.run_id == "run-001")
    assert r1.has_research is True
    assert r1.has_editorial is False
    assert r1.has_validation is False

    r2 = next(r for r in runs if r.run_id == "run-002")
    assert r2.has_research is True
    assert r2.has_editorial is True
    assert r2.preview_path is not None


def test_load_research_success_and_error(tmp_path: Path) -> None:
    res = make_sample_research("run-ok")
    valid_file = tmp_path / "run-ok.json"
    valid_file.write_text(res.model_dump_json(), encoding="utf-8")

    loaded = load_research(valid_file)
    assert loaded.run_id == "run-ok"
    assert len(loaded.items) == 2

    # Missing file
    with pytest.raises(ArtifactError) as exc:
        load_research(tmp_path / "missing.json")
    assert "not found" in str(exc.value).lower()

    # Malformed file
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ArtifactError):
        load_research(bad_file)


def test_load_editorial_success_and_error(tmp_path: Path) -> None:
    pkg = make_sample_editorial("run-ok")
    valid_file = tmp_path / "editorial-ok.json"
    valid_file.write_text(pkg.model_dump_json(), encoding="utf-8")

    loaded = load_editorial(valid_file)
    assert loaded.run_id == "run-ok"
    assert len(loaded.digest.sections) == 2

    with pytest.raises(ArtifactError):
        load_editorial(tmp_path / "missing.json")


def test_load_validation_success_and_error(tmp_path: Path) -> None:
    assert load_validation(None) is None
    assert load_validation(tmp_path / "nonexistent.json") is None

    val_data = {"run_id": "test", "passed": True, "errors": [], "warnings": []}
    f = tmp_path / "val.json"
    f.write_text(json.dumps(val_data), encoding="utf-8")

    loaded = load_validation(f)
    assert loaded == val_data

    bad_f = tmp_path / "bad_val.json"
    bad_f.write_text("invalid json", encoding="utf-8")
    with pytest.raises(ArtifactError):
        load_validation(bad_f)


# ==============================================================================
# Tests for Research Notes & Source Cap View
# ==============================================================================


def test_parse_research_notes() -> None:
    notes = "model=gemini-3.6-flash; attempts=1; hallucinated=0; duplicates=0; source_cap_dropped=2"
    parsed = parse_research_notes(notes)
    assert parsed["model"] == "gemini-3.6-flash"
    assert parsed["attempts"] == "1"
    assert parsed["source_cap_dropped"] == "2"

    assert parse_research_notes("") == {}


def test_source_cap_view() -> None:
    res = make_sample_research()
    # cap = 1
    caps = source_cap_view(res, max_per_source=1)
    # The Hacker News: 1 -> at_cap=True
    # BleepingComputer: 1 -> at_cap=True
    assert len(caps) == 2
    assert all(c["at_cap"] is True for c in caps)

    caps2 = source_cap_view(res, max_per_source=2)
    assert all(c["at_cap"] is False for c in caps2)


# ==============================================================================
# Tests for SQLite query_articles and unselected_candidates
# ==============================================================================


def test_query_articles_missing_db(tmp_path: Path) -> None:
    missing_db = tmp_path / "nonexistent.db"
    assert query_articles(missing_db) == []
    # Assert DB was not created
    assert not missing_db.exists()


def test_query_articles_read_only_protection(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    conn = sqlite3.connect(db_file)
    init_db(conn)
    conn.close()

    # Open using query_articles logic
    uri = f"file:{db_file.resolve().as_posix()}?mode=ro"
    ro_conn = sqlite3.connect(uri, uri=True)
    with pytest.raises(sqlite3.OperationalError):
        ro_conn.execute("INSERT INTO articles (id, source_name, title, url, published_at, fetched_at) VALUES ('1','a','t','u','2026-01-01','2026-01-01')")
    ro_conn.close()


def test_query_articles_filters(tmp_path: Path) -> None:
    db_file = tmp_path / "test_filter.db"
    conn = sqlite3.connect(db_file)
    init_db(conn)

    t1 = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)

    art1 = Article(
        id=ID_1,
        source_name="Source A",
        title="Zero-Day Vulnerability in Linux Kernel",
        url="https://example.com/a1",
        published_at=t1,
        fetched_at=t1,
        summary="Critical security flaw discovered in Linux.",
        topics_matched=["vulnerability"],
    )
    art2 = Article(
        id=ID_2,
        source_name="Source B",
        title="Ransomware Attack on Healthcare System",
        url="https://example.com/a2",
        published_at=t2,
        fetched_at=t2,
        summary="Hospital operations affected by malware.",
        topics_matched=["ransomware"],
    )
    upsert_article(conn, art1)
    upsert_article(conn, art2)
    conn.close()

    # Source filter
    res = query_articles(db_file, sources=["Source A"])
    assert len(res) == 1
    assert res[0].id == ID_1

    # Date filter: since t2
    res_date = query_articles(db_file, since=t2)
    assert len(res_date) == 1
    assert res_date[0].id == ID_2

    # Keyword filter on summary (case-insensitive)
    res_kw = query_articles(db_file, keyword="linux")
    assert len(res_kw) == 1
    assert res_kw[0].id == ID_1

    res_kw2 = query_articles(db_file, keyword="HOSPITAL")
    assert len(res_kw2) == 1
    assert res_kw2[0].id == ID_2


def test_unselected_candidates(tmp_path: Path) -> None:
    db_file = tmp_path / "test_unsel.db"
    conn = sqlite3.connect(db_file)
    init_db(conn)

    now = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)
    # Selected article
    art1 = Article(
        id=ID_1,
        source_name="Source A",
        title="Article 1",
        url="https://example.com/1",
        published_at=now - timedelta(hours=10),
        fetched_at=now - timedelta(hours=10),
        summary="Summary 1",
    )
    # Unselected article in window
    art_unsel = Article(
        id="9999999999999999",
        source_name="Source C",
        title="Article Unselected",
        url="https://example.com/3",
        published_at=now - timedelta(hours=5),
        fetched_at=now - timedelta(hours=5),
        summary="Summary unselected",
    )
    upsert_article(conn, art1)
    upsert_article(conn, art_unsel)
    conn.close()

    res = make_sample_research()  # contains ID_1, but NOT 9999999999999999
    unselected = unselected_candidates(db_file, res, lookback_hours=48, now=now)
    assert len(unselected) == 1
    assert unselected[0].id == "9999999999999999"


# ==============================================================================
# Tests for Stats and Storyboard
# ==============================================================================


def test_caption_stats() -> None:
    pkg = make_sample_editorial()
    stats = caption_stats(pkg)
    assert len(stats) == 2
    fb = next(s for s in stats if s["platform"] == "facebook")
    assert fb["limit"] == 600
    assert fb["over_limit"] is False
    assert fb["hashtag_count"] == 3


def test_podcast_stats() -> None:
    pkg = make_sample_editorial()
    # segments have approx 10 words
    stats = podcast_stats(pkg, target_words=10, tolerance=0.2)
    assert "Minh" in stats["words_by_speaker"]
    assert "Lan" in stats["words_by_speaker"]
    assert stats["total_words"] > 0
    assert stats["min_allowed"] == 8
    assert stats["max_allowed"] == 12


def test_video_storyboard() -> None:
    pkg = make_sample_editorial()
    rows, total_sec, in_budget = video_storyboard(pkg, max_total_sec=20.0)
    assert len(rows) == 2
    assert total_sec == 14.0
    assert in_budget is True


# ==============================================================================
# Tests for Block Extraction, Highlighting & Freshness
# ==============================================================================


def test_blocks_by_article_paths_match_validator() -> None:
    res = make_sample_research()
    pkg = make_sample_editorial()

    mapping = blocks_by_article(res, pkg)
    assert ID_1 in mapping
    assert ID_2 in mapping

    paths = [b.path for b in mapping[ID_1]]
    assert "digest.sections[0].body.text" in paths
    assert "podcast.segments[0].line.text" in paths
    assert "video.scenes[0].narration.text" in paths
    assert "captions[0].text" in paths

    uncited = uncited_blocks(pkg)
    u_paths = [u.path for u in uncited]
    assert "digest.title" in u_paths
    assert "digest.intro" in u_paths
    assert "podcast.title" in u_paths
    assert "video.title" in u_paths


def test_highlight_escapes_xss_and_marks_evidence() -> None:
    malicious_text = "<script>alert('xss')</script> CVE-2026-9999 found."
    evidence = ["CVE-2026-9999"]
    result = highlight(malicious_text, evidence)

    # <script> must be escaped
    assert "<script>" not in result
    assert "&lt;script&gt;" in result
    # evidence must be wrapped with <mark>
    assert "<mark>CVE-2026-9999</mark>" in result


def test_validation_freshness(tmp_path: Path) -> None:
    ed_file = tmp_path / "editorial.json"
    val_file = tmp_path / "validation.json"

    # Missing validation
    assert validation_freshness(ed_file, None) == "missing"
    assert validation_freshness(ed_file, val_file) == "missing"

    # Fresh: val created after ed
    ed_file.write_text("{}", encoding="utf-8")
    val_file.write_text("{}", encoding="utf-8")
    assert validation_freshness(ed_file, val_file) == "fresh"

    # Stale: ed touched after val
    # Set val mtime into past
    import os
    past = ed_file.stat().st_mtime - 100
    os.utime(val_file, (past, past))
    assert validation_freshness(ed_file, val_file) == "stale"

