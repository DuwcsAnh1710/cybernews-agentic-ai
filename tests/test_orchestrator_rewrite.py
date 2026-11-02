"""Tests for Orchestrator bounded rewrite loop, failure mappings, and persistence invariants."""

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import pytest

from cyberpulse.config import EditorialConfig, PathsConfig, Settings
from cyberpulse.feedback import FeedbackItem
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
    VideoScene,
    VideoScript,
)
from cyberpulse.orchestrator import (
    FailureReason,
    Orchestrator,
    RunResult,
)
from cyberpulse.storage import (
    connect,
    init_db,
    list_step_logs,
    load_editorial_package,
    load_validation_report,
    upsert_article,
)
from cyberpulse.validator import ValidationIssue, ValidationReport


def _make_settings(tmp_path: Path) -> Settings:
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
        editorial=EditorialConfig(
            podcast_target_words=600,
            video_max_total_sec=90,
            caption_platforms=["facebook", "youtube", "tiktok"],
            podcast_speakers=["Minh", "Lan"],
        ),
    )


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "test_orch_rewrite.db")
    init_db(c)
    yield c
    c.close()


def _make_article(art_id: str, title: str = "Threat Advisory") -> Article:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return Article(
        id=art_id,
        source_name="Source A",
        title=title,
        url=f"https://example.com/{art_id}",
        published_at=now,
        summary="Vulnerability advisory summary.",
        fetched_at=now,
        topics_json='["cybersecurity"]',
    )


def _make_research_result(run_id: str, count: int = 3) -> ResearchResult:
    items = []
    for i in range(1, count + 1):
        art = _make_article(f"{i:016x}", f"Advisory {i}")
        items.append(
            ResearchItem(
                rank=i,
                article=art,
                relevance_reason=f"Important security advisory {i}",
            )
        )
    return ResearchResult(
        run_id=run_id,
        status="ok",
        requested_count=count,
        items=items,
        notes="Standard research completed",
    )


def _make_package(run_id: str, research: ResearchResult, tag: str = "initial") -> EditorialPackage:
    art_ids = [item.article.id for item in research.items]
    art_id = art_ids[0] if art_ids else "0000000000000001"
    sections = [
        DigestSection(
            heading=f"Heading {i+1} ({tag})",
            body=CitedText(text=f"Body {i+1} ({tag})", cited_article_ids=[art_ids[i]]),
        )
        for i in range(len(research.items))
    ]
    return EditorialPackage(
        run_id=run_id,
        language="vi",
        digest=Digest(title=f"Digest ({tag})", intro="Intro", sections=sections),
        podcast=PodcastScript(
            title=f"Podcast ({tag})",
            segments=[PodcastSegment(speaker="Minh", line=CitedText(text=f"Line ({tag})", cited_article_ids=[art_id]))],
        ),
        video=VideoScript(
            title=f"Video ({tag})",
            scenes=[VideoScene(scene_no=1, on_screen_text=f"Overlay ({tag})", narration=CitedText(text=f"Narr ({tag})", cited_article_ids=[art_id]), duration_sec=10.0)],
        ),
        captions=[
            Caption(platform="facebook", text=f"Cap ({tag})", hashtags=["#a", "#b", "#c"], cited_article_ids=[art_id])
        ],
    )


def _make_report(run_id: str, passed: bool, errors: list[ValidationIssue] | None = None) -> ValidationReport:
    return ValidationReport(
        run_id=run_id,
        passed=passed,
        errors=errors or [],
        warnings=[],
        checks_run=["layer1_structural", "layer2_source_consistency"],
        llm_review="skipped",
        scope_note="Test validation",
    )


class LLMTransientError(Exception):
    pass


class LLMError(Exception):
    pass


class EditorialError(Exception):
    pass


def test_rewrite_success_one_attempt_awaiting_review(conn: sqlite3.Connection, tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    for i in range(1, 4):
        upsert_article(conn, _make_article(f"{i:016x}"))

    calls = {"editorial": 0, "rewrite": 0, "validate": 0}

    def research_step(rid: str, cands: list[Article]) -> ResearchResult:
        return _make_research_result(rid)

    def editorial_step(res: ResearchResult) -> EditorialPackage:
        calls["editorial"] += 1
        return _make_package(res.run_id, res, tag="attempt0")

    def rewrite_step(res: ResearchResult, prev: EditorialPackage, fb: list[FeedbackItem]) -> EditorialPackage:
        calls["rewrite"] += 1
        assert len(fb) > 0
        return _make_package(res.run_id, res, tag="rewritten1")

    def validate_step(res: ResearchResult, pkg: EditorialPackage) -> ValidationReport:
        calls["validate"] += 1
        if calls["validate"] == 1:
            # First validation fails
            return _make_report(
                pkg.run_id,
                passed=False,
                errors=[ValidationIssue(code="E_NUMBER_UNSUPPORTED", severity="error", path="podcast.segments[0].line.text", message="Num", evidence="10")],
            )
        # Second validation passes
        return _make_report(pkg.run_id, passed=True)

    orch = Orchestrator(
        conn=conn,
        settings=settings,
        research_step=research_step,
        editorial_step=editorial_step,
        validate_step=validate_step,
        rewrite_step=rewrite_step,
        max_rewrites=2,
    )

    result = orch.run()

    assert result.status == RunStatus.awaiting_review
    assert result.failure_reason is None
    assert result.rewrite_attempts == 1
    assert result.stop_reason is None
    assert calls["editorial"] == 1
    assert calls["rewrite"] == 1
    assert calls["validate"] == 2
    assert result.step_attempts == {"research": 1, "editorial": 1, "validate": 2, "rewrite": 1}

    # Verify transitions recorded
    logs = list_step_logs(conn, result.run_id)
    steps = [l.step for l in logs]
    assert "rewrite" in steps


def test_rewrite_budget_exhausted_fails_after_max_rewrites(conn: sqlite3.Connection, tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    for i in range(1, 4):
        upsert_article(conn, _make_article(f"{i:016x}"))

    calls = {"rewrite": 0}

    def rewrite_step(res: ResearchResult, prev: EditorialPackage, fb: list[FeedbackItem]) -> EditorialPackage:
        calls["rewrite"] += 1
        return _make_package(res.run_id, res, tag=f"attempt_{calls['rewrite']}")

    def validate_step(res: ResearchResult, pkg: EditorialPackage) -> ValidationReport:
        # Changes error slightly each time so signature changes, but never passes
        err_code = "E_NUMBER_UNSUPPORTED" if calls["rewrite"] % 2 == 0 else "E_CAPTION_LENGTH"
        return _make_report(
            pkg.run_id,
            passed=False,
            errors=[ValidationIssue(code=err_code, severity="error", path=f"captions[{calls['rewrite']}].text", message="Err", evidence=f"{calls['rewrite']}")],
        )

    orch = Orchestrator(
        conn=conn,
        settings=settings,
        research_step=lambda rid, cands: _make_research_result(rid),
        editorial_step=lambda res: _make_package(res.run_id, res, tag="init"),
        validate_step=validate_step,
        rewrite_step=rewrite_step,
        max_rewrites=2,
    )

    result = orch.run()

    assert result.status == RunStatus.failed
    assert result.failure_reason == FailureReason.validation_failed
    assert result.rewrite_attempts == 2
    assert result.stop_reason == "budget_exhausted"
    assert calls["rewrite"] == 2


def test_rewrite_no_progress_stops_early(conn: sqlite3.Connection, tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    for i in range(1, 4):
        upsert_article(conn, _make_article(f"{i:016x}"))

    calls = {"rewrite": 0}

    def rewrite_step(res: ResearchResult, prev: EditorialPackage, fb: list[FeedbackItem]) -> EditorialPackage:
        calls["rewrite"] += 1
        return _make_package(res.run_id, res, tag="stuck")

    def validate_step(res: ResearchResult, pkg: EditorialPackage) -> ValidationReport:
        # Same error every time -> identical feedback signature
        return _make_report(
            pkg.run_id,
            passed=False,
            errors=[ValidationIssue(code="E_NUMBER_UNSUPPORTED", severity="error", path="podcast.segments[0].line.text", message="Num 10", evidence="10")],
        )

    orch = Orchestrator(
        conn=conn,
        settings=settings,
        research_step=lambda rid, cands: _make_research_result(rid),
        editorial_step=lambda res: _make_package(res.run_id, res),
        validate_step=validate_step,
        rewrite_step=rewrite_step,
        max_rewrites=3,
    )

    result = orch.run()

    assert result.status == RunStatus.failed
    assert result.failure_reason == FailureReason.validation_failed
    # Stops after attempt 1 because attempt 1 signature equals attempt 0 signature!
    assert result.rewrite_attempts == 1
    assert result.stop_reason == "no_progress"
    assert calls["rewrite"] == 1


def test_rewrite_non_rewritable_mixed_case_zero_rewrites(conn: sqlite3.Connection, tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    for i in range(1, 4):
        upsert_article(conn, _make_article(f"{i:016x}"))

    calls = {"rewrite": 0}

    def validate_step(res: ResearchResult, pkg: EditorialPackage) -> ValidationReport:
        return _make_report(
            pkg.run_id,
            passed=False,
            errors=[
                ValidationIssue(code="E_NUMBER_UNSUPPORTED", severity="error", path="podcast.segments[0].line.text", message="Num", evidence="10"),
                ValidationIssue(code="E_CITE_UNKNOWN", severity="error", path="digest.sections[0]", message="Unknown cite", evidence="fake_id"),
            ],
        )

    orch = Orchestrator(
        conn=conn,
        settings=settings,
        research_step=lambda rid, cands: _make_research_result(rid),
        editorial_step=lambda res: _make_package(res.run_id, res),
        validate_step=validate_step,
        rewrite_step=lambda r, p, fb: calls.update({"rewrite": calls["rewrite"] + 1}) or p,
        max_rewrites=2,
    )

    result = orch.run()

    assert result.status == RunStatus.failed
    assert result.failure_reason == FailureReason.validation_failed
    assert result.rewrite_attempts == 0
    assert result.stop_reason == "non_rewritable"
    assert calls["rewrite"] == 0


def test_rewrite_empty_feedback_stops_non_rewritable(conn: sqlite3.Connection, tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    for i in range(1, 4):
        upsert_article(conn, _make_article(f"{i:016x}"))

    calls = {"rewrite": 0}

    def validate_step(res: ResearchResult, pkg: EditorialPackage) -> ValidationReport:
        # Only non-rewritable code E_RUN_ID_MISMATCH present
        return _make_report(
            pkg.run_id,
            passed=False,
            errors=[ValidationIssue(code="E_RUN_ID_MISMATCH", severity="error", path="run_id", message="Mismatch", evidence="")],
        )

    orch = Orchestrator(
        conn=conn,
        settings=settings,
        research_step=lambda rid, cands: _make_research_result(rid),
        editorial_step=lambda res: _make_package(res.run_id, res),
        validate_step=validate_step,
        rewrite_step=lambda r, p, fb: calls.update({"rewrite": calls["rewrite"] + 1}) or p,
        max_rewrites=2,
    )

    result = orch.run()
    assert result.status == RunStatus.failed
    assert result.stop_reason == "non_rewritable"
    assert calls["rewrite"] == 0


def test_rewrite_best_attempt_persistence(conn: sqlite3.Connection, tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    for i in range(1, 4):
        upsert_article(conn, _make_article(f"{i:016x}"))

    calls = {"rewrite": 0}

    def rewrite_step(res: ResearchResult, prev: EditorialPackage, fb: list[FeedbackItem]) -> EditorialPackage:
        calls["rewrite"] += 1
        return _make_package(res.run_id, res, tag=f"attempt_{calls['rewrite']}")

    def validate_step(res: ResearchResult, pkg: EditorialPackage) -> ValidationReport:
        if calls["rewrite"] == 0:
            # Initial: 2 errors
            return _make_report(pkg.run_id, passed=False, errors=[
                ValidationIssue(code="E_NUMBER_UNSUPPORTED", severity="error", path="p1", message="m1", evidence="1"),
                ValidationIssue(code="E_NUMBER_UNSUPPORTED", severity="error", path="p2", message="m2", evidence="2"),
            ])
        elif calls["rewrite"] == 1:
            # Attempt 1: 1 error (BEST)
            return _make_report(pkg.run_id, passed=False, errors=[
                ValidationIssue(code="E_CAPTION_LENGTH", severity="error", path="c1", message="m", evidence="e")
            ])
        else:
            # Attempt 2: 3 errors (worse)
            return _make_report(pkg.run_id, passed=False, errors=[
                ValidationIssue(code="E_NUMBER_UNSUPPORTED", severity="error", path="p1", message="m", evidence="1"),
                ValidationIssue(code="E_NUMBER_UNSUPPORTED", severity="error", path="p2", message="m", evidence="2"),
                ValidationIssue(code="E_NUMBER_UNSUPPORTED", severity="error", path="p3", message="m", evidence="3"),
            ])

    orch = Orchestrator(
        conn=conn,
        settings=settings,
        research_step=lambda rid, cands: _make_research_result(rid),
        editorial_step=lambda res: _make_package(res.run_id, res, tag="initial"),
        validate_step=validate_step,
        rewrite_step=rewrite_step,
        max_rewrites=2,
    )

    result = orch.run()

    # The persisted editorial package MUST be attempt_1 (the best attempt)
    loaded_pkg = load_editorial_package(conn, result.run_id)
    assert loaded_pkg is not None
    assert "attempt_1" in loaded_pkg.digest.title

    # File on disk must also match attempt_1
    disk_json = result.artifacts["editorial_json"].read_text(encoding="utf-8")
    assert "attempt_1" in disk_json


def test_rewrite_all_rewrites_worse_keeps_initial(conn: sqlite3.Connection, tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    for i in range(1, 4):
        upsert_article(conn, _make_article(f"{i:016x}"))

    calls = {"rewrite": 0}

    def validate_step(res: ResearchResult, pkg: EditorialPackage) -> ValidationReport:
        if calls["rewrite"] == 0:
            # Initial: 1 error (BEST)
            return _make_report(pkg.run_id, passed=False, errors=[
                ValidationIssue(code="E_CAPTION_LENGTH", severity="error", path="c1", message="m", evidence="e")
            ])
        else:
            # Rewrites: 2 errors
            return _make_report(pkg.run_id, passed=False, errors=[
                ValidationIssue(code="E_NUMBER_UNSUPPORTED", severity="error", path=f"p{calls['rewrite']}", message="m", evidence="1"),
                ValidationIssue(code="E_NUMBER_UNSUPPORTED", severity="error", path="p2", message="m", evidence="2"),
            ])

    orch = Orchestrator(
        conn=conn,
        settings=settings,
        research_step=lambda rid, cands: _make_research_result(rid),
        editorial_step=lambda res: _make_package(res.run_id, res, tag="initial"),
        validate_step=validate_step,
        rewrite_step=lambda r, p, fb: calls.update({"rewrite": calls["rewrite"] + 1}) or _make_package(r.run_id, r, tag=f"rewrite_{calls['rewrite']}"),
        max_rewrites=2,
    )

    result = orch.run()
    loaded_pkg = load_editorial_package(conn, result.run_id)
    assert loaded_pkg is not None
    assert "initial" in loaded_pkg.digest.title


def test_rewrite_single_finalize_path_consistency(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """A7.v / C12: On any stop path, DB contents, atomic file, and result point to same package."""
    settings = _make_settings(tmp_path)
    for i in range(1, 4):
        upsert_article(conn, _make_article(f"{i:016x}"))

    def validate_step(res: ResearchResult, pkg: EditorialPackage) -> ValidationReport:
        return _make_report(pkg.run_id, passed=True)

    orch = Orchestrator(
        conn=conn,
        settings=settings,
        research_step=lambda rid, cands: _make_research_result(rid),
        editorial_step=lambda res: _make_package(res.run_id, res, tag="consistent"),
        validate_step=validate_step,
        max_rewrites=2,
    )

    result = orch.run()

    loaded_pkg = load_editorial_package(conn, result.run_id)
    assert loaded_pkg is not None
    db_json = loaded_pkg.model_dump_json()
    file_json = EditorialPackage.model_validate_json(result.artifacts["editorial_json"].read_text(encoding="utf-8")).model_dump_json()

    assert db_json == file_json


def test_rewrite_validation_failed_message_codes_only(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """C2 / C12: validation_failed message contains only codes and counts, never evidence text."""
    settings = _make_settings(tmp_path)
    for i in range(1, 4):
        upsert_article(conn, _make_article(f"{i:016x}"))

    secret_evidence = "SECRETTOKEN123"

    def validate_step(res: ResearchResult, pkg: EditorialPackage) -> ValidationReport:
        return _make_report(
            pkg.run_id,
            passed=False,
            errors=[
                ValidationIssue(code="E_NUMBER_UNSUPPORTED", severity="error", path="path1", message="Secret leak", evidence=secret_evidence),
                ValidationIssue(code="E_CAPTION_LENGTH", severity="error", path="path2", message="Secret leak 2", evidence=secret_evidence),
            ],
        )

    orch = Orchestrator(
        conn=conn,
        settings=settings,
        research_step=lambda rid, cands: _make_research_result(rid),
        editorial_step=lambda res: _make_package(res.run_id, res),
        validate_step=validate_step,
        rewrite_step=lambda r, p, fb: _make_package(r.run_id, r, tag="rw"),
        max_rewrites=1,
    )

    result = orch.run()
    assert result.status == RunStatus.failed
    assert result.failure_reason == FailureReason.validation_failed
    assert secret_evidence not in (result.error_message or "")
    assert "E_CAPTION_LENGTH=1, E_NUMBER_UNSUPPORTED=1" in (result.error_message or "")


def test_rewrite_disabled_preserves_t51a_behavior(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """C3: rewrite_step=None or max_rewrites=0 behaves identically to T5.1a."""
    settings = _make_settings(tmp_path)
    for i in range(1, 4):
        upsert_article(conn, _make_article(f"{i:016x}"))

    def validate_step(res: ResearchResult, pkg: EditorialPackage) -> ValidationReport:
        return _make_report(
            pkg.run_id,
            passed=False,
            errors=[ValidationIssue(code="E_SECTION_COUNT", severity="error", path="digest.sections", message="count", evidence="1")],
        )

    orch = Orchestrator(
        conn=conn,
        settings=settings,
        research_step=lambda rid, cands: _make_research_result(rid),
        editorial_step=lambda res: _make_package(res.run_id, res),
        validate_step=validate_step,
        rewrite_step=None,
        max_rewrites=2,
    )

    result = orch.run()
    assert result.status == RunStatus.failed
    assert result.failure_reason == FailureReason.validation_failed
    assert result.error_message == "Validation checks failed"
    assert result.rewrite_attempts == 0
    assert result.stop_reason is None
    assert result.step_attempts == {"research": 1, "editorial": 1, "validate": 1}

    logs = list_step_logs(conn, result.run_id)
    assert not any(l.step == "rewrite" for l in logs)


def test_rewrite_transient_llm_error_retried_with_backoff(conn: sqlite3.Connection, tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    for i in range(1, 4):
        upsert_article(conn, _make_article(f"{i:016x}"))

    delays = []
    rw_calls = 0

    def faulty_rewrite(res: ResearchResult, prev: EditorialPackage, fb: list[FeedbackItem]) -> EditorialPackage:
        nonlocal rw_calls
        rw_calls += 1
        if rw_calls == 1:
            raise LLMTransientError("Transient 503")
        return _make_package(res.run_id, res, tag="fixed")

    def validate_step(res: ResearchResult, pkg: EditorialPackage) -> ValidationReport:
        if rw_calls == 0:
            return _make_report(pkg.run_id, passed=False, errors=[ValidationIssue(code="E_NUMBER_UNSUPPORTED", severity="error", path="p", message="m", evidence="e")])
        return _make_report(pkg.run_id, passed=True)

    orch = Orchestrator(
        conn=conn,
        settings=settings,
        research_step=lambda rid, cands: _make_research_result(rid),
        editorial_step=lambda res: _make_package(res.run_id, res),
        validate_step=validate_step,
        rewrite_step=faulty_rewrite,
        sleep_fn=delays.append,
        max_rewrites=2,
        max_step_attempts=2,
    )

    result = orch.run()
    assert result.status == RunStatus.awaiting_review
    assert len(delays) == 1
    assert delays[0] == 5.0
    assert rw_calls == 2


def test_rewrite_phase_llm_error_maps_to_validation_failed(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """C2 / C10: LLM error in rewrite maps to validation_failed with stop_reason llm_error."""
    settings = _make_settings(tmp_path)
    for i in range(1, 4):
        upsert_article(conn, _make_article(f"{i:016x}"))

    def faulty_rewrite(res: ResearchResult, prev: EditorialPackage, fb: list[FeedbackItem]) -> EditorialPackage:
        raise LLMError("Non-retryable model error")

    def validate_step(res: ResearchResult, pkg: EditorialPackage) -> ValidationReport:
        return _make_report(pkg.run_id, passed=False, errors=[ValidationIssue(code="E_NUMBER_UNSUPPORTED", severity="error", path="p", message="m", evidence="e")])

    orch = Orchestrator(
        conn=conn,
        settings=settings,
        research_step=lambda rid, cands: _make_research_result(rid),
        editorial_step=lambda res: _make_package(res.run_id, res),
        validate_step=validate_step,
        rewrite_step=faulty_rewrite,
        max_rewrites=2,
    )

    result = orch.run()
    assert result.status == RunStatus.failed
    assert result.failure_reason == FailureReason.validation_failed
    assert result.stop_reason == "llm_error"
    assert "E_NUMBER_UNSUPPORTED=1" in (result.error_message or "")


def test_rewrite_phase_editorial_error_maps_to_validation_failed(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """C2: EditorialError in rewrite maps to validation_failed with stop_reason editorial_error."""
    settings = _make_settings(tmp_path)
    for i in range(1, 4):
        upsert_article(conn, _make_article(f"{i:016x}"))

    def faulty_rewrite(res: ResearchResult, prev: EditorialPackage, fb: list[FeedbackItem]) -> EditorialPackage:
        raise EditorialError("Format or ref error in rewrite")

    def validate_step(res: ResearchResult, pkg: EditorialPackage) -> ValidationReport:
        return _make_report(pkg.run_id, passed=False, errors=[ValidationIssue(code="E_NUMBER_UNSUPPORTED", severity="error", path="p", message="m", evidence="e")])

    orch = Orchestrator(
        conn=conn,
        settings=settings,
        research_step=lambda rid, cands: _make_research_result(rid),
        editorial_step=lambda res: _make_package(res.run_id, res),
        validate_step=validate_step,
        rewrite_step=faulty_rewrite,
        max_rewrites=2,
    )

    result = orch.run()
    assert result.status == RunStatus.failed
    assert result.failure_reason == FailureReason.validation_failed
    assert result.stop_reason == "editorial_error"


def test_rewrite_unexpected_exception_internal_error(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """C2: Unexpected exception in rewrite_step maps to internal_error."""
    settings = _make_settings(tmp_path)
    for i in range(1, 4):
        upsert_article(conn, _make_article(f"{i:016x}"))

    def faulty_rewrite(res: ResearchResult, prev: EditorialPackage, fb: list[FeedbackItem]) -> EditorialPackage:
        raise RuntimeError("Unexpected memory fault")

    def validate_step(res: ResearchResult, pkg: EditorialPackage) -> ValidationReport:
        return _make_report(pkg.run_id, passed=False, errors=[ValidationIssue(code="E_NUMBER_UNSUPPORTED", severity="error", path="p", message="m", evidence="e")])

    orch = Orchestrator(
        conn=conn,
        settings=settings,
        research_step=lambda rid, cands: _make_research_result(rid),
        editorial_step=lambda res: _make_package(res.run_id, res),
        validate_step=validate_step,
        rewrite_step=faulty_rewrite,
        max_rewrites=2,
    )

    result = orch.run()
    assert result.status == RunStatus.failed
    assert result.failure_reason == FailureReason.internal_error
    assert result.stop_reason == "internal_error"


def test_rewrite_wrong_run_id_internal_error(conn: sqlite3.Connection, tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    for i in range(1, 4):
        upsert_article(conn, _make_article(f"{i:016x}"))

    def wrong_id_rewrite(res: ResearchResult, prev: EditorialPackage, fb: list[FeedbackItem]) -> EditorialPackage:
        pkg = _make_package("wrong-run-id", res)
        return pkg

    def validate_step(res: ResearchResult, pkg: EditorialPackage) -> ValidationReport:
        return _make_report(pkg.run_id, passed=False, errors=[ValidationIssue(code="E_NUMBER_UNSUPPORTED", severity="error", path="p", message="m", evidence="e")])

    orch = Orchestrator(
        conn=conn,
        settings=settings,
        research_step=lambda rid, cands: _make_research_result(rid),
        editorial_step=lambda res: _make_package(res.run_id, res),
        validate_step=validate_step,
        rewrite_step=wrong_id_rewrite,
        max_rewrites=2,
    )

    result = orch.run()
    assert result.status == RunStatus.failed
    assert result.failure_reason == FailureReason.internal_error


def test_rewrite_step_log_format(conn: sqlite3.Connection, tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    for i in range(1, 4):
        upsert_article(conn, _make_article(f"{i:016x}"))

    def validate_step(res: ResearchResult, pkg: EditorialPackage) -> ValidationReport:
        return _make_report(pkg.run_id, passed=False, errors=[ValidationIssue(code="E_NUMBER_UNSUPPORTED", severity="error", path="p", message="m", evidence="e")])

    orch = Orchestrator(
        conn=conn,
        settings=settings,
        research_step=lambda rid, cands: _make_research_result(rid),
        editorial_step=lambda res: _make_package(res.run_id, res),
        validate_step=validate_step,
        rewrite_step=lambda r, p, fb: _make_package(r.run_id, r, tag="rw"),
        max_rewrites=1,
    )

    result = orch.run()
    logs = list_step_logs(conn, result.run_id)
    rewrite_logs = [l for l in logs if l.step == "rewrite" and l.status == "failed"]
    assert len(rewrite_logs) == 1
    # Check format: rewrite i/N: errors a->b; signature=<first 8 hex>; stop=<reason or none>
    msg = rewrite_logs[0].message
    assert msg.startswith("rewrite 1/1: errors 1->1; signature=")
    assert "stop=" in msg


def test_rewrite_clamped_bounds(conn: sqlite3.Connection, tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    orch_neg = Orchestrator(
        conn=conn,
        settings=settings,
        research_step=lambda r, c: _make_research_result(r),
        editorial_step=lambda r: _make_package(r.run_id, r),
        validate_step=lambda r, p: _make_report(p.run_id, True),
        max_rewrites=-5,
    )
    assert orch_neg.max_rewrites == 0

    orch_high = Orchestrator(
        conn=conn,
        settings=settings,
        research_step=lambda r, c: _make_research_result(r),
        editorial_step=lambda r: _make_package(r.run_id, r),
        validate_step=lambda r, p: _make_report(p.run_id, True),
        max_rewrites=10,
    )
    assert orch_high.max_rewrites == 3
