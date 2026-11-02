"""Hermetic unit tests for cyberpulse.runlock."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest

from cyberpulse.runlock import (
    RunLock,
    RunLockBusyError,
    acquire_lock,
    adopt_lock,
    get_lock_path,
    read_lock_info,
    release_lock,
    update_lock_child_pid,
)


def test_runlock_no_streamlit_imported() -> None:
    """Ensure runlock module does NOT import Streamlit."""
    import subprocess
    cmd = [
        sys.executable,
        "-c",
        "import sys; import cyberpulse.runlock; sys.exit(1 if 'streamlit' in sys.modules else 0)",
    ]
    res = subprocess.run(cmd, capture_output=True)
    assert res.returncode == 0


def test_runlock_atomic_acquire_and_release(tmp_path: Path) -> None:
    """Basic acquire creates pipeline.lock and release unlinks it."""
    out_dir = tmp_path / "output"
    lock_data = acquire_lock(out_dir, step="run", job_id="test-job-1")
    assert lock_data["step"] == "run"
    assert lock_data["owner_pid"] == os.getpid()
    assert lock_data["child_pid"] is None

    lock_file = get_lock_path(out_dir)
    assert lock_file.exists()

    release_lock(out_dir)
    assert not lock_file.exists()


def test_runlock_busy_detection(tmp_path: Path) -> None:
    """Process B attempting acquire while lock held by active process raises RunLockBusyError."""
    out_dir = tmp_path / "output"
    acquire_lock(out_dir, step="run", job_id="job-active", is_pid_alive_fn=lambda pid: True)

    with pytest.raises(RunLockBusyError, match="Pipeline đang bận: bước 'run'") as exc_info:
        acquire_lock(out_dir, step="run", job_id="job-second", is_pid_alive_fn=lambda pid: True)

    assert exc_info.value.step == "run"
    assert exc_info.value.pid == os.getpid()
    assert exc_info.value.job_id == "job-active"


def test_runlock_stale_cleanup_dead_child(tmp_path: Path) -> None:
    """Stale lock with dead child_pid is cleaned up and callbacks invoked."""
    out_dir = tmp_path / "output"
    lock_file = get_lock_path(out_dir)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "owner_pid": 999991,
        "child_pid": 999992,
        "step": "research",
        "job_id": "orphaned-job-1",
        "started_at": "2026-11-01T00:00:00Z",
    }
    lock_file.write_text(json.dumps(payload), encoding="utf-8")

    stale_cleaned = []

    def on_stale(data: dict) -> None:
        stale_cleaned.append(data.get("job_id"))

    # When child 999992 is dead (is_pid_alive returns False)
    new_lock = acquire_lock(
        out_dir,
        step="run",
        job_id="new-job",
        is_pid_alive_fn=lambda pid: False,
        on_stale_cleaned=on_stale,
    )

    assert stale_cleaned == ["orphaned-job-1"]
    assert new_lock["job_id"] == "new-job"
    assert new_lock["owner_pid"] == os.getpid()


def test_runlock_stale_cleanup_dead_owner_null_child(tmp_path: Path) -> None:
    """Stale lock with dead owner_pid and null child_pid is cleaned up."""
    out_dir = tmp_path / "output"
    lock_file = get_lock_path(out_dir)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "owner_pid": 999993,
        "child_pid": None,
        "step": "editorial",
        "job_id": "orphaned-job-2",
        "started_at": "2026-11-01T00:00:00Z",
    }
    lock_file.write_text(json.dumps(payload), encoding="utf-8")

    new_lock = acquire_lock(
        out_dir,
        step="run",
        job_id="new-job-2",
        is_pid_alive_fn=lambda pid: False,
    )
    assert new_lock["job_id"] == "new-job-2"
    assert new_lock["owner_pid"] == os.getpid()


def test_runlock_backward_compatible_format(tmp_path: Path) -> None:
    """Existing lock file without 'token' field is parsed correctly without error."""
    out_dir = tmp_path / "output"
    lock_file = get_lock_path(out_dir)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "owner_pid": os.getpid(),
        "child_pid": None,
        "step": "fetch",
        "job_id": "legacy-job",
        "started_at": "2026-11-01T00:00:00Z",
    }
    lock_file.write_text(json.dumps(payload), encoding="utf-8")

    info = read_lock_info(out_dir)
    assert info is not None
    assert info.get("token") is None
    assert info["job_id"] == "legacy-job"

    # Alive owner -> busy
    with pytest.raises(RunLockBusyError):
        acquire_lock(out_dir, step="run", is_pid_alive_fn=lambda pid: True)


def test_runlock_token_adoption_success(tmp_path: Path) -> None:
    """Child process adopts lock if token matches and owner is alive."""
    out_dir = tmp_path / "output"
    token = "secret-token-abc-123"
    acquire_lock(out_dir, step="run", job_id="runner-job", token=token, is_pid_alive_fn=lambda pid: True)

    child_pid = 88888
    adopted = adopt_lock(out_dir, token=token, child_pid=child_pid, is_pid_alive_fn=lambda pid: True)
    assert adopted["child_pid"] == child_pid
    assert adopted["token"] == token

    # Check updated on disk
    disk_info = read_lock_info(out_dir)
    assert disk_info is not None
    assert disk_info["child_pid"] == child_pid


def test_runlock_token_adoption_mismatch_raises(tmp_path: Path) -> None:
    """Adoption fails if token does not match."""
    out_dir = tmp_path / "output"
    acquire_lock(out_dir, step="run", token="correct-token", is_pid_alive_fn=lambda pid: True)

    with pytest.raises(RunLockBusyError, match="không khớp"):
        adopt_lock(out_dir, token="wrong-token", child_pid=77777, is_pid_alive_fn=lambda pid: True)


def test_runlock_token_adoption_dead_owner_raises(tmp_path: Path) -> None:
    """Adoption fails if parent process died before child started."""
    out_dir = tmp_path / "output"
    acquire_lock(out_dir, step="run", token="token-x", is_pid_alive_fn=lambda pid: True)

    with pytest.raises(RunLockBusyError, match="đã dừng"):
        adopt_lock(out_dir, token="token-x", child_pid=77777, is_pid_alive_fn=lambda pid: False)


def test_runlock_context_manager_release_on_exception(tmp_path: Path) -> None:
    """RunLock context manager unlinks lock file even on uncaught exception."""
    out_dir = tmp_path / "output"
    lock_file = get_lock_path(out_dir)

    with pytest.raises(RuntimeError):
        with RunLock(out_dir, step="run", is_pid_alive_fn=lambda pid: True):
            assert lock_file.exists()
            raise RuntimeError("Boom!")

    assert not lock_file.exists()


def test_runlock_context_manager_adopted_relinquish(tmp_path: Path) -> None:
    """Adopted RunLock context manager leaves lock file for parent if parent alive."""
    out_dir = tmp_path / "output"
    token = "adopt-token-123"
    acquire_lock(out_dir, step="run", token=token, is_pid_alive_fn=lambda pid: True)
    lock_file = get_lock_path(out_dir)

    with RunLock(out_dir, step="run", adopt_token=token, is_pid_alive_fn=lambda pid: True):
        assert lock_file.exists()

    # Still exists for parent runner
    assert lock_file.exists()
    release_lock(out_dir, force=True)
    assert not lock_file.exists()
