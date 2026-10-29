"""Unit tests for CyberPulse SQLite storage layer."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import time
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
    RunStatus,
    StepLog,
    VideoScene,
    VideoScript,
    make_article_id,
)
from cyberpulse.storage import (
    add_step_log,
    connect,
    create_run,
    get_run,
    init_db,
    list_step_logs,
    load_editorial_package,
    load_research_result,
    save_editorial_package,
    save_research_result,
    update_run_status,
    upsert_article,
)


@pytest.fixture
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    """Fixture providing an initialized SQLite connection in a temporary directory."""
    db_file = tmp_path / "test_cyberpulse.db"
    conn = connect(db_file)
    init_db(conn)
    yield conn
    conn.close()


def _make_sample_article(index: int) -> Article:
    url = f"https://example.com/sample/item-{index}"
    now = datetime.now(timezone.utc)
    return Article(
        id=make_article_id(url),
        source_name="Sample Source",
        title=f"SAMPLE — Cyber threat advisory #{index}",
        url=url,
        published_at=now,
        summary=f"SAMPLE — Summary of threat advisory #{index}",
        fetched_at=now,
        topics_matched=["security", "sample"],
    )


def test_init_db_idempotent_and_version(tmp_path: Path) -> None:
    """9. init_db twice -> no error; user_version == 1."""
    db_file = tmp_path / "idempotent.db"
    conn = connect(db_file)
    try:
        init_db(conn)
        init_db(conn)  # second execution
        cur = conn.execute("PRAGMA user_version")
        row = cur.fetchone()
        assert row[0] == 1
    finally:
        conn.close()


def test_run_lifecycle_and_timestamps(db_conn: sqlite3.Connection) -> None:
    """10. create_run -> get_run status 'pending'; update_run_status -> status changes, updated_at changes."""
    run_id = create_run(db_conn)
    run_data = get_run(db_conn, run_id)
    assert run_data is not None
    assert run_data["id"] == run_id
    assert run_data["status"] == "pending"
    assert run_data["created_at"] is not None
    assert run_data["updated_at"] is not None
    assert run_data["error"] is None

    original_updated_at = run_data["updated_at"]

    # Sleep slightly to ensure distinct ISO timestamp on fast execution
    time.sleep(0.01)

    update_run_status(db_conn, run_id, RunStatus.researching)
    updated_run = get_run(db_conn, run_id)
    assert updated_run is not None
    assert updated_run["status"] == "researching"
    assert updated_run["updated_at"] >= original_updated_at


def test_update_run_status_unknown_id_raises_keyerror(db_conn: sqlite3.Connection) -> None:
    """11. update_run_status on unknown id -> KeyError."""
    with pytest.raises(KeyError):
        update_run_status(db_conn, "nonexistent-run-id", RunStatus.failed)


def test_research_result_roundtrip(db_conn: sqlite3.Connection) -> None:
    """12. save/load ResearchResult round-trip equal."""
    run_id = create_run(db_conn)
    art1 = _make_sample_article(1)
    art2 = _make_sample_article(2)

    result = ResearchResult(
        run_id=run_id,
        status="ok",
        requested_count=2,
        items=[
            ResearchItem(rank=1, article=art1, relevance_reason="SAMPLE — high severity reason"),
            ResearchItem(rank=2, article=art2, relevance_reason="SAMPLE — broad impact reason"),
        ],
        notes="SAMPLE — research notes",
    )

    save_research_result(db_conn, result)
    loaded = load_research_result(db_conn, run_id)

    assert loaded is not None
    assert loaded.model_dump() == result.model_dump()


def test_editorial_package_roundtrip(db_conn: sqlite3.Connection) -> None:
    """13. save/load EditorialPackage round-trip equal."""
    run_id = create_run(db_conn)
    art_id = make_article_id("https://example.com/sample/item-1")

    pkg = EditorialPackage(
        run_id=run_id,
        language="vi",
        digest=Digest(
            title="SAMPLE — Bản tin an ninh mạng",
            intro="SAMPLE — Tổng quan các điểm tin chính trong ngày.",
            sections=[
                DigestSection(
                    heading="SAMPLE — Tiêu điểm",
                    body=CitedText(
                        text="SAMPLE — Thông tin chi tiết lỗ hổng nghiêm trọng.",
                        cited_article_ids=[art_id],
                    ),
                )
            ],
        ),
        podcast=PodcastScript(
            title="SAMPLE — Podcast CyberPulse",
            segments=[
                PodcastSegment(
                    speaker="Host",
                    line=CitedText(
                        text="SAMPLE — Chào mừng các bạn đến với bản tin hôm nay.",
                        cited_article_ids=[art_id],
                    ),
                )
            ],
        ),
        video=VideoScript(
            title="SAMPLE — Video Bản tin nhanh",
            scenes=[
                VideoScene(
                    scene_no=1,
                    on_screen_text="SAMPLE — Tin nóng an ninh mạng",
                    narration=CitedText(
                        text="SAMPLE — Khuyến nghị cập nhật hệ thống ngay lập tức.",
                        cited_article_ids=[art_id],
                    ),
                    duration_sec=10.5,
                )
            ],
        ),
        captions=[
            Caption(
                platform="facebook",
                text="SAMPLE — Cập nhật bản tin an ninh mạng mới nhất.",
                hashtags=["#CyberSecurity", "#Sample"],
                cited_article_ids=[art_id],
            )
        ],
    )

    save_editorial_package(db_conn, pkg)
    loaded = load_editorial_package(db_conn, run_id)

    assert loaded is not None
    assert loaded.model_dump() == pkg.model_dump()


def test_upsert_article_deduplication(db_conn: sqlite3.Connection) -> None:
    """14. Upserting same article twice -> one row."""
    art = _make_sample_article(1)
    upsert_article(db_conn, art)

    cur = db_conn.execute("SELECT count(*) FROM articles WHERE id = ?", (art.id,))
    assert cur.fetchone()[0] == 1

    # Update summary and re-upsert
    art_updated = art.model_copy(update={"summary": "SAMPLE — Updated summary content"})
    upsert_article(db_conn, art_updated)

    cur = db_conn.execute("SELECT count(*) FROM articles WHERE id = ?", (art.id,))
    assert cur.fetchone()[0] == 1

    cur = db_conn.execute("SELECT summary FROM articles WHERE id = ?", (art.id,))
    assert cur.fetchone()[0] == "SAMPLE — Updated summary content"


def test_foreign_key_enforcement(db_conn: sqlite3.Connection) -> None:
    """15. run_articles insert with unknown run_id -> sqlite3.IntegrityError."""
    art = _make_sample_article(1)
    upsert_article(db_conn, art)

    with pytest.raises(sqlite3.IntegrityError):
        with db_conn:
            db_conn.execute(
                """
                INSERT INTO run_articles (run_id, article_id, rank, relevance_reason)
                VALUES (?, ?, ?, ?)
                """,
                ("missing-run-id", art.id, 1, "SAMPLE — reason"),
            )


def test_step_logs_ordering(db_conn: sqlite3.Connection) -> None:
    """16. step logs returned in insertion order."""
    run_id = create_run(db_conn)
    now = datetime.now(timezone.utc)

    log1 = StepLog(
        run_id=run_id,
        step="research",
        status="started",
        attempt=1,
        message="SAMPLE — Starting research step",
        created_at=now,
    )
    log2 = StepLog(
        run_id=run_id,
        step="research",
        status="succeeded",
        attempt=1,
        message="SAMPLE — Research completed successfully",
        created_at=now,
    )
    log3 = StepLog(
        run_id=run_id,
        step="editorial",
        status="started",
        attempt=1,
        message="SAMPLE — Starting editorial step",
        created_at=now,
    )

    add_step_log(db_conn, log1)
    add_step_log(db_conn, log2)
    add_step_log(db_conn, log3)

    logs = list_step_logs(db_conn, run_id)
    assert len(logs) == 3
    assert [entry.step for entry in logs] == ["research", "research", "editorial"]
    assert [entry.status for entry in logs] == ["started", "succeeded", "started"]
    assert [entry.message for entry in logs] == [
        "SAMPLE — Starting research step",
        "SAMPLE — Research completed successfully",
        "SAMPLE — Starting editorial step",
    ]

