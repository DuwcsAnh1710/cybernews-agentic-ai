"""Hermetic tests for orchestrator-related storage functions in storage.py."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import pytest

from cyberpulse.models import (
    Article,
    EditorialPackage,
    ResearchItem,
    ResearchResult,
    RunStatus,
    StepLog,
)
from cyberpulse.storage import (
    add_step_log,
    connect,
    create_run,
    get_run,
    init_db,
    list_step_logs,
    load_content,
    load_editorial_package,
    load_research_result,
    load_validation_report,
    save_content,
    save_editorial_package,
    save_research_result,
    save_validation_report,
    update_run_status,
)
from cyberpulse.validator import ValidationIssue, ValidationReport


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    db_file = tmp_path / "test_storage_orchestrator.db"
    c = connect(db_file)
    init_db(c)
    yield c
    c.close()


def test_update_run_status_accepts_enum(conn: sqlite3.Connection) -> None:
    run_id = create_run(conn)
    rec_before = get_run(conn, run_id)
    assert rec_before is not None
    assert rec_before["status"] == RunStatus.pending.value

    update_run_status(conn, run_id, RunStatus.researching)
    rec_after = get_run(conn, run_id)
    assert rec_after is not None
    assert rec_after["status"] == RunStatus.researching.value
    assert rec_after["error"] is None

    update_run_status(conn, run_id, RunStatus.failed, error="Some error")
    rec_failed = get_run(conn, run_id)
    assert rec_failed is not None
    assert rec_failed["status"] == RunStatus.failed.value
    assert rec_failed["error"] == "Some error"


def test_update_run_status_rejects_string(conn: sqlite3.Connection) -> None:
    run_id = create_run(conn)
    with pytest.raises(TypeError, match="status must be a RunStatus enum member"):
        update_run_status(conn, run_id, "researching")  # type: ignore[arg-type]


def test_update_run_status_rejects_other_types(conn: sqlite3.Connection) -> None:
    run_id = create_run(conn)
    with pytest.raises(TypeError, match="status must be a RunStatus enum member"):
        update_run_status(conn, run_id, 123)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="status must be a RunStatus enum member"):
        update_run_status(conn, run_id, None)  # type: ignore[arg-type]


def test_update_run_status_unknown_id(conn: sqlite3.Connection) -> None:
    with pytest.raises(KeyError, match="Run 'nonexistent-id' not found"):
        update_run_status(conn, "nonexistent-id", RunStatus.failed)


def test_save_and_load_content_roundtrip(conn: sqlite3.Connection) -> None:
    run_id = create_run(conn)
    assert load_content(conn, run_id, "custom_kind") is None

    save_content(conn, run_id, "custom_kind", '{"key": "value"}')
    loaded = load_content(conn, run_id, "custom_kind")
    assert loaded == '{"key": "value"}'

    # Update on conflict
    save_content(conn, run_id, "custom_kind", '{"key": "updated"}')
    loaded2 = load_content(conn, run_id, "custom_kind")
    assert loaded2 == '{"key": "updated"}'


def test_save_and_load_validation_report(conn: sqlite3.Connection) -> None:
    run_id = create_run(conn)
    assert load_validation_report(conn, run_id) is None

    report = ValidationReport(
        run_id=run_id,
        passed=False,
        llm_review="skipped",
        errors=[
            ValidationIssue(
                code="E_SECTION_COUNT",
                severity="error",
                path="digest.sections",
                message="Expected 3-5 sections, found 2",
            )
        ],
        warnings=[
            ValidationIssue(
                code="W_TERM_UNSUPPORTED",
                severity="warning",
                path="digest.sections[0]",
                message="Unsupported technical term",
            )
        ],
        scope_note="Full validation",
    )

    save_validation_report(conn, run_id, report)
    loaded = load_validation_report(conn, run_id)
    assert loaded is not None
    assert loaded.passed is False
    assert len(loaded.errors) == 1
    assert loaded.errors[0].code == "E_SECTION_COUNT"
    assert len(loaded.warnings) == 1
    assert loaded.warnings[0].code == "W_TERM_UNSUPPORTED"
