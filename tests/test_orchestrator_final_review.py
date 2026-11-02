"""Unit tests for Orchestrator Layer 3 final_review_step integration (T5.2)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cyberpulse.config import load_settings
from cyberpulse.models import EditorialPackage, ResearchResult, RunStatus
from cyberpulse.orchestrator import FailureReason, Orchestrator
from cyberpulse.storage import connect, init_db, load_validation_report
from cyberpulse.validator import ValidationIssue, ValidationReport
from tests.test_orchestrator import (
    make_article,
    make_editorial_package,
    make_research_result,
    make_validation_report,
)


def test_orchestrator_final_review_default_none_matches_t51b(tmp_path: Path) -> None:
    """When final_review_step is None, orchestrator behavior is identical to T5.1b."""
    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    init_db(conn)

    settings = load_settings(Path("config/settings.toml"))
    settings.paths.output_dir = tmp_path / "output"

    rid = "run-20261101T120000Z-11111111"
    research = make_research_result(rid)
    pkg = make_editorial_package(rid, research)
    report = make_validation_report(rid, passed=True)

    orch = Orchestrator(
        conn=conn,
        settings=settings,
        research_step=lambda r_id, cands: research,
        editorial_step=lambda res: pkg,
        validate_step=lambda res, p: report,
        candidate_fetcher=lambda c, s: [item.article for item in research.items],
        final_review_step=None,
    )

    res = orch.run(run_id=rid)
    assert res.status == RunStatus.awaiting_review
    assert "validation" in res.artifacts
    conn.close()


def test_orchestrator_final_review_called_only_when_layer12_passed(tmp_path: Path) -> None:
    """When Layer 1-2 passes, final_review_step is called once and its report is finalized."""
    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    init_db(conn)

    settings = load_settings(Path("config/settings.toml"))
    settings.paths.output_dir = tmp_path / "output"

    rid = "run-20261101T120000Z-22222222"
    research = make_research_result(rid)
    pkg = make_editorial_package(rid, research)
    l12_report = make_validation_report(rid, passed=True)

    calls = []

    def mock_final_review(res: ResearchResult, p: EditorialPackage, rep: Any) -> Any:
        calls.append((res.run_id, p.run_id))
        return ValidationReport(
            run_id=p.run_id,
            passed=True,
            errors=[],
            warnings=[
                ValidationIssue(
                    code="W_CLAIM_ADVISORY",
                    severity="warning",
                    path="digest.sections[0].body.text",
                    message="Claim advisory",
                    evidence="text",
                )
            ],
            checks_run=list(rep.checks_run) + ["layer3_llm_claim_review (findings=1, dropped=0)"],
            llm_review="done",
        )

    orch = Orchestrator(
        conn=conn,
        settings=settings,
        research_step=lambda r_id, cands: research,
        editorial_step=lambda res: pkg,
        validate_step=lambda res, p: l12_report,
        candidate_fetcher=lambda c, s: [item.article for item in research.items],
        final_review_step=mock_final_review,
    )

    res = orch.run(run_id=rid)
    assert res.status == RunStatus.awaiting_review
    assert len(calls) == 1
    assert calls[0] == (rid, rid)

    # Invariant check: disk, DB, and result report match
    val_file = res.artifacts["validation"]
    disk_data = json.loads(val_file.read_text(encoding="utf-8"))
    assert disk_data["llm_review"] == "done"
    assert len(disk_data["warnings"]) == 1
    assert disk_data["warnings"][0]["code"] == "W_CLAIM_ADVISORY"

    db_report = load_validation_report(conn, rid)
    assert db_report is not None
    assert db_report.llm_review == "done"
    assert len(db_report.warnings) == 1

    conn.close()


def test_orchestrator_final_review_not_called_when_validation_fails(tmp_path: Path) -> None:
    """When Layer 1-2 validation fails (and rewrites fail), final_review_step is NEVER called."""
    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    init_db(conn)

    settings = load_settings(Path("config/settings.toml"))
    settings.paths.output_dir = tmp_path / "output"

    rid = "run-20261101T120000Z-33333333"
    research = make_research_result(rid)
    pkg = make_editorial_package(rid, research)
    failing_report = make_validation_report(rid, passed=False)

    calls = []

    def mock_final_review(res: ResearchResult, p: EditorialPackage, rep: Any) -> Any:
        calls.append("called")
        return rep

    orch = Orchestrator(
        conn=conn,
        settings=settings,
        research_step=lambda r_id, cands: research,
        editorial_step=lambda res: pkg,
        validate_step=lambda res, p: failing_report,
        candidate_fetcher=lambda c, s: [item.article for item in research.items],
        max_rewrites=0,
        final_review_step=mock_final_review,
    )

    res = orch.run(run_id=rid)
    assert res.status == RunStatus.failed
    assert res.failure_reason == FailureReason.validation_failed
    assert len(calls) == 0  # Gated: never called when Layer 1-2 failed
    conn.close()


def test_orchestrator_final_review_failure_does_not_fail_run(tmp_path: Path) -> None:
    """When final_review_step encounters an exception, the run does not fail (advisory only)."""
    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    init_db(conn)

    settings = load_settings(Path("config/settings.toml"))
    settings.paths.output_dir = tmp_path / "output"

    rid = "run-20261101T120000Z-44444444"
    research = make_research_result(rid)
    pkg = make_editorial_package(rid, research)
    l12_report = make_validation_report(rid, passed=True)

    def failing_final_review(res: ResearchResult, p: EditorialPackage, rep: Any) -> Any:
        raise RuntimeError("LLM service unavailable during final review")

    orch = Orchestrator(
        conn=conn,
        settings=settings,
        research_step=lambda r_id, cands: research,
        editorial_step=lambda res: pkg,
        validate_step=lambda res, p: l12_report,
        candidate_fetcher=lambda c, s: [item.article for item in research.items],
        final_review_step=failing_final_review,
    )

    res = orch.run(run_id=rid)
    assert res.status == RunStatus.awaiting_review  # Remains successful
    conn.close()
