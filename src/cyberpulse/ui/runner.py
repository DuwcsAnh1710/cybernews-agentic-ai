"""Pipeline Runner engine for CyberPulse AI (Task T4.4).

Pure-logic execution engine: manages atomic locks, background subprocesses,
streaming logs, output snapshots, and job history.
NOTE: This module MUST NOT import Streamlit.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from typing import Any, Callable

logger = logging.getLogger("cyberpulse.ui.runner")

# ==============================================================================
# Constants & Launchers
# ==============================================================================

# Command launcher choosing Python -c entry point to avoid wrapper .exe orphan issues
LAUNCHER_ARGV: list[str] = [
    sys.executable,
    "-c",
    "import sys; from cyberpulse.cli import main; sys.exit(main())",
]

# Regex for Google Gemini API key redaction
_AIZA_REGEX = re.compile(r"AIza[0-9A-Za-z_\-]{20,}")

# Vietnamese labels for CLI exit codes and job statuses
EXIT_LABELS_VI: dict[int | str, str] = {
    0: "Hoàn thành thành công",
    1: "Lỗi cấu hình (ConfigError)",
    2: "Tất cả các nguồn tin thất bại",
    3: "Không đủ bài viết / Không có bài phù hợp",
    4: "Thiếu GEMINI_API_KEY",
    5: "Lỗi gọi LLM (LLMError)",
    6: "Cơ sở dữ liệu trống",
    7: "Dữ liệu đầu vào hoặc tham chiếu không hợp lệ",
    8: "Phát hiện lỗi đối soát (Validation errors)",
    9: "Thiếu thư viện Streamlit",
    "timeout": "Hết thời gian chờ (Timeout)",
    "cancelled": "Đã bị hủy bởi người dùng",
    "interrupted": "Bị gián đoạn (ứng dụng hoặc tiến trình dừng đột ngột)",
}

# Step configurations
STEP_SPECS: dict[str, dict[str, Any]] = {
    "fetch": {
        "timeout_s": 180,
        "soft_exit_codes": set(),
        "calls_llm": False,
        "required_params": [],
    },
    "collect": {
        "timeout_s": 180,
        "soft_exit_codes": {3},
        "calls_llm": False,
        "required_params": [],
    },
    "research": {
        "timeout_s": 600,
        "soft_exit_codes": {3},
        "calls_llm": True,
        "required_params": [],
    },
    "editorial": {
        "timeout_s": 900,
        "soft_exit_codes": set(),
        "calls_llm": True,
        "required_params": ["research"],
    },
    "validate": {
        "timeout_s": 600,
        "soft_exit_codes": {8},
        "calls_llm": True,
        "required_params": ["research", "editorial"],
    },
}


# ==============================================================================
# Exceptions & Data Models
# ==============================================================================


class RunnerError(Exception):
    """Base exception for runner operations."""


class InvalidInputError(RunnerError):
    """Raised when input parameters or file paths are invalid."""


class PipelineBusyError(RunnerError):
    """Raised when another job currently holds the lock."""


class JobStatus(str, Enum):
    """Execution status of a pipeline runner job."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FINISHED_WITH_FINDINGS = "finished_with_findings"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


@dataclass
class JobRecord:
    """Persistent state record of a pipeline run job."""

    job_id: str
    step: str
    argv: list[str]
    status: JobStatus
    exit_code: int | None = None
    exit_label_vi: str | None = None
    started_at: str = ""
    finished_at: str | None = None
    log_path: str = ""
    new_files: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert job record to serializable dictionary."""
        d = asdict(self)
        d["status"] = self.status.value
        if isinstance(d.get("params"), dict):
            d["params"] = {k: str(v) if isinstance(v, Path) else v for k, v in d["params"].items()}
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobRecord:
        """Create JobRecord from dictionary."""
        status_val = data.get("status", JobStatus.FAILED.value)
        try:
            status = JobStatus(status_val)
        except ValueError:
            status = JobStatus.FAILED

        return cls(
            job_id=str(data.get("job_id", "")),
            step=str(data.get("step", "")),
            argv=list(data.get("argv", [])),
            status=status,
            exit_code=data.get("exit_code"),
            exit_label_vi=data.get("exit_label_vi"),
            started_at=str(data.get("started_at", "")),
            finished_at=data.get("finished_at"),
            log_path=str(data.get("log_path", "")),
            new_files=list(data.get("new_files", [])),
            params=dict(data.get("params", {})),
        )


# ==============================================================================
# Process Registry (for cancellation)
# ==============================================================================


@dataclass
class _ActiveHandle:
    proc: subprocess.Popen[bytes]
    cancel_requested: threading.Event


_REGISTRY_LOCK = threading.Lock()
_JOB_REGISTRY: dict[str, _ActiveHandle] = {}


# ==============================================================================
# Helper Functions: Secrets & Liveness
# ==============================================================================


def redact_secrets(text: str) -> str:
    """Redact Gemini API keys from text using pattern matching and os.environ."""
    redacted = _AIZA_REGEX.sub("[REDACTED_GEMINI_KEY]", text)
    env_key = os.environ.get("GEMINI_API_KEY")
    if env_key and len(env_key) >= 8:
        redacted = redacted.replace(env_key, "[REDACTED_GEMINI_KEY]")
    return redacted


def redact_argv(argv: list[str]) -> list[str]:
    """Redact sensitive arguments from argv list."""
    return [redact_secrets(arg) for arg in argv]


def _default_is_pid_alive(pid: int) -> bool:
    """Check if process is alive on Windows using OpenProcess and GetExitCodeProcess."""
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
        raise NotImplementedError("Windows only")


# ==============================================================================
# File Output Snapshotting (A1)
# ==============================================================================


def snapshot_outputs(output_dir: Path, base_dir: Path | None = None) -> dict[str, tuple[int, int]]:
    """Snapshot (relative_path: (mtime_ns, size)) for output subdirectories.

    Tracks research, editorial, and validation outputs.
    """
    base = base_dir or output_dir.resolve().parent
    snapshot: dict[str, tuple[int, int]] = {}
    for sub in ("research", "editorial", "validation"):
        d = output_dir / sub
        if d.exists() and d.is_dir():
            for f in d.rglob("*"):
                if f.is_file():
                    try:
                        st = f.stat()
                        try:
                            rel = str(f.relative_to(base)).replace("\\", "/")
                        except ValueError:
                            rel = str(f.relative_to(output_dir)).replace("\\", "/")
                        snapshot[rel] = (st.st_mtime_ns, st.st_size)
                    except OSError:
                        pass
    return snapshot


def detect_new_files(
    pre_snapshot: dict[str, tuple[int, int]],
    post_snapshot: dict[str, tuple[int, int]],
) -> list[str]:
    """Return files that are newly created or modified (A1)."""
    new_or_modified: list[str] = []
    for rel_path, post_stat in post_snapshot.items():
        if rel_path not in pre_snapshot:
            new_or_modified.append(rel_path)
        elif pre_snapshot[rel_path] != post_stat:
            new_or_modified.append(rel_path)
    return sorted(new_or_modified)


# ==============================================================================
# Path Validation
# ==============================================================================


def validate_input_path(path: Path | str, output_dir: Path) -> Path:
    """Validate that path exists, is a file, and resides inside output_dir."""
    p = Path(path).resolve()
    resolved_output = output_dir.resolve()

    if not p.exists() or not p.is_file():
        raise InvalidInputError(f"Tệp không tồn tại hoặc không phải là tệp: '{path}'")

    try:
        p.relative_to(resolved_output)
    except ValueError:
        raise InvalidInputError(f"Đường dẫn '{path}' nằm ngoài thư mục output an toàn.")

    return p


# ==============================================================================
# Lock Management (A2, A3)
# ==============================================================================


def get_lock_path(output_dir: Path) -> Path:
    """Return path to pipeline.lock file."""
    return output_dir / "pipeline" / "pipeline.lock"


def read_lock_info(output_dir: Path) -> dict[str, Any] | None:
    """Read lock file data if present; return None if missing or corrupt."""
    lock_file = get_lock_path(output_dir)
    if not lock_file.exists():
        return None
    try:
        content = lock_file.read_text(encoding="utf-8")
        return json.loads(content)
    except Exception:
        return None


def release_lock(output_dir: Path) -> None:
    """Safely release and remove pipeline.lock file."""
    lock_file = get_lock_path(output_dir)
    try:
        if lock_file.exists():
            lock_file.unlink()
    except OSError:
        pass


def _cleanup_stale_job(output_dir: Path, job_id: str | None) -> None:
    """Mark an orphaned running job as FAILED (A3)."""
    if not job_id:
        return
    job_file = output_dir / "pipeline" / "jobs" / f"{job_id}.json"
    if not job_file.exists():
        return
    try:
        content = job_file.read_text(encoding="utf-8")
        data = json.loads(content)
        if data.get("status") == JobStatus.RUNNING.value:
            record = JobRecord.from_dict(data)
            record.status = JobStatus.FAILED
            record.exit_label_vi = EXIT_LABELS_VI["interrupted"]
            record.finished_at = datetime.now(timezone.utc).isoformat()
            save_job_record_atomic(job_file, record)
    except Exception as err:
        logger.warning("Could not mark stale job '%s' as failed: %s", job_id, err)


def acquire_lock(
    output_dir: Path,
    step: str,
    job_id: str,
    is_pid_alive_fn: Callable[[int], bool] | None = None,
) -> dict[str, Any]:
    """Atomically acquire the pipeline lock, cleaning stale locks if necessary.

    Raises PipelineBusyError if held by an active process.
    """
    is_alive = is_pid_alive_fn or _default_is_pid_alive
    lock_file = get_lock_path(output_dir)
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "owner_pid": os.getpid(),
        "child_pid": None,
        "step": step,
        "job_id": job_id,
        "started_at": started_at,
    }

    # Attempt atomic creation
    while True:
        try:
            fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            return payload
        except FileExistsError:
            # Check if stale
            lock_data = read_lock_info(output_dir)
            if lock_data is None:
                # Corrupt lock file -> clean it up
                release_lock(output_dir)
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
                # Stale lock: clean up orphaned job (A3) and unlink
                _cleanup_stale_job(output_dir, lock_data.get("job_id"))
                release_lock(output_dir)
                continue
            else:
                h_step = lock_data.get("step", "unknown")
                h_pid = child_pid or owner_pid
                h_start = lock_data.get("started_at", "unknown")
                h_job = lock_data.get("job_id", "unknown")
                raise PipelineBusyError(
                    f"Pipeline đang bận: bước '{h_step}' (PID: {h_pid}, Job: {h_job}) đang chạy từ {h_start}"
                )


def update_lock_child_pid(output_dir: Path, child_pid: int) -> None:
    """Update child_pid in lock file atomically."""
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


# ==============================================================================
# Job Record Storage & Listing
# ==============================================================================


def save_job_record_atomic(job_file: Path, record: JobRecord, retries: int = 10) -> None:
    """Save JobRecord to JSON atomically using a temporary file with retry for Windows."""
    job_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = job_file.with_suffix(f".{os.getpid()}_{time.time_ns()}.tmp")
    data_dict = record.to_dict()
    tmp_file.write_text(
        json.dumps(data_dict, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    for attempt in range(retries):
        try:
            os.replace(tmp_file, job_file)
            return
        except OSError:
            if attempt == retries - 1:
                # If replace fails after retries, try direct write as fallback
                try:
                    job_file.write_text(
                        json.dumps(data_dict, indent=2, ensure_ascii=False, default=str),
                        encoding="utf-8",
                    )
                    if tmp_file.exists():
                        tmp_file.unlink()
                    return
                except Exception:
                    raise
            time.sleep(0.05)


def get_job(output_dir: Path, job_id: str) -> JobRecord | None:
    """Get JobRecord by job_id. Skips corrupt/partial files without raising (A3)."""
    job_file = output_dir / "pipeline" / "jobs" / f"{job_id}.json"
    if not job_file.exists():
        return None
    try:
        content = job_file.read_text(encoding="utf-8")
        data = json.loads(content)
        if not isinstance(data, dict):
            return None
        return JobRecord.from_dict(data)
    except Exception:
        return None


def list_jobs(output_dir: Path, limit: int = 20) -> list[JobRecord]:
    """List jobs sorted newest first. Skips corrupt/partial files without raising (A3)."""
    jobs_dir = output_dir / "pipeline" / "jobs"
    if not jobs_dir.exists() or not jobs_dir.is_dir():
        return []

    records: list[JobRecord] = []
    json_files = sorted(jobs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

    for f in json_files:
        try:
            content = f.read_text(encoding="utf-8")
            data = json.loads(content)
            if isinstance(data, dict) and "job_id" in data:
                records.append(JobRecord.from_dict(data))
        except Exception:
            # Corrupt or partial file -> skip without raising
            continue

        if len(records) >= limit:
            break

    return records


def read_log_tail(log_path: Path | str, max_lines: int = 300) -> str:
    """Read UTF-8 log tail with secret redaction."""
    p = Path(log_path)
    if not p.exists():
        return ""
    try:
        raw = p.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        tail_lines = lines[-max_lines:] if len(lines) > max_lines else lines
        return redact_secrets("\n".join(tail_lines))
    except Exception:
        return ""


# ==============================================================================
# Build Argv
# ==============================================================================


def build_argv(
    step: str,
    output_dir: Path,
    settings_path: Path | None = None,
    params: dict[str, Any] | None = None,
    launcher: list[str] | None = None,
) -> list[str]:
    """Build exact subprocess argv for the requested pipeline step."""
    if step not in STEP_SPECS:
        raise InvalidInputError(f"Bước pipeline không hợp lệ: '{step}'")

    launcher_cmd = list(launcher or LAUNCHER_ARGV)
    params = params or {}
    argv = list(launcher_cmd)
    argv.append(step)

    # Optional settings
    if settings_path is not None:
        argv.extend(["--settings", str(settings_path)])

    if step == "fetch":
        if "limit" in params:
            argv.extend(["--limit", str(int(params["limit"]))])
        if "sources" in params:
            argv.extend(["--sources", str(params["sources"])])

    elif step == "collect":
        if params.get("dry_run", False):
            argv.append("--dry-run")
        if "sources" in params:
            argv.extend(["--sources", str(params["sources"])])

    elif step == "research":
        if "sources" in params:
            argv.extend(["--sources", str(params["sources"])])

    elif step == "editorial":
        if "research" not in params:
            raise InvalidInputError("Bước 'editorial' yêu cầu tham số 'research'.")
        res_path = validate_input_path(params["research"], output_dir)
        argv.extend(["--research", str(res_path)])

    elif step == "validate":
        if "research" not in params or "editorial" not in params:
            raise InvalidInputError("Bước 'validate' yêu cầu tham số 'research' và 'editorial'.")
        res_path = validate_input_path(params["research"], output_dir)
        edit_path = validate_input_path(params["editorial"], output_dir)
        argv.extend(["--research", str(res_path), "--editorial", str(edit_path)])

        llm_review = params.get("llm_review", False)
        if not llm_review:
            argv.append("--no-llm-review")

    return argv


# ==============================================================================
# Job Execution: start_job & cancel_job
# ==============================================================================


def _generate_job_id(output_dir: Path, step: str) -> str:
    """Generate unique job_id with timestamp and collision suffix."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base_id = f"{step}-{timestamp}"
    jobs_dir = output_dir / "pipeline" / "jobs"
    if not (jobs_dir / f"{base_id}.json").exists():
        return base_id

    counter = 1
    while (jobs_dir / f"{base_id}_{counter}.json").exists():
        counter += 1
    return f"{base_id}_{counter}"


def cancel_job(job_id: str) -> tuple[bool, str]:
    """Request cancellation for an active job in the current process.

    Escalates terminate -> wait 5s -> kill (A6).
    """
    with _REGISTRY_LOCK:
        handle = _JOB_REGISTRY.get(job_id)

    if handle is None:
        return False, "Không thể hủy tiến trình này từ phiên hiện tại (không tìm thấy tiến trình)."

    handle.cancel_requested.set()
    try:
        handle.proc.terminate()
    except OSError:
        pass

    return True, "Đã gửi tín hiệu hủy tiến trình."


def start_job(
    step: str,
    output_dir: Path,
    settings_path: Path | None = None,
    params: dict[str, Any] | None = None,
    launcher: list[str] | None = None,
    cwd: Path | None = None,
    is_pid_alive_fn: Callable[[int], bool] | None = None,
    timeout_override: int | None = None,
) -> JobRecord:
    """Start a pipeline step in a background subprocess.

    Non-blocking: returns immediately after spawning child process.
    Releases lock and records failed if spawn fails (A2).
    """
    params = params or {}
    # 1. Validate inputs and build argv
    argv = build_argv(
        step=step,
        output_dir=output_dir,
        settings_path=settings_path,
        params=params,
        launcher=launcher,
    )

    spec = STEP_SPECS[step]
    timeout_s = timeout_override or spec["timeout_s"]
    soft_exit_codes = spec["soft_exit_codes"]

    # 2. Setup job paths and ID
    jobs_dir = output_dir / "pipeline" / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    job_id = _generate_job_id(output_dir, step)
    job_file = jobs_dir / f"{job_id}.json"
    log_file_path = jobs_dir / f"{job_id}.log"

    # 3. Acquire lock
    acquire_lock(output_dir, step=step, job_id=job_id, is_pid_alive_fn=is_pid_alive_fn)

    # 4. Prepare initial record and snapshot
    record = JobRecord(
        job_id=job_id,
        step=step,
        argv=redact_argv(argv),
        status=JobStatus.RUNNING,
        started_at=datetime.now(timezone.utc).isoformat(),
        log_path=str(log_file_path),
        params=params,
    )
    save_job_record_atomic(job_file, record)

    pre_snapshot = snapshot_outputs(output_dir)

    # 5. Spawn child process (guard lock release if Popen fails - A2)
    proc: subprocess.Popen[bytes]
    cancel_event = threading.Event()
    working_dir = cwd or output_dir.resolve().parent

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

    try:
        log_file = open(log_file_path, "ab", buffering=0)
        proc = subprocess.Popen(
            argv,
            cwd=str(working_dir),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=creationflags,
        )
    except Exception as exc:
        # A2: Release lock if Popen raises, mark job record failed, re-raise typed error
        release_lock(output_dir)
        record.status = JobStatus.FAILED
        record.exit_label_vi = f"Lỗi khởi chạy: {exc}"
        record.finished_at = datetime.now(timezone.utc).isoformat()
        try:
            save_job_record_atomic(job_file, record)
        except Exception:
            pass
        raise RunnerError(f"Không thể khởi chạy tiến trình con: {exc}") from exc

    # Update lock with child_pid
    update_lock_child_pid(output_dir, proc.pid)

    # Register active handle
    handle = _ActiveHandle(proc=proc, cancel_requested=cancel_event)
    with _REGISTRY_LOCK:
        _JOB_REGISTRY[job_id] = handle

    # 6. Start daemon watcher thread
    def _watcher() -> None:
        exit_code: int | None = None
        timed_out = False
        try:
            try:
                exit_code = proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.terminate()
                try:
                    exit_code = proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    exit_code = proc.wait()

            # Handle cancellation escalation if still alive
            if cancel_event.is_set():
                try:
                    exit_code = proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    exit_code = proc.wait()

            # Determine status
            if cancel_event.is_set():
                final_status = JobStatus.CANCELLED
            elif timed_out:
                final_status = JobStatus.TIMEOUT
            elif exit_code == 0:
                final_status = JobStatus.SUCCEEDED
            elif exit_code in soft_exit_codes:
                final_status = JobStatus.FINISHED_WITH_FINDINGS
            else:
                final_status = JobStatus.FAILED

            # Detect new/modified output files (A1)
            post_snapshot = snapshot_outputs(output_dir)
            new_files = detect_new_files(pre_snapshot, post_snapshot)

            # Determine Vietnamese exit label
            exit_label: str
            if final_status == JobStatus.CANCELLED:
                exit_label = EXIT_LABELS_VI["cancelled"]
            elif final_status == JobStatus.TIMEOUT:
                exit_label = EXIT_LABELS_VI["timeout"]
            else:
                exit_label = EXIT_LABELS_VI.get(exit_code or 0, f"Mã thoát {exit_code}")

            # Update job record atomically
            record.status = final_status
            record.exit_code = exit_code
            record.exit_label_vi = exit_label
            record.finished_at = datetime.now(timezone.utc).isoformat()
            record.new_files = new_files
            save_job_record_atomic(job_file, record)

        except Exception as watcher_err:
            logger.error("Watcher thread error for job %s: %s", job_id, watcher_err)
            record.status = JobStatus.FAILED
            record.exit_label_vi = f"Lỗi theo dõi: {watcher_err}"
            record.finished_at = datetime.now(timezone.utc).isoformat()
            try:
                save_job_record_atomic(job_file, record)
            except Exception:
                pass

        finally:
            # Clean up registry and release lock in ALL outcomes
            with _REGISTRY_LOCK:
                _JOB_REGISTRY.pop(job_id, None)
            release_lock(output_dir)
            try:
                log_file.close()
            except Exception:
                pass

    watcher_thread = threading.Thread(target=_watcher, name=f"watcher-{job_id}", daemon=True)
    watcher_thread.start()

    return record
