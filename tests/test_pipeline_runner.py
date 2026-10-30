"""Offline hermetic unit tests for Pipeline Runner (Task T4.4).

Ensures all lifecycle, locking, redaction, path safety, and snapshot requirements
are tested using mock sys.executable child scripts (no network, no real CLI).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
import pytest

from cyberpulse.ui.runner import (
    EXIT_LABELS_VI,
    InvalidInputError,
    JobRecord,
    JobStatus,
    PipelineBusyError,
    RunnerError,
    acquire_lock,
    build_argv,
    cancel_job,
    detect_new_files,
    get_job,
    get_lock_path,
    list_jobs,
    read_lock_info,
    read_log_tail,
    redact_argv,
    redact_secrets,
    release_lock,
    save_job_record_atomic,
    snapshot_outputs,
    start_job,
    update_lock_child_pid,
    validate_input_path,
)


def _mock_launcher_for_code(code_str: str) -> list[str]:
    """Helper to create a launcher that executes inline Python code."""
    return [sys.executable, "-c", code_str]


# ------------------------------------------------------------------------------
# 1. Module Independence & Secret Redaction
# ------------------------------------------------------------------------------


def test_runner_module_does_not_import_streamlit(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC 2: runner.py must be pure logic and never import streamlit."""
    monkeypatch.setitem(sys.modules, "streamlit", None)
    import cyberpulse.ui.runner

    assert cyberpulse.ui.runner is not None


def test_secret_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC 8: Redaction of regex pattern AIza... and os.environ[GEMINI_API_KEY]."""
    fake_key = "AIza" + "X" * 35
    env_secret = "secret_gemini_key_12345"
    monkeypatch.setenv("GEMINI_API_KEY", env_secret)

    sample_text = (
        f"Connecting with key={fake_key} and fallback={env_secret}. Status: OK."
    )
    redacted = redact_secrets(sample_text)

    assert fake_key not in redacted
    assert env_secret not in redacted
    assert "[REDACTED_GEMINI_KEY]" in redacted

    argv = ["cyberpulse", "research", f"--key={fake_key}", f"--other={env_secret}"]
    redacted_argv = redact_argv(argv)
    assert fake_key not in " ".join(redacted_argv)
    assert env_secret not in " ".join(redacted_argv)


# ------------------------------------------------------------------------------
# 2. Build Argv & Path Safety
# ------------------------------------------------------------------------------


def test_build_argv_all_five_steps(tmp_path: Path) -> None:
    """AC 3: Exactly 5 steps exist, correct flags according to real CLI specs."""
    out_dir = tmp_path / "output"
    r_dir = out_dir / "research"
    e_dir = out_dir / "editorial"
    r_dir.mkdir(parents=True)
    e_dir.mkdir(parents=True)

    r_file = r_dir / "test-run.json"
    e_file = e_dir / "test-run.json"
    r_file.write_text("{}", encoding="utf-8")
    e_file.write_text("{}", encoding="utf-8")

    settings = tmp_path / "settings.toml"
    settings.write_text("", encoding="utf-8")

    # 1. fetch
    argv_fetch = build_argv("fetch", out_dir, settings_path=settings, params={"limit": 5})
    assert "fetch" in argv_fetch
    assert "--settings" in argv_fetch
    assert "--limit" in argv_fetch
    assert "5" in argv_fetch

    # 2. collect
    argv_collect = build_argv("collect", out_dir, settings_path=settings, params={"dry_run": True})
    assert "collect" in argv_collect
    assert "--dry-run" in argv_collect

    # 3. research
    argv_research = build_argv("research", out_dir, settings_path=settings)
    assert "research" in argv_research

    # 4. editorial
    argv_edit = build_argv(
        "editorial",
        out_dir,
        settings_path=settings,
        params={"research": r_file},
    )
    assert "editorial" in argv_edit
    assert "--research" in argv_edit
    assert str(r_file.resolve()) in argv_edit

    # 5. validate (default no-llm-review)
    argv_val = build_argv(
        "validate",
        out_dir,
        settings_path=settings,
        params={"research": r_file, "editorial": e_file, "llm_review": False},
    )
    assert "validate" in argv_val
    assert "--research" in argv_val
    assert "--editorial" in argv_val
    assert "--no-llm-review" in argv_val

    # 5b. validate with LLM review enabled
    argv_val_llm = build_argv(
        "validate",
        out_dir,
        settings_path=settings,
        params={"research": r_file, "editorial": e_file, "llm_review": True},
    )
    assert "--no-llm-review" not in argv_val_llm


def test_path_safety_validation(tmp_path: Path) -> None:
    """AC 9: Nonexistent paths and paths escaping output/ are rejected."""
    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True)

    # 1. Nonexistent file
    with pytest.raises(InvalidInputError, match="không tồn tại"):
        validate_input_path(out_dir / "nonexistent.json", out_dir)

    # 2. Escaping output directory
    secret_file = tmp_path / "secret.json"
    secret_file.write_text("{}", encoding="utf-8")
    with pytest.raises(InvalidInputError, match="nằm ngoài thư mục output"):
        validate_input_path(secret_file, out_dir)


# ------------------------------------------------------------------------------
# 3. Lock & Stale Lock Cleanup (AC 4, AC 5, A2, A3)
# ------------------------------------------------------------------------------


def test_atomic_lock_and_busy_error(tmp_path: Path) -> None:
    """AC 4: Second acquire while lock is held raises PipelineBusyError."""
    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True)

    # Lock with current running process (alive)
    lock_data = acquire_lock(
        out_dir,
        step="fetch",
        job_id="job-1",
        is_pid_alive_fn=lambda pid: True,
    )
    assert lock_data["step"] == "fetch"

    # Second acquire must fail with PipelineBusyError
    with pytest.raises(PipelineBusyError, match="Pipeline đang bận"):
        acquire_lock(
            out_dir,
            step="collect",
            job_id="job-2",
            is_pid_alive_fn=lambda pid: True,
        )

    # Clean release
    release_lock(out_dir)
    assert not get_lock_path(out_dir).exists()


def test_stale_lock_cleanup_child_dead(tmp_path: Path) -> None:
    """AC 5 & A3: Stale lock when child_pid is dead is cleaned up, and orphaned job marked failed."""
    out_dir = tmp_path / "output"
    jobs_dir = out_dir / "pipeline" / "jobs"
    jobs_dir.mkdir(parents=True)

    # Create orphaned running job record
    orphan_job = JobRecord(
        job_id="orphan-job-1",
        step="fetch",
        argv=["mock"],
        status=JobStatus.RUNNING,
        started_at="2026-10-30T00:00:00Z",
    )
    save_job_record_atomic(jobs_dir / "orphan-job-1.json", orphan_job)

    # Write lock with dead child_pid
    lock_path = get_lock_path(out_dir)
    lock_path.write_text(
        json.dumps(
            {
                "owner_pid": 999999,
                "child_pid": 888888,
                "step": "fetch",
                "job_id": "orphan-job-1",
                "started_at": "2026-10-30T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    # Injected liveness check returns False (child is dead)
    new_lock = acquire_lock(
        out_dir,
        step="collect",
        job_id="new-job",
        is_pid_alive_fn=lambda pid: False,
    )
    assert new_lock["job_id"] == "new-job"

    # Verify A3: orphan job JSON is updated to FAILED with Vietnamese interrupted label
    updated_orphan = get_job(out_dir, "orphan-job-1")
    assert updated_orphan is not None
    assert updated_orphan.status == JobStatus.FAILED
    assert updated_orphan.exit_label_vi == EXIT_LABELS_VI["interrupted"]

    release_lock(out_dir)


def test_stale_lock_cleanup_owner_dead(tmp_path: Path) -> None:
    """AC 5: Stale lock when child_pid is null and owner_pid is dead is cleaned up."""
    out_dir = tmp_path / "output"
    jobs_dir = out_dir / "pipeline" / "jobs"
    jobs_dir.mkdir(parents=True)

    lock_path = get_lock_path(out_dir)
    lock_path.write_text(
        json.dumps(
            {
                "owner_pid": 777777,
                "child_pid": None,
                "step": "research",
                "job_id": "orphan-job-2",
                "started_at": "2026-10-30T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    new_lock = acquire_lock(
        out_dir,
        step="editorial",
        job_id="new-job-2",
        is_pid_alive_fn=lambda pid: False,
    )
    assert new_lock["job_id"] == "new-job-2"
    release_lock(out_dir)


def test_lock_release_on_popen_failure(tmp_path: Path) -> None:
    """A2: Lock release MUST happen if Popen raises (nonexistent executable)."""
    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True)

    # Pass an invalid/nonexistent executable in launcher
    invalid_launcher = ["this_executable_does_not_exist_at_all_12345"]

    with pytest.raises(RunnerError, match="Không thể khởi chạy"):
        start_job(
            step="fetch",
            output_dir=out_dir,
            launcher=invalid_launcher,
        )

    # Assert lock file is removed
    assert not get_lock_path(out_dir).exists()


# ------------------------------------------------------------------------------
# 4. Output Snapshotting & Modification Detection (A1)
# ------------------------------------------------------------------------------


def test_new_files_detection_created_and_overwritten(tmp_path: Path) -> None:
    """A1: new_files detects newly created AND modified/overwritten files."""
    out_dir = tmp_path / "output"
    val_dir = out_dir / "validation"
    val_dir.mkdir(parents=True)

    existing_file = val_dir / "run-123.json"
    existing_file.write_text('{"passed": false}', encoding="utf-8")

    pre_snap = snapshot_outputs(out_dir, base_dir=tmp_path)

    # 1. Modify existing file (simulate validate re-run)
    time.sleep(0.01)
    existing_file.write_text('{"passed": true, "note": "overwritten"}', encoding="utf-8")

    # 2. Add a new file
    new_file = val_dir / "run-456.json"
    new_file.write_text('{"passed": true}', encoding="utf-8")

    post_snap = snapshot_outputs(out_dir, base_dir=tmp_path)
    diff = detect_new_files(pre_snap, post_snap)

    assert "output/validation/run-123.json" in diff
    assert "output/validation/run-456.json" in diff


# ------------------------------------------------------------------------------
# 5. Non-blocking & Lifecycle Tests (AC 6, AC 7, AC 10, A5, A6)
# ------------------------------------------------------------------------------


def test_start_job_non_blocking(tmp_path: Path) -> None:
    """AC 10 & A5: start_job for a sleep child returns in < 2 seconds."""
    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True)

    # Child sleeps for 2 seconds
    launcher = _mock_launcher_for_code("import time; time.sleep(2)")

    t0 = time.perf_counter()
    job = start_job(
        step="fetch",
        output_dir=out_dir,
        launcher=launcher,
    )
    elapsed = time.perf_counter() - t0

    assert elapsed < 2.0, f"start_job took too long: {elapsed:.3f}s"
    assert job.status == JobStatus.RUNNING

    # Wait for completion
    for _ in range(50):
        time.sleep(0.1)
        j = get_job(out_dir, job.job_id)
        if j and j.status != JobStatus.RUNNING:
            break

    final_job = get_job(out_dir, job.job_id)
    assert final_job is not None
    assert final_job.status == JobStatus.SUCCEEDED
    assert not get_lock_path(out_dir).exists()


def test_lifecycle_exit_0_succeeded(tmp_path: Path) -> None:
    """AC 6: Exit 0 -> JobStatus.SUCCEEDED, lock released."""
    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True)

    launcher = _mock_launcher_for_code("print('Hello from child'); exit(0)")
    job = start_job(step="fetch", output_dir=out_dir, launcher=launcher)

    for _ in range(50):
        time.sleep(0.1)
        j = get_job(out_dir, job.job_id)
        if j and j.status != JobStatus.RUNNING:
            break

    res = get_job(out_dir, job.job_id)
    assert res is not None
    assert res.status == JobStatus.SUCCEEDED
    assert res.exit_code == 0
    assert not get_lock_path(out_dir).exists()


def test_lifecycle_soft_exit_codes(tmp_path: Path) -> None:
    """AC 6: Soft exit codes (validate 8, collect 3, research 3) -> FINISHED_WITH_FINDINGS."""
    out_dir = tmp_path / "output"
    r_dir = out_dir / "research"
    e_dir = out_dir / "editorial"
    r_dir.mkdir(parents=True)
    e_dir.mkdir(parents=True)
    r_file = r_dir / "r.json"
    e_file = e_dir / "e.json"
    r_file.write_text("{}", encoding="utf-8")
    e_file.write_text("{}", encoding="utf-8")

    # 1. Validate exit 8
    launcher_val = _mock_launcher_for_code("print('Validation errors found'); exit(8)")
    job_val = start_job(
        step="validate",
        output_dir=out_dir,
        launcher=launcher_val,
        params={"research": r_file, "editorial": e_file},
    )
    for _ in range(50):
        time.sleep(0.1)
        j = get_job(out_dir, job_val.job_id)
        if j and j.status != JobStatus.RUNNING:
            break
    res_val = get_job(out_dir, job_val.job_id)
    assert res_val is not None
    assert res_val.status == JobStatus.FINISHED_WITH_FINDINGS
    assert res_val.exit_code == 8

    # 2. Collect exit 3
    launcher_col = _mock_launcher_for_code("print('0 articles kept'); exit(3)")
    job_col = start_job(step="collect", output_dir=out_dir, launcher=launcher_col)
    for _ in range(50):
        time.sleep(0.1)
        j = get_job(out_dir, job_col.job_id)
        if j and j.status != JobStatus.RUNNING:
            break
    res_col = get_job(out_dir, job_col.job_id)
    assert res_col is not None
    assert res_col.status == JobStatus.FINISHED_WITH_FINDINGS
    assert res_col.exit_code == 3

    # 3. Research exit 3
    launcher_res = _mock_launcher_for_code("print('Insufficient candidates'); exit(3)")
    job_res = start_job(step="research", output_dir=out_dir, launcher=launcher_res)
    for _ in range(50):
        time.sleep(0.1)
        j = get_job(out_dir, job_res.job_id)
        if j and j.status != JobStatus.RUNNING:
            break
    res_res = get_job(out_dir, job_res.job_id)
    assert res_res is not None
    assert res_res.status == JobStatus.FINISHED_WITH_FINDINGS
    assert res_res.exit_code == 3


def test_lifecycle_failure_exit_codes(tmp_path: Path) -> None:
    """AC 6: Hard exit code (e.g. exit 1, 2) -> JobStatus.FAILED + Vietnamese label."""
    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True)

    launcher = _mock_launcher_for_code("print('Config error'); exit(1)")
    job = start_job(step="fetch", output_dir=out_dir, launcher=launcher)

    for _ in range(50):
        time.sleep(0.1)
        j = get_job(out_dir, job.job_id)
        if j and j.status != JobStatus.RUNNING:
            break

    res = get_job(out_dir, job.job_id)
    assert res is not None
    assert res.status == JobStatus.FAILED
    assert res.exit_code == 1
    assert "ConfigError" in (res.exit_label_vi or "")


def test_lifecycle_timeout(tmp_path: Path) -> None:
    """AC 6: Timeout expired -> child process terminated/killed, status TIMEOUT."""
    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True)

    launcher = _mock_launcher_for_code("import time; time.sleep(10)")
    job = start_job(
        step="fetch",
        output_dir=out_dir,
        launcher=launcher,
        timeout_override=1,
    )

    for _ in range(80):
        time.sleep(0.1)
        j = get_job(out_dir, job.job_id)
        if j and j.status != JobStatus.RUNNING:
            break

    res = get_job(out_dir, job.job_id)
    assert res is not None
    assert res.status == JobStatus.TIMEOUT
    assert "Timeout" in (res.exit_label_vi or "")
    assert not get_lock_path(out_dir).exists()


def test_lifecycle_cancellation(tmp_path: Path) -> None:
    """AC 6 & A6: cancel_job terminates child process, status CANCELLED."""
    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True)

    launcher = _mock_launcher_for_code("import time; time.sleep(10)")
    job = start_job(step="fetch", output_dir=out_dir, launcher=launcher)

    time.sleep(0.2)
    ok, msg = cancel_job(job.job_id)
    assert ok is True

    for _ in range(60):
        time.sleep(0.1)
        j = get_job(out_dir, job.job_id)
        if j and j.status != JobStatus.RUNNING:
            break

    res = get_job(out_dir, job.job_id)
    assert res is not None
    assert res.status == JobStatus.CANCELLED
    assert "hủy" in (res.exit_label_vi or "").lower()
    assert not get_lock_path(out_dir).exists()


def test_utf8_log_roundtrip(tmp_path: Path) -> None:
    """AC 7: Vietnamese characters in child process stdout appear intact in read_log_tail."""
    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True)

    vn_text = "Kiểm tra tiếng Việt: An ninh mạng và trí tuệ nhân tạo."
    code = f"import sys; sys.stdout.reconfigure(encoding='utf-8'); print('{vn_text}')"
    launcher = _mock_launcher_for_code(code)

    job = start_job(step="fetch", output_dir=out_dir, launcher=launcher)

    for _ in range(50):
        time.sleep(0.1)
        j = get_job(out_dir, job.job_id)
        if j and j.status != JobStatus.RUNNING:
            break

    tail = read_log_tail(job.log_path)
    assert vn_text in tail


def test_list_and_get_jobs_skips_corrupt_files(tmp_path: Path) -> None:
    """A3: list_jobs and get_job must skip corrupt/partial JSON files without raising."""
    out_dir = tmp_path / "output"
    jobs_dir = out_dir / "pipeline" / "jobs"
    jobs_dir.mkdir(parents=True)

    # 1. Valid job
    job1 = JobRecord(
        job_id="job-valid",
        step="fetch",
        argv=["mock"],
        status=JobStatus.SUCCEEDED,
        started_at="2026-10-30T01:00:00Z",
    )
    save_job_record_atomic(jobs_dir / "job-valid.json", job1)

    # 2. Corrupt partial JSON
    (jobs_dir / "job-corrupt.json").write_text("{ incomplete json ...", encoding="utf-8")

    # 3. Non-dict JSON
    (jobs_dir / "job-nondict.json").write_text("[1, 2, 3]", encoding="utf-8")

    # Call list_jobs
    jobs = list_jobs(out_dir, limit=10)
    assert len(jobs) == 1
    assert jobs[0].job_id == "job-valid"

    # Call get_job on corrupt
    assert get_job(out_dir, "job-corrupt") is None
    assert get_job(out_dir, "job-valid") is not None

