"""Unit tests for CyberPulse shared data contracts and Pydantic models."""

from __future__ import annotations

from datetime import datetime, timezone
import pytest
from pydantic import ValidationError

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


def _make_sample_article(
    url: str = "https://example.com/sample/1",
    title: str = "SAMPLE — Cyber defense update",
    published_at: datetime | None = None,
    fetched_at: datetime | None = None,
) -> Article:
    now = datetime.now(timezone.utc)
    pub = published_at if published_at is not None else now
    fet = fetched_at if fetched_at is not None else now
    aid = make_article_id(url)
    return Article(
        id=aid,
        source_name="Sample Source",
        title=title,
        url=url,
        published_at=pub,
        summary="SAMPLE — A sample article summary",
        fetched_at=fet,
        topics_matched=["sample_topic"],
    )


def test_article_naive_datetime_rejected() -> None:
    """1. Naive datetime in Article -> ValidationError."""
    naive_dt = datetime(2026, 10, 29, 12, 0, 0)
    aware_dt = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)

    # published_at naive
    with pytest.raises(ValidationError):
        _make_sample_article(published_at=naive_dt, fetched_at=aware_dt)

    # fetched_at naive
    with pytest.raises(ValidationError):
        _make_sample_article(published_at=aware_dt, fetched_at=naive_dt)


def test_article_invalid_url_scheme_rejected() -> None:
    """2. Article url 'ftp://x' -> ValidationError."""
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        Article(
            id="0123456789abcdef",
            source_name="Sample Source",
            title="SAMPLE — Invalid protocol",
            url="ftp://example.com/sample/test",
            published_at=now,
            summary="SAMPLE — summary",
            fetched_at=now,
            topics_matched=[],
        )


def test_research_result_ok_count_mismatch() -> None:
    """3. ResearchResult status 'ok' with 4 items and requested_count 5 -> ValidationError."""
    items = [
        ResearchItem(
            rank=i,
            article=_make_sample_article(f"https://example.com/sample/{i}"),
            relevance_reason=f"SAMPLE — Relevance reason for item {i}",
        )
        for i in range(1, 5)
    ]
    with pytest.raises(ValidationError) as exc_info:
        ResearchResult(
            run_id="sample-run-123",
            status="ok",
            requested_count=5,
            items=items,
        )
    assert "status 'ok' requires len(items) == requested_count" in str(exc_info.value)


def test_research_result_insufficient_valid() -> None:
    """4. ResearchResult 'insufficient' with 3 of 5 -> valid."""
    items = [
        ResearchItem(
            rank=i,
            article=_make_sample_article(f"https://example.com/sample/{i}"),
            relevance_reason=f"SAMPLE — Relevance reason for item {i}",
        )
        for i in range(1, 4)
    ]
    res = ResearchResult(
        run_id="sample-run-123",
        status="insufficient",
        requested_count=5,
        items=items,
    )
    assert res.status == "insufficient"
    assert len(res.items) == 3


def test_research_result_duplicate_ranks_or_ids() -> None:
    """5. Duplicate rank or duplicate article id -> ValidationError."""
    art1 = _make_sample_article("https://example.com/sample/1")
    art2 = _make_sample_article("https://example.com/sample/2")

    # Duplicate ranks (1, 1)
    with pytest.raises(ValidationError) as exc1:
        ResearchResult(
            run_id="sample-run-123",
            status="ok",
            requested_count=2,
            items=[
                ResearchItem(rank=1, article=art1, relevance_reason="SAMPLE — reason 1"),
                ResearchItem(rank=1, article=art2, relevance_reason="SAMPLE — reason 2"),
            ],
        )
    assert "ranks must be exactly 1..2" in str(exc1.value)

    # Duplicate article ids with different ranks
    with pytest.raises(ValidationError) as exc2:
        ResearchResult(
            run_id="sample-run-123",
            status="ok",
            requested_count=2,
            items=[
                ResearchItem(rank=1, article=art1, relevance_reason="SAMPLE — reason 1"),
                ResearchItem(rank=2, article=art1, relevance_reason="SAMPLE — reason 2"),
            ],
        )
    assert "article ids must be unique" in str(exc2.value)


def test_cited_text_empty_citations_rejected() -> None:
    """6. CitedText with empty cited_article_ids -> ValidationError."""
    with pytest.raises(ValidationError):
        CitedText(text="SAMPLE — Some referenced claim", cited_article_ids=[])

    with pytest.raises(ValidationError):
        CitedText(text="", cited_article_ids=["0123456789abcdef"])


def test_video_script_scene_numbers_invalid() -> None:
    """7. VideoScript scene_no not 1..n -> ValidationError."""
    cited = CitedText(text="SAMPLE — Narration", cited_article_ids=["0123456789abcdef"])
    # Non-consecutive scenes: 1 and 3
    scenes = [
        VideoScene(scene_no=1, on_screen_text="SAMPLE — Intro", narration=cited, duration_sec=10.0),
        VideoScene(scene_no=3, on_screen_text="SAMPLE — Outro", narration=cited, duration_sec=10.0),
    ]
    with pytest.raises(ValidationError) as exc_info:
        VideoScript(title="SAMPLE — Video", scenes=scenes)
    assert "scene_no must be exactly 1..2" in str(exc_info.value)


def test_editorial_package_all_cited_ids() -> None:
    """8. EditorialPackage.all_cited_ids returns ids from digest, podcast, video and captions."""
    id1 = "1111111111111111"
    id2 = "2222222222222222"
    id3 = "3333333333333333"
    id4 = "4444444444444444"

    digest = Digest(
        title="SAMPLE — Digest",
        intro="SAMPLE — Intro",
        sections=[
            DigestSection(
                heading="SAMPLE — Section 1",
                body=CitedText(text="SAMPLE — Text 1", cited_article_ids=[id1]),
            )
        ],
    )
    podcast = PodcastScript(
        title="SAMPLE — Podcast",
        segments=[
            PodcastSegment(
                speaker="Host",
                line=CitedText(text="SAMPLE — Host line", cited_article_ids=[id2]),
            )
        ],
    )
    video = VideoScript(
        title="SAMPLE — Video",
        scenes=[
            VideoScene(
                scene_no=1,
                on_screen_text="SAMPLE — Title",
                narration=CitedText(text="SAMPLE — Narration", cited_article_ids=[id3]),
                duration_sec=15.0,
            )
        ],
    )
    captions = [
        Caption(
            platform="generic",
            text="SAMPLE — Caption text",
            hashtags=["#SampleNews"],
            cited_article_ids=[id4],
        )
    ]

    pkg = EditorialPackage(
        run_id="sample-run-pkg",
        language="vi",
        digest=digest,
        podcast=podcast,
        video=video,
        captions=captions,
    )

    all_ids = pkg.all_cited_ids()
    assert all_ids == {id1, id2, id3, id4}

