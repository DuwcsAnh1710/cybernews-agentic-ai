"""Unit tests for early failed runs DB query in ui/data.py (T5.2)."""

from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from cyberpulse.storage import connect, create_run, init_db, update_run_status
from cyberpulse.models import RunStatus
from cyberpulse.ui.data import list_early_failed_runs


def test_list_early_failed_runs_missing_db(tmp_path: Path) -> None:
    """Missing DB file returns [] without error."""
    missing_db = tmp_path / "nonexistent" / "cyberpulse.db"
    runs = list_early_failed_runs(missing_db)
    assert runs == []


def test_list_early_failed_runs_empty_db(tmp_path: Path) -> None:
    """Empty runs table returns []."""
    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    init_db(conn)
    conn.close()

    runs = list_early_failed_runs(db_path)
    assert runs == []


def test_list_early_failed_runs_filters_existing_file_runs(tmp_path: Path) -> None:
    """Runs whose run_id is in file_run_ids are filtered out."""
    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    init_db(conn)

    # 1 run with files, 1 early failed run without files
    rid_with_files = "run-20261101T100000Z-file0001"
    rid_early_fail = "run-20261101T100500Z-fail0002"

    create_run(conn, run_id=rid_with_files)
    update_run_status(conn, rid_with_files, RunStatus.failed, error="[validation_failed] Checks failed")

    create_run(conn, run_id=rid_early_fail)
    update_run_status(conn, rid_early_fail, RunStatus.failed, error="[llm_error] 503 Service Unavailable")

    conn.close()

    # Pass rid_with_files as already having files
    runs = list_early_failed_runs(db_path, file_run_ids={rid_with_files})
    assert len(runs) == 1
    assert runs[0]["Run ID"] == rid_early_fail
    assert runs[0]["Lý do"] == "llm_error"
    assert "503 Service Unavailable" in runs[0]["Lỗi"]


def test_list_early_failed_runs_all_string_columns(tmp_path: Path) -> None:
    """All dictionary values must strictly be str to avoid Arrow mixed-type crashes."""
    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    init_db(conn)

    rid = "run-20261101T101000Z-fail0003"
    create_run(conn, run_id=rid)
    update_run_status(conn, rid, RunStatus.failed, error=None)  # None error
    conn.close()

    runs = list_early_failed_runs(db_path)
    assert len(runs) == 1
    for k, v in runs[0].items():
        assert isinstance(k, str), f"Key {k} is not str"
        assert isinstance(v, str), f"Value for {k} ({v!r}) is not str"
