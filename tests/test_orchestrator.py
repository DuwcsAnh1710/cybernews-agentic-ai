"""Hermetic unit and integration tests for Orchestrator core."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
from typing import Any
import pytest

from cyberpulse.config import PathsConfig, Settings
from cyberpulse.llm import (
    LLMAuthError,
    LLMConfigError,
    LLMError,
    LLMRateLimitError,
    LLMTransientError,
)
from cyberpulse.agents.editorial import EditorialRefError
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
from cyberpulse.orchestrator import (
    ALLOWED_TRANSITIONS,
    DEGRADED_MARKER,
    FailureReason,
    IllegalTransition,
    Orchestrator,
    RunResult,
    atomic_write_text,
    default_candidate_fetcher,
    generate_run_id,
    is_run_degraded,
    sanitize_error_message,
)
from cyberpulse.storage import (
    connect,
    get_run,
    init_db,
    list_recent_articles,
    list_step_logs,
    load_editorial_package,
    load_research_result,
    load_validation_report,
    upsert_article,
)
from cyberpulse.ui.data import list_runs
from cyberpulse.validator import ValidationIssue, ValidationReport


# ==============================================================================
# Test Fixtures & Helpers
# ==============================================================================

@pytest.fixture
def fake_settings(tmp_path: Path) -> Settings:
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "cyberpulse.db"
    return Settings(
        project_name="CyberPulse Test",
        topics=["cybersecurity"],
        lookback_hours=24,
        paths=PathsConfig(
            data_dir=tmp_path / "data",
            output_dir=output_dir,
            log_dir=log_dir,
            db_path=db_path,
        ),
    )


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "test_orchestrator.db")
    init_db(c)
    yield c
    c.close()


_NOW = datetime.now(timezone.utc).replace(microsecond=0)


def make_article(
    article_suffix: str = "1",
    title: str = "Critical Vulnerability in System X",
    source_name: str = "The Record",
    hours_ago: int = 1,
) -> Article:
    now = _NOW
    pub = now - timedelta(hours=hours_ago)
    url = f"https://example.com/news/{article_suffix}"
    art_id = make_article_id(url)
    return Article(
        id=art_id,
        source_name=source_name,
        title=title,
        url=url,
        published_at=pub,
        summary=f"Summary for {title}. Impacting millions of devices.",
        fetched_at=now,
        topics_json='["vulnerability", "patch"]',
    )


def make_research_result(
    run_id: str,
    status: str = "ok",
    count: int = 3,
    requested_count: int = 3,
) -> ResearchResult:
    items = []
    for i in range(1, count + 1):
        art = make_article(f"art-{i}", f"Cyber Threat News {i}", source_name=f"Source {i}")
        items.append(
            ResearchItem(
                rank=i,
                article=art,
                relevance_reason=f"Important security update {i}",
            )
        )
    return ResearchResult(
        run_id=run_id,
        status=status,  # type: ignore[arg-type]
        requested_count=requested_count,
        items=items,
        notes="Standard ranking completed",
    )


def make_editorial_package(run_id: str, research: ResearchResult) -> EditorialPackage:
    art_ids = [item.article.id for item in research.items]
    art_id = art_ids[0] if art_ids else "art-1"
    return EditorialPackage(
        run_id=run_id,
        language="vi",
        digest=Digest(
            title="Bản tin an ninh mạng",
            intro="Tổng quan các điểm tin chính trong ngày.",
            sections=[
                DigestSection(
                    heading="Tiêu điểm",
                    body=CitedText(
                        text="Thông tin chi tiết lỗ hổng nghiêm trọng.",
                        cited_article_ids=[art_id],
                    ),
                )
            ],
        ),
        podcast=PodcastScript(
            title="Podcast CyberPulse",
            segments=[
                PodcastSegment(
                    speaker="Host",
                    line=CitedText(
                        text="Chào mừng các bạn đến với bản tin hôm nay.",
                        cited_article_ids=[art_id],
                    ),
                )
            ],
        ),
        video=VideoScript(
            title="Video Bản tin nhanh",
            scenes=[
                VideoScene(
                    scene_no=1,
                    on_screen_text="Tin nóng an ninh mạng",
                    narration=CitedText(
                        text="Khuyến nghị cập nhật hệ thống ngay lập tức.",
                        cited_article_ids=[art_id],
                    ),
                    duration_sec=10.5,
                )
            ],
        ),
        captions=[
            Caption(
                platform="facebook",
                text="Cập nhật bản tin an ninh mạng mới nhất.",
                hashtags=["#CyberSecurity", "#News"],
                cited_article_ids=[art_id],
            )
        ],
    )


def make_validation_report(run_id: str, passed: bool = True) -> ValidationReport:
    errors = []
    if not passed:
        errors.append(
            ValidationIssue(
                code="E_SECTION_COUNT",
                severity="error",
                path="digest.sections",
                message="Expected at least 3 sections",
            )
        )
    return ValidationReport(
        run_id=run_id,
        passed=passed,
        errors=errors,
        warnings=[],
        checks_run=["structure", "source_consistency"],
        llm_review="skipped",
        scope_note="Test report",
    )


class SleepTracker:
    def __init__(self) -> None:
        self.delays: list[float] = []

    def sleep(self, delay: float) -> None:
        self.delays.append(delay)


# ==============================================================================
# 1. State Machine & Transitions Tests (Acceptance Criteria 2)
# ==============================================================================

def test_transition_table_allowed_transitions(conn: sqlite3.Connection, fake_settings: Settings) -> None:
    """Every transition in ALLOWED_TRANSITIONS succeeds and writes to DB and step_logs."""
    dummy_research = lambda rid, cands: make_research_result(rid)
    dummy_editorial = lambda res: make_editorial_package(res.run_id, res)
    dummy_validate = lambda res, pkg: make_validation_report(pkg.run_id, passed=True)

    orch = Orchestrator(
        conn=conn,
        settings=fake_settings,
        research_step=dummy_research,
        editorial_step=dummy_editorial,
        validate_step=dummy_validate,
    )

    # Test pending -> researching -> editing -> validating -> awaiting_review
    run_id = "test-trans-1"
    from cyberpulse.storage import create_run
    create_run(conn, run_id=run_id)
    orch._current_status[run_id] = RunStatus.pending

    orch._transition_to(run_id, RunStatus.researching)
    assert get_run(conn, run_id)["status"] == RunStatus.researching.value

    orch._transition_to(run_id, RunStatus.editing)
    assert get_run(conn, run_id)["status"] == RunStatus.editing.value

    orch._transition_to(run_id, RunStatus.validating)
    assert get_run(conn, run_id)["status"] == RunStatus.validating.value

    orch._transition_to(run_id, RunStatus.awaiting_review)
    assert get_run(conn, run_id)["status"] == RunStatus.awaiting_review.value

    # Verify each transition wrote a StepLog
    logs = list_step_logs(conn, run_id)
    assert len(logs) == 4
    assert [l.step for l in logs] == ["orchestrator", "orchestrator", "orchestrator", "orchestrator"]


def test_transition_table_illegal_transitions(conn: sqlite3.Connection, fake_settings: Settings) -> None:
    """Every (from, to) pair not in ALLOWED_TRANSITIONS raises IllegalTransition."""
    orch = Orchestrator(
        conn=conn,
        settings=fake_settings,
        research_step=lambda r, c: make_research_result(r),
        editorial_step=lambda r: make_editorial_package(r.run_id, r),
        validate_step=lambda r, p: make_validation_report(p.run_id),
    )

    all_statuses = list(RunStatus)
    for from_st in all_statuses:
        allowed = ALLOWED_TRANSITIONS.get(from_st, set())
        for to_st in all_statuses:
            if to_st not in allowed:
                run_id = f"test-illegal-{from_st.value}-{to_st.value}"
                from cyberpulse.storage import create_run
                create_run(conn, run_id=run_id)
                orch._current_status[run_id] = from_st
                with pytest.raises(IllegalTransition) as exc_info:
                    orch._transition_to(run_id, to_st)
                assert exc_info.value.from_status == from_st
                assert exc_info.value.to_status == to_st


def test_orchestrator_terminal_states_reject_outgoing(conn: sqlite3.Connection, fake_settings: Settings) -> None:
    """Terminal states (awaiting_review, failed) reject all outgoing transitions."""
    orch = Orchestrator(
        conn=conn,
        settings=fake_settings,
        research_step=lambda r, c: make_research_result(r),
        editorial_step=lambda r: make_editorial_package(r.run_id, r),
        validate_step=lambda r, p: make_validation_report(p.run_id),
    )

    for terminal_st in (RunStatus.awaiting_review, RunStatus.failed):
        run_id = f"term-{terminal_st.value}"
        from cyberpulse.storage import create_run
        create_run(conn, run_id=run_id)
        orch._current_status[run_id] = terminal_st
        for target in RunStatus:
            with pytest.raises(IllegalTransition):
                orch._transition_to(run_id, target)


# ==============================================================================
# 2. Happy Path & Artifacts Tests (Acceptance Criteria 3, Extra Test UI List)
# ==============================================================================

def test_orchestrator_happy_path(conn: sqlite3.Connection, fake_settings: Settings) -> None:
    """Happy path runs through all steps to awaiting_review and persists all artifacts."""
    # Seed candidates in DB
    for i in range(1, 4):
        upsert_article(conn, make_article(f"art-{i}", f"Article {i}"))

    research_fake = lambda rid, cands: make_research_result(rid, status="ok", count=3)
    editorial_fake = lambda res: make_editorial_package(res.run_id, res)
    validate_fake = lambda res, pkg: make_validation_report(pkg.run_id, passed=True)

    fixed_time = _NOW
    sleep_tracker = SleepTracker()

    orch = Orchestrator(
        conn=conn,
        settings=fake_settings,
        research_step=research_fake,
        editorial_step=editorial_fake,
        validate_step=validate_fake,
        clock_fn=lambda: fixed_time,
        sleep_fn=sleep_tracker.sleep,
    )

    result = orch.run()

    # Verify RunResult
    assert result.status == RunStatus.awaiting_review
    assert result.failure_reason is None
    assert result.degraded is False
    assert result.error_message is None
    assert result.step_attempts == {"research": 1, "editorial": 1, "validate": 1}
    assert sleep_tracker.delays == []

    # Verify DB runs record
    run_rec = get_run(conn, result.run_id)
    assert run_rec is not None
    assert run_rec["status"] == RunStatus.awaiting_review.value
    assert run_rec["error"] is None

    # Verify research result stored in contents and run_articles
    loaded_res = load_research_result(conn, result.run_id)
    assert loaded_res is not None
    assert loaded_res.run_id == result.run_id
    assert len(loaded_res.items) == 3

    # Verify editorial package stored in contents
    loaded_pkg = load_editorial_package(conn, result.run_id)
    assert loaded_pkg is not None
    assert loaded_pkg.run_id == result.run_id

    # Verify validation report stored in contents
    loaded_rep = load_validation_report(conn, result.run_id)
    assert loaded_rep is not None
    assert loaded_rep.passed is True

    # Verify output files exist with matching run_id
    assert result.artifacts["research"].exists()
    assert result.artifacts["editorial_json"].exists()
    assert result.artifacts["editorial_md"].exists()
    assert result.artifacts["validation"].exists()

    # Load artifacts directly
    res_from_file = ResearchResult.model_validate_json(result.artifacts["research"].read_text(encoding="utf-8"))
    assert res_from_file.run_id == result.run_id

    pkg_from_file = EditorialPackage.model_validate_json(result.artifacts["editorial_json"].read_text(encoding="utf-8"))
    assert pkg_from_file.run_id == result.run_id

    rep_from_file = ValidationReport.model_validate_json(result.artifacts["validation"].read_text(encoding="utf-8"))
    assert rep_from_file.run_id == result.run_id
    assert rep_from_file.passed is True

    # Extra test: ui/data.py list_runs pairs research, editorial, and validation
    runs_list = list_runs(fake_settings.paths.output_dir)
    assert len(runs_list) == 1
    ui_item = runs_list[0]
    assert ui_item.run_id == result.run_id
    assert ui_item.research_path == result.artifacts["research"]
    assert ui_item.editorial_path == result.artifacts["editorial_json"]
    assert ui_item.preview_path == result.artifacts["editorial_md"]
    assert ui_item.validation_path == result.artifacts["validation"]


# ==============================================================================
# 3. Research Policy Tests (Acceptance Criteria 4 & 5, Amendments M3, M10)
# ==============================================================================

def test_research_insufficient_aborts_by_default(conn: sqlite3.Connection, fake_settings: Settings) -> None:
    """When research status is 'insufficient' and allow_insufficient=False, run ends failed(insufficient_research)."""
    upsert_article(conn, make_article("art-1", "Only Article"))

    editorial_called = 0
    def editorial_fake(res: ResearchResult) -> EditorialPackage:
        nonlocal editorial_called
        editorial_called += 1
        return make_editorial_package(res.run_id, res)

    research_fake = lambda rid, cands: make_research_result(rid, status="insufficient", count=1, requested_count=5)

    orch = Orchestrator(
        conn=conn,
        settings=fake_settings,
        research_step=research_fake,
        editorial_step=editorial_fake,
        validate_step=lambda r, p: make_validation_report(p.run_id),
        allow_insufficient=False,
    )

    result = orch.run()
    assert result.status == RunStatus.failed
    assert result.failure_reason == FailureReason.insufficient_research
    assert editorial_called == 0
    assert "Insufficient research" in (result.error_message or "")

    run_rec = get_run(conn, result.run_id)
    assert run_rec["status"] == RunStatus.failed.value
    assert "insufficient_research" in run_rec["error"]


def test_research_insufficient_allowed_degraded(conn: sqlite3.Connection, fake_settings: Settings) -> None:
    """When research is 'insufficient' and allow_insufficient=True, run continues and sets degraded=True."""
    upsert_article(conn, make_article("art-1", "Single article"))

    research_fake = lambda rid, cands: make_research_result(rid, status="insufficient", count=1, requested_count=5)
    editorial_fake = lambda res: make_editorial_package(res.run_id, res)
    validate_fake = lambda res, pkg: make_validation_report(pkg.run_id, passed=True)

    orch = Orchestrator(
        conn=conn,
        settings=fake_settings,
        research_step=research_fake,
        editorial_step=editorial_fake,
        validate_step=validate_fake,
        allow_insufficient=True,
    )

    result = orch.run()
    assert result.status == RunStatus.awaiting_review
    assert result.degraded is True

    # Assert degraded marker helper agrees
    assert is_run_degraded(conn, result.run_id) is True

    # Assert warning StepLog exists
    logs = list_step_logs(conn, result.run_id)
    degraded_logs = [l for l in logs if DEGRADED_MARKER in l.message]
    assert len(degraded_logs) == 1
    assert "requested 5, got 1" in degraded_logs[0].message


def test_research_kept_zero_always_fails(conn: sqlite3.Connection, fake_settings: Settings) -> None:
    """When kept articles count is 0, run ALWAYS fails even with allow_insufficient=True."""
    upsert_article(conn, make_article("art-1", "Ignored article"))

    def research_zero_items(rid: str, cands: list[Article]) -> ResearchResult:
        return ResearchResult(
            run_id=rid,
            status="insufficient",
            requested_count=3,
            items=[],
            notes="No relevant news found",
        )

    editorial_called = 0
    def editorial_fake(res: ResearchResult) -> EditorialPackage:
        nonlocal editorial_called
        editorial_called += 1
        return make_editorial_package(res.run_id, res)

    orch = Orchestrator(
        conn=conn,
        settings=fake_settings,
        research_step=research_zero_items,
        editorial_step=editorial_fake,
        validate_step=lambda r, p: make_validation_report(p.run_id),
        allow_insufficient=True,
    )

    result = orch.run()
    assert result.status == RunStatus.failed
    assert result.failure_reason == FailureReason.insufficient_research
    assert "0 articles kept" in (result.error_message or "")
    assert editorial_called == 0


def test_empty_candidates_in_db_fails_insufficient_research(conn: sqlite3.Connection, fake_settings: Settings) -> None:
    """No candidates in DB ends in failed(insufficient_research) via pending -> failed edge."""
    orch = Orchestrator(
        conn=conn,
        settings=fake_settings,
        research_step=lambda r, c: make_research_result(r),
        editorial_step=lambda r: make_editorial_package(r.run_id, r),
        validate_step=lambda r, p: make_validation_report(p.run_id),
    )

    result = orch.run()
    assert result.status == RunStatus.failed
    assert result.failure_reason == FailureReason.insufficient_research
    assert result.error_message == "no candidates"
    assert result.step_attempts["research"] == 0

    run_rec = get_run(conn, result.run_id)
    assert run_rec["status"] == RunStatus.failed.value
    assert "no candidates" in run_rec["error"]


def test_default_candidate_fetcher_matches_list_recent_articles(conn: sqlite3.Connection, fake_settings: Settings) -> None:
    """Default candidate selection matches list_recent_articles with settings lookback and max_candidates."""
    now = _NOW
    # Seed 5 articles: 3 within 24h, 2 older than 24h
    a1 = make_article("recent-1", "Recent 1", hours_ago=2)
    a2 = make_article("recent-2", "Recent 2", hours_ago=5)
    a3 = make_article("recent-3", "Recent 3", hours_ago=12)
    upsert_article(conn, a1)
    upsert_article(conn, a2)
    upsert_article(conn, a3)
    upsert_article(conn, make_article("old-1", "Old 1", hours_ago=30))
    upsert_article(conn, make_article("old-2", "Old 2", hours_ago=48))

    default_selected = default_candidate_fetcher(conn, fake_settings, clock_fn=lambda: now)
    assert len(default_selected) == 3
    assert [a.id for a in default_selected] == [a1.id, a2.id, a3.id]


# ==============================================================================
# 4. Bounded Retry & Error Classification Tests (Criteria 6, Amendments M4, M5)
# ==============================================================================

def test_transient_llm_error_retry_succeeds(conn: sqlite3.Connection, fake_settings: Settings) -> None:
    """Transient LLM error on attempt 1 succeeds on attempt 2."""
    upsert_article(conn, make_article("art-1", "Article 1"))

    attempts = 0
    def flaky_research(rid: str, cands: list[Article]) -> ResearchResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise LLMTransientError("Simulated 503 network timeout")
        return make_research_result(rid)

    sleep_tracker = SleepTracker()
    orch = Orchestrator(
        conn=conn,
        settings=fake_settings,
        research_step=flaky_research,
        editorial_step=lambda r: make_editorial_package(r.run_id, r),
        validate_step=lambda r, p: make_validation_report(p.run_id),
        sleep_fn=sleep_tracker.sleep,
        max_step_attempts=2,
    )

    result = orch.run()
    assert result.status == RunStatus.awaiting_review
    assert result.step_attempts["research"] == 2
    assert len(sleep_tracker.delays) == 1
    assert sleep_tracker.delays[0] == 5.0  # backoff_base_sec * 2^0


def test_rate_limit_error_retry_with_retry_after(conn: sqlite3.Connection, fake_settings: Settings) -> None:
    """LLMRateLimitError honors retry_after_sec when <= 60."""
    upsert_article(conn, make_article("art-1", "Article 1"))

    attempts = 0
    def rate_limited_editorial(res: ResearchResult) -> EditorialPackage:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise LLMRateLimitError("Rate limit exceeded 429", retry_after_sec=15.0)
        return make_editorial_package(res.run_id, res)

    sleep_tracker = SleepTracker()
    orch = Orchestrator(
        conn=conn,
        settings=fake_settings,
        research_step=lambda r, c: make_research_result(r),
        editorial_step=rate_limited_editorial,
        validate_step=lambda r, p: make_validation_report(p.run_id),
        sleep_fn=sleep_tracker.sleep,
        max_step_attempts=2,
        backoff_base_sec=5.0,
    )

    result = orch.run()
    assert result.status == RunStatus.awaiting_review
    assert result.step_attempts["editorial"] == 2
    assert len(sleep_tracker.delays) == 1
    assert sleep_tracker.delays[0] == 15.0  # max(5.0, 15.0)


def test_retryable_llm_error_exhausts_retries(conn: sqlite3.Connection, fake_settings: Settings) -> None:
    """Transient LLM error every time fails after exactly max_step_attempts attempts."""
    upsert_article(conn, make_article("art-1", "Article 1"))

    attempts = 0
    def always_failing_research(rid: str, cands: list[Article]) -> ResearchResult:
        nonlocal attempts
        attempts += 1
        raise LLMTransientError("Continuous 503 backend failure")

    sleep_tracker = SleepTracker()
    orch = Orchestrator(
        conn=conn,
        settings=fake_settings,
        research_step=always_failing_research,
        editorial_step=lambda r: make_editorial_package(r.run_id, r),
        validate_step=lambda r, p: make_validation_report(p.run_id),
        sleep_fn=sleep_tracker.sleep,
        max_step_attempts=2,
    )

    result = orch.run()
    assert result.status == RunStatus.failed
    assert result.failure_reason == FailureReason.llm_error
    assert attempts == 2
    assert result.step_attempts["research"] == 2
    assert len(sleep_tracker.delays) == 1


def test_non_retryable_llm_error_fails_immediately(conn: sqlite3.Connection, fake_settings: Settings) -> None:
    """Non-retryable LLMAuthError fails immediately on attempt 1 without sleeping."""
    upsert_article(conn, make_article("art-1", "Article 1"))

    attempts = 0
    def auth_failing_research(rid: str, cands: list[Article]) -> ResearchResult:
        nonlocal attempts
        attempts += 1
        raise LLMAuthError("Invalid API key")

    sleep_tracker = SleepTracker()
    orch = Orchestrator(
        conn=conn,
        settings=fake_settings,
        research_step=auth_failing_research,
        editorial_step=lambda r: make_editorial_package(r.run_id, r),
        validate_step=lambda r, p: make_validation_report(p.run_id),
        sleep_fn=sleep_tracker.sleep,
        max_step_attempts=3,
    )

    result = orch.run()
    assert result.status == RunStatus.failed
    assert result.failure_reason == FailureReason.llm_error
    assert attempts == 1
    assert sleep_tracker.delays == []


def test_non_llm_editorial_error_fails_immediately(conn: sqlite3.Connection, fake_settings: Settings) -> None:
    """EditorialRefError fails immediately on attempt 1 with failure_reason=editorial_failed."""
    upsert_article(conn, make_article("art-1", "Article 1"))

    def ref_error_editorial(res: ResearchResult) -> EditorialPackage:
        raise EditorialRefError(["[ref:99]"], ["digest.sections[0].cites"])

    sleep_tracker = SleepTracker()
    orch = Orchestrator(
        conn=conn,
        settings=fake_settings,
        research_step=lambda r, c: make_research_result(r),
        editorial_step=ref_error_editorial,
        validate_step=lambda r, p: make_validation_report(p.run_id),
        sleep_fn=sleep_tracker.sleep,
        max_step_attempts=3,
    )

    result = orch.run()
    assert result.status == RunStatus.failed
    assert result.failure_reason == FailureReason.editorial_failed
    assert result.step_attempts["editorial"] == 1
    assert sleep_tracker.delays == []


def test_max_step_attempts_1_means_no_retry_no_sleep(conn: sqlite3.Connection, fake_settings: Settings) -> None:
    """When max_step_attempts=1, exactly one attempt is run and sleep_fn is never called."""
    upsert_article(conn, make_article("art-1", "Article 1"))

    attempts = 0
    def failing_research(rid: str, cands: list[Article]) -> ResearchResult:
        nonlocal attempts
        attempts += 1
        raise LLMTransientError("Transient error")

    sleep_tracker = SleepTracker()
    orch = Orchestrator(
        conn=conn,
        settings=fake_settings,
        research_step=failing_research,
        editorial_step=lambda r: make_editorial_package(r.run_id, r),
        validate_step=lambda r, p: make_validation_report(p.run_id),
        sleep_fn=sleep_tracker.sleep,
        max_step_attempts=1,
    )

    result = orch.run()
    assert result.status == RunStatus.failed
    assert attempts == 1
    assert sleep_tracker.delays == []


def test_backoff_delays_asserted_5_then_10(conn: sqlite3.Connection, fake_settings: Settings) -> None:
    """With max_step_attempts=3, backoff delays asserted are 5.0 then 10.0."""
    upsert_article(conn, make_article("art-1", "Article 1"))

    def always_transient_error(rid: str, cands: list[Article]) -> ResearchResult:
        raise LLMTransientError("Persistent transient failure")

    sleep_tracker = SleepTracker()
    orch = Orchestrator(
        conn=conn,
        settings=fake_settings,
        research_step=always_transient_error,
        editorial_step=lambda r: make_editorial_package(r.run_id, r),
        validate_step=lambda r, p: make_validation_report(p.run_id),
        sleep_fn=sleep_tracker.sleep,
        max_step_attempts=3,
        backoff_base_sec=5.0,
    )

    result = orch.run()
    assert result.status == RunStatus.failed
    assert sleep_tracker.delays == [5.0, 10.0]


# ==============================================================================
# 5. Validation Failure Tests (Acceptance Criteria 7)
# ==============================================================================

def test_validation_errors_ends_in_failed(conn: sqlite3.Connection, fake_settings: Settings) -> None:
    """Validation report with errors ends in failed(validation_failed); artifacts remain on disk."""
    upsert_article(conn, make_article("art-1", "Article 1"))

    editorial_called = 0
    def editorial_fake(res: ResearchResult) -> EditorialPackage:
        nonlocal editorial_called
        editorial_called += 1
        return make_editorial_package(res.run_id, res)

    validate_fake = lambda res, pkg: make_validation_report(pkg.run_id, passed=False)

    orch = Orchestrator(
        conn=conn,
        settings=fake_settings,
        research_step=lambda r, c: make_research_result(r),
        editorial_step=editorial_fake,
        validate_step=validate_fake,
    )

    result = orch.run()
    assert result.status == RunStatus.failed
    assert result.failure_reason == FailureReason.validation_failed
    assert editorial_called == 1

    # Check artifacts remain on disk for inspection
    assert result.artifacts["research"].exists()
    assert result.artifacts["editorial_json"].exists()
    assert result.artifacts["editorial_md"].exists()
    assert result.artifacts["validation"].exists()

    run_rec = get_run(conn, result.run_id)
    assert run_rec["status"] == RunStatus.failed.value
    assert "validation_failed" in run_rec["error"]


# ==============================================================================
# 6. Fault Tolerance & Stop Conditions Tests (Criteria 8, 9, Amendments M6, M12)
# ==============================================================================

@pytest.mark.parametrize("step_name", ["research", "editorial", "validate"])
def test_unexpected_exception_in_steps_parametrized(
    conn: sqlite3.Connection,
    fake_settings: Settings,
    step_name: str,
) -> None:
    """Unexpected exception in any step ends run in failed(internal_error) without crashing run()."""
    upsert_article(conn, make_article("art-1", "Article 1"))

    def faulty_research(rid: str, cands: list[Article]) -> ResearchResult:
        if step_name == "research":
            raise RuntimeError("Unexpected memory fault in research")
        return make_research_result(rid)

    def faulty_editorial(res: ResearchResult) -> EditorialPackage:
        if step_name == "editorial":
            raise RuntimeError("Unexpected disk error in editorial")
        return make_editorial_package(res.run_id, res)

    def faulty_validate(res: ResearchResult, pkg: EditorialPackage) -> ValidationReport:
        if step_name == "validate":
            raise RuntimeError("Unexpected assertion failure in validate")
        return make_validation_report(pkg.run_id, passed=True)

    orch = Orchestrator(
        conn=conn,
        settings=fake_settings,
        research_step=faulty_research,
        editorial_step=faulty_editorial,
        validate_step=faulty_validate,
    )

    result = orch.run()
    assert result.status == RunStatus.failed
    assert result.failure_reason == FailureReason.internal_error
    assert "Unexpected" in (result.error_message or "")

    run_rec = get_run(conn, result.run_id)
    assert run_rec["status"] == RunStatus.failed.value
    assert "internal_error" in run_rec["error"]


def test_keyboard_interrupt_propagates_and_marks_db(conn: sqlite3.Connection, fake_settings: Settings) -> None:
    """KeyboardInterrupt marks run failed(interrupted) and re-raises exception."""
    upsert_article(conn, make_article("art-1", "Article 1"))

    def interrupted_research(rid: str, cands: list[Article]) -> ResearchResult:
        raise KeyboardInterrupt()

    orch = Orchestrator(
        conn=conn,
        settings=fake_settings,
        research_step=interrupted_research,
        editorial_step=lambda r: make_editorial_package(r.run_id, r),
        validate_step=lambda r, p: make_validation_report(p.run_id),
    )

    with pytest.raises(KeyboardInterrupt):
        orch.run("test-interrupt-run")

    run_rec = get_run(conn, "test-interrupt-run")
    assert run_rec["status"] == RunStatus.failed.value
    assert "interrupted" in run_rec["error"]


def test_secret_masking_in_error_messages(conn: sqlite3.Connection, fake_settings: Settings) -> None:
    """Secret matching AIza... is masked as [REDACTED] in step_logs and runs.error."""
    upsert_article(conn, make_article("art-1", "Article 1"))
    fake_key = "AIza" + "X" * 35

    def leaking_research(rid: str, cands: list[Article]) -> ResearchResult:
        raise RuntimeError(f"Failed to authenticate with key: {fake_key}")

    orch = Orchestrator(
        conn=conn,
        settings=fake_settings,
        research_step=leaking_research,
        editorial_step=lambda r: make_editorial_package(r.run_id, r),
        validate_step=lambda r, p: make_validation_report(p.run_id),
    )

    result = orch.run("test-mask-run")
    assert fake_key not in (result.error_message or "")
    assert "[REDACTED]" in (result.error_message or "")

    run_rec = get_run(conn, "test-mask-run")
    assert fake_key not in run_rec["error"]
    assert "[REDACTED]" in run_rec["error"]

    logs = list_step_logs(conn, "test-mask-run")
    for log in logs:
        assert fake_key not in log.message


def test_atomic_artifact_writes_no_leftover_tmp_files(tmp_path: Path) -> None:
    """atomic_write_text leaves no .tmp files on success or simulated failure."""
    target_dir = tmp_path / "artifacts"
    final_file = target_dir / "report.json"

    # Success case
    atomic_write_text(final_file, '{"success": true}')
    assert final_file.exists()
    assert list(target_dir.glob(".*.tmp")) == []

    # Failure case
    def faulty_replace(src: Path, dst: Path) -> None:
        raise OSError("Simulated atomic replace failure")

    import os
    orig_replace = os.replace
    try:
        os.replace = faulty_replace  # type: ignore[assignment]
        with pytest.raises(OSError):
            atomic_write_text(final_file, '{"broken": true}')
        assert list(target_dir.glob(".*.tmp")) == []
    finally:
        os.replace = orig_replace


def test_duplicate_caller_run_id_raises_valueerror_no_db_row(conn: sqlite3.Connection, fake_settings: Settings) -> None:
    """Caller-supplied run_id that already exists in runs raises ValueError before any side effect."""
    from cyberpulse.storage import create_run
    create_run(conn, run_id="existing-run-id")

    orch = Orchestrator(
        conn=conn,
        settings=fake_settings,
        research_step=lambda r, c: make_research_result(r),
        editorial_step=lambda r: make_editorial_package(r.run_id, r),
        validate_step=lambda r, p: make_validation_report(p.run_id),
    )

    with pytest.raises(ValueError, match="already exists"):
        orch.run("existing-run-id")


def test_run_id_mismatch_ends_in_internal_error(conn: sqlite3.Connection, fake_settings: Settings) -> None:
    """Agent step returning a mismatched run_id ends in internal_error."""
    upsert_article(conn, make_article("art-1", "Article 1"))

    def mismatch_research(rid: str, cands: list[Article]) -> ResearchResult:
        return make_research_result("completely-different-run-id")

    orch = Orchestrator(
        conn=conn,
        settings=fake_settings,
        research_step=mismatch_research,
        editorial_step=lambda r: make_editorial_package(r.run_id, r),
        validate_step=lambda r, p: make_validation_report(p.run_id),
    )

    result = orch.run()
    assert result.status == RunStatus.failed
    assert result.failure_reason == FailureReason.internal_error
    assert "mismatch" in (result.error_message or "")


def test_degraded_helper_agrees_with_run_result(conn: sqlite3.Connection, fake_settings: Settings) -> None:
    """is_run_degraded helper agrees with RunResult.degraded on both normal and degraded runs."""
    upsert_article(conn, make_article("art-1", "Article 1"))

    # Normal run
    orch_normal = Orchestrator(
        conn=conn,
        settings=fake_settings,
        research_step=lambda r, c: make_research_result(r, status="ok", count=3),
        editorial_step=lambda r: make_editorial_package(r.run_id, r),
        validate_step=lambda r, p: make_validation_report(p.run_id, passed=True),
    )
    res_normal = orch_normal.run()
    assert res_normal.degraded is False
    assert is_run_degraded(conn, res_normal.run_id) is False

    # Degraded run
    orch_degraded = Orchestrator(
        conn=conn,
        settings=fake_settings,
        research_step=lambda r, c: make_research_result(r, status="insufficient", count=1, requested_count=5),
        editorial_step=lambda r: make_editorial_package(r.run_id, r),
        validate_step=lambda r, p: make_validation_report(p.run_id, passed=True),
        allow_insufficient=True,
    )
    res_degraded = orch_degraded.run()
    assert res_degraded.degraded is True
    assert is_run_degraded(conn, res_degraded.run_id) is True


def test_validate_step_argument_order(conn: sqlite3.Connection, fake_settings: Settings) -> None:
    """N1: validate_step is called with ResearchResult FIRST and EditorialPackage second."""
    upsert_article(conn, make_article("art-arg-order", "Article Arg Order"))
    recorded_args: list[tuple[Any, Any]] = []

    def recording_validator(first: Any, second: Any) -> ValidationReport:
        recorded_args.append((first, second))
        return make_validation_report(second.run_id, passed=True)

    orch = Orchestrator(
        conn=conn,
        settings=fake_settings,
        research_step=lambda r, c: make_research_result(r),
        editorial_step=lambda r: make_editorial_package(r.run_id, r),
        validate_step=recording_validator,
    )
    result = orch.run()
    assert result.status == RunStatus.awaiting_review
    assert len(recorded_args) == 1
    first_arg, second_arg = recorded_args[0]
    assert isinstance(first_arg, ResearchResult)
    assert isinstance(second_arg, EditorialPackage)


def test_foreign_keys_and_runs_row_order(conn: sqlite3.Connection, fake_settings: Settings) -> None:
    """N3: connect() enables foreign keys; child rows are written only after runs row exists;
    pending -> failed path with no candidates leaves a valid runs row.
    """
    # Verify foreign_keys pragma is enabled on connect()
    cursor = conn.execute("PRAGMA foreign_keys")
    assert cursor.fetchone()[0] == 1

    # 1. Test pending -> failed with no candidates leaves a valid runs row
    orch_empty = Orchestrator(
        conn=conn,
        settings=fake_settings,
        research_step=lambda r, c: make_research_result(r),
        editorial_step=lambda r: make_editorial_package(r.run_id, r),
        validate_step=lambda r, p: make_validation_report(p.run_id),
    )
    res_empty = orch_empty.run("run-no-cands-fk")
    assert res_empty.status == RunStatus.failed
    run_rec = get_run(conn, "run-no-cands-fk")
    assert run_rec is not None
    assert run_rec["status"] == RunStatus.failed.value
    # Step logs written without FK violation
    logs_empty = list_step_logs(conn, "run-no-cands-fk")
    assert len(logs_empty) >= 1

    # 2. Happy path run: child rows written after runs row exists
    upsert_article(conn, make_article("art-fk-1", "FK Article 1"))
    orch_full = Orchestrator(
        conn=conn,
        settings=fake_settings,
        research_step=lambda r, c: make_research_result(r),
        editorial_step=lambda r: make_editorial_package(r.run_id, r),
        validate_step=lambda r, p: make_validation_report(p.run_id),
    )
    res_full = orch_full.run("run-fk-success")
    assert res_full.status == RunStatus.awaiting_review

    # Verify all child tables reference run-fk-success
    art_count = conn.execute("SELECT count(*) FROM run_articles WHERE run_id = 'run-fk-success'").fetchone()[0]
    assert art_count > 0
    cnt_count = conn.execute("SELECT count(*) FROM contents WHERE run_id = 'run-fk-success'").fetchone()[0]
    assert cnt_count >= 3
    log_count = conn.execute("SELECT count(*) FROM step_logs WHERE run_id = 'run-fk-success'").fetchone()[0]
    assert log_count >= 4


def test_ui_data_list_runs_on_orchestrated_artifacts(conn: sqlite3.Connection, fake_settings: Settings) -> None:
    """N4: ui.data.list_runs pairs research, editorial, and validation paths for an orchestrated run."""
    for i in range(1, 4):
        upsert_article(conn, make_article(f"art-{i}", f"Article {i}"))

    orch = Orchestrator(
        conn=conn,
        settings=fake_settings,
        research_step=lambda r, c: make_research_result(r, status="ok", count=3),
        editorial_step=lambda r: make_editorial_package(r.run_id, r),
        validate_step=lambda r, p: make_validation_report(p.run_id, passed=True),
    )
    res = orch.run("run-ui-pairing-test")
    assert res.status == RunStatus.awaiting_review

    # Call ui.data.list_runs directly on output directory
    runs = list_runs(fake_settings.paths.output_dir)
    matching = [r for r in runs if r.run_id == "run-ui-pairing-test"]
    assert len(matching) == 1
    item = matching[0]
    assert item.has_research is True
    assert item.has_editorial is True
    assert item.has_validation is True
    assert item.research_path is not None and item.research_path.exists()
    assert item.editorial_path is not None and item.editorial_path.exists()
    assert item.preview_path is not None and item.preview_path.exists()
    assert item.validation_path is not None and item.validation_path.exists()

