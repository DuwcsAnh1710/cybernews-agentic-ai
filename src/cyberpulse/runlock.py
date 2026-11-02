"""Cross-process run lock for CyberPulse pipeline execution.

This module provides an atomic, cross-process lock using O_EXCL file creation,
with Windows process liveness checking via OpenProcess, stale lock cleanup,
and a token-based adoption handshake for subprocess runners.

NOTE: This module MUST NOT import Streamlit or UI modules.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any

logger = logging.getLogger("cyberpulse.runlock")


class RunLockError(Exception):
    """Base exception for run lock operations."""


class RunLockBusyError(RunLockError):
    """Raised when the lock is held by an active process."""

    def __init__(
        self,
        message: str,
        *,
        step: str = "unknown",
        pid: int | None = None,
        job_id: str | None = None,
        started_at: str | None = None,
    ) -> None:
        super().__init__(message)
        self.step = step
        self.pid = pid
        self.job_id = job_id
        self.started_at = started_at


def is_pid_alive(pid: int) -> bool:
    """Check if process is alive on Windows using OpenProcess and GetExitCodeProcess.

    NEVER uses os.kill(pid, 0) on Windows as that terminates the process.
    """
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == STILL_ACTIVE
            return False
        finally:
            kernel32.CloseHandle(handle)
    else:
        # Fallback for POSIX platforms (testing/CI environments)
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


def get_lock_path(output_dir: Path | str) -> Path:
    """Return path to pipeline.lock file."""
    return Path(output_dir) / "pipeline" / "pipeline.lock"


def read_lock_info(output_dir: Path | str) -> dict[str, Any] | None:
    """Read lock file data if present; return None if missing or corrupt."""
    lock_file = get_lock_path(output_dir)
    if not lock_file.exists():
        return None
    try:
        content = lock_file.read_text(encoding="utf-8")
        return json.loads(content)
    except Exception:
        return None


def release_lock(output_dir: Path | str, owner_pid: int | None = None, force: bool = False) -> None:
    """Safely release and remove pipeline.lock file."""
    lock_file = get_lock_path(output_dir)
    try:
        if not lock_file.exists():
            return
        if not force and owner_pid is not None:
            data = read_lock_info(output_dir)
            if data and data.get("owner_pid") != owner_pid:
                return
        lock_file.unlink()
    except OSError:
        pass


def update_lock_child_pid(output_dir: Path | str, child_pid: int) -> None:
    """Update child_pid in lock file atomically via temp file replacement."""
    lock_file = get_lock_path(output_dir)
    if not lock_file.exists():
        return
    try:
        lock_data = read_lock_info(output_dir)
        if lock_data:
            lock_data["child_pid"] = child_pid
            tmp_file = lock_file.with_suffix(".tmp")
            tmp_file.write_text(json.dumps(lock_data, indent=2), encoding="utf-8")
            os.replace(tmp_file, lock_file)
    except Exception as err:
        logger.warning("Failed to update child_pid in lock file: %s", err)


def acquire_lock(
    output_dir: Path | str,
    step: str = "run",
    job_id: str | None = None,
    token: str | None = None,
    is_pid_alive_fn: Callable[[int], bool] | None = None,
    on_stale_cleaned: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Atomically acquire the pipeline lock via os.O_CREAT | os.O_EXCL.

    If lock is held by a live process, raises RunLockBusyError.
    If lock is stale (process dead), cleans up and takes over.
    """
    is_alive = is_pid_alive_fn or is_pid_alive
    out_dir = Path(output_dir)
    lock_file = get_lock_path(out_dir)
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "owner_pid": os.getpid(),
        "child_pid": None,
        "step": step,
        "job_id": job_id,
        "started_at": started_at,
        "token": token,
    }

    while True:
        try:
            fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            return payload
        except FileExistsError:
            lock_data = read_lock_info(out_dir)
            if lock_data is None:
                # Corrupt lock file -> clean it up
                release_lock(out_dir, force=True)
                continue

            child_pid = lock_data.get("child_pid")
            owner_pid = lock_data.get("owner_pid")
            is_stale = False

            if child_pid is not None:
                if not is_alive(int(child_pid)):
                    is_stale = True
            else:
                if owner_pid is None or not is_alive(int(owner_pid)):
                    is_stale = True

            if is_stale:
                if on_stale_cleaned:
                    try:
                        on_stale_cleaned(lock_data)
                    except Exception as clean_err:
                        logger.warning("on_stale_cleaned error: %s", clean_err)
                release_lock(out_dir, force=True)
                continue
            else:
                h_step = lock_data.get("step", "unknown")
                h_pid = child_pid or owner_pid
                h_start = lock_data.get("started_at", "unknown")
                h_job = lock_data.get("job_id", "unknown")
                msg = f"Pipeline đang bận: bước '{h_step}' (PID: {h_pid}, Job: {h_job}) đang chạy từ {h_start}"
                raise RunLockBusyError(
                    msg,
                    step=str(h_step),
                    pid=int(h_pid) if h_pid else None,
                    job_id=str(h_job) if h_job else None,
                    started_at=str(h_start),
                )


def adopt_lock(
    output_dir: Path | str,
    token: str,
    child_pid: int,
    is_pid_alive_fn: Callable[[int], bool] | None = None,
) -> dict[str, Any]:
    """Adopt an existing lock held by parent process if token matches and owner is alive.

    Updates child_pid in the lock file atomically.
    Raises RunLockBusyError if token is invalid or owner process is dead.
    """
    is_alive = is_pid_alive_fn or is_pid_alive
    out_dir = Path(output_dir)
    lock_data = read_lock_info(out_dir)

    if lock_data is None:
        raise RunLockBusyError("Không tìm thấy tệp khóa pipeline để kế thừa (adopt).")

    stored_token = lock_data.get("token")
    if not stored_token or stored_token != token:
        raise RunLockBusyError("Mã bảo mật khóa (lock token) không khớp. Từ chối kế thừa.")

    owner_pid = lock_data.get("owner_pid")
    if owner_pid is None or not is_alive(int(owner_pid)):
        raise RunLockBusyError("Tiến trình sở hữu khóa (owner process) đã dừng. Từ chối kế thừa.")

    update_lock_child_pid(out_dir, child_pid)
    lock_data["child_pid"] = child_pid
    return lock_data


class RunLock:
    """Context manager for acquiring or adopting cross-process run lock."""

    def __init__(
        self,
        output_dir: Path | str,
        step: str = "run",
        job_id: str | None = None,
        token: str | None = None,
        adopt_token: str | None = None,
        is_pid_alive_fn: Callable[[int], bool] | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.step = step
        self.job_id = job_id
        self.token = token
        self.adopt_token = adopt_token
        self.is_pid_alive_fn = is_pid_alive_fn
        self.is_adopted = False
        self.lock_data: dict[str, Any] | None = None

    def acquire(self) -> dict[str, Any]:
        """Acquire or adopt the lock."""
        if self.adopt_token:
            self.lock_data = adopt_lock(
                output_dir=self.output_dir,
                token=self.adopt_token,
                child_pid=os.getpid(),
                is_pid_alive_fn=self.is_pid_alive_fn,
            )
            self.is_adopted = True
        else:
            self.lock_data = acquire_lock(
                output_dir=self.output_dir,
                step=self.step,
                job_id=self.job_id,
                token=self.token,
                is_pid_alive_fn=self.is_pid_alive_fn,
            )
            self.is_adopted = False
        return self.lock_data

    def release(self) -> None:
        """Release the lock."""
        if self.is_adopted:
            # If child adopted the lock, owner unlinks upon watcher completion.
            # If owner died prematurely, child cleans up.
            if self.lock_data:
                owner_pid = self.lock_data.get("owner_pid")
                is_alive = self.is_pid_alive_fn or is_pid_alive
                if owner_pid is None or not is_alive(int(owner_pid)):
                    release_lock(self.output_dir, force=True)
        else:
            release_lock(self.output_dir, owner_pid=os.getpid(), force=True)

    def __enter__(self) -> RunLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()
