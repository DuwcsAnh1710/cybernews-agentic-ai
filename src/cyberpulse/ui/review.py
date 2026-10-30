"""Review decision logging and quick text edit persistence for CyberPulse AI.

NOTE: This module MUST NOT import Streamlit. It contains pure review logic,
rule validation, and atomic edit persistence that are independently unit-tested.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import tempfile
from typing import Literal

from cyberpulse.models import EditorialPackage
from cyberpulse.render_preview import render_markdown
from cyberpulse.ui.data import ArtifactError, load_editorial, load_research, load_validation, validation_freshness

logger = logging.getLogger("cyberpulse.ui.review")


class ReviewRuleError(Exception):
    """Raised when a human review decision violates pipeline governance rules."""


EDITABLE_PATH_PATTERNS = [
    re.compile(r"^digest\.title$"),
    re.compile(r"^digest\.intro$"),
    re.compile(r"^digest\.sections\[\d+\]\.heading$"),
    re.compile(r"^digest\.sections\[\d+\]\.body\.text$"),
    re.compile(r"^podcast\.segments\[\d+\]\.line\.text$"),
    re.compile(r"^video\.scenes\[\d+\]\.on_screen_text$"),
    re.compile(r"^video\.scenes\[\d+\]\.narration\.text$"),
    re.compile(r"^captions\[\d+\]\.text$"),
]


def is_editable_path(path: str) -> bool:
    """Return True if path points to an allowed editable text field."""
    return any(pattern.match(path) for pattern in EDITABLE_PATH_PATTERNS)


def sha256_of_file(path: Path | str | None) -> str | None:
    """Compute hex SHA-256 digest of file content, or None if path missing."""
    if path is None:
        return None
    p = Path(path)
    if not p.exists():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def load_review(path: Path | str | None) -> dict:
    """Load review JSON file or return empty structure."""
    if path is None:
        return {"run_id": "", "decisions": [], "edits": []}
    p = Path(path)
    if not p.exists():
        return {"run_id": p.stem, "decisions": [], "edits": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"run_id": p.stem, "decisions": [], "edits": []}
        data.setdefault("run_id", p.stem)
        data.setdefault("decisions", [])
        data.setdefault("edits", [])
        return data
    except Exception:
        return {"run_id": p.stem, "decisions": [], "edits": []}


def current_status(
    review_data: dict,
    editorial_path: Path | str | None,
) -> Literal["unreviewed", "approved", "rejected", "changes_requested"]:
    """Compute current review status.

    Reverts 'approved' to 'unreviewed' if the editorial file's current SHA256 differs
    from the approved SHA256.
    """
    decisions = review_data.get("decisions", [])
    if not decisions:
        return "unreviewed"

    last_dec = decisions[-1]
    dec_type = last_dec.get("decision")

    if dec_type == "approve":
        current_hash = sha256_of_file(editorial_path)
        approved_hash = last_dec.get("editorial_sha256")
        if current_hash != approved_hash:
            return "unreviewed"
        return "approved"
    elif dec_type == "reject":
        return "rejected"
    elif dec_type == "request_changes":
        return "changes_requested"

    return "unreviewed"


def record_decision(
    review_path: Path | str,
    editorial_path: Path | str,
    validation_path: Path | str | None,
    *,
    decision: Literal["approve", "reject", "request_changes"],
    reviewer: str,
    note: str,
    warnings_acknowledged: bool = False,
) -> dict:
    """Record a human review decision adhering to pipeline governance rules.

    Raises ReviewRuleError with user-facing Vietnamese explanation if rules fail.
    """
    if not reviewer or not reviewer.strip():
        raise ReviewRuleError("Tên người duyệt không được để trống.")

    clean_reviewer = reviewer.strip()
    clean_note = note.strip() if note else ""

    if decision in {"reject", "request_changes"} and not clean_note:
        raise ReviewRuleError("Ghi chú bắt buộc khi từ chối (reject) hoặc yêu cầu sửa đổi (request changes).")

    r_path = Path(review_path)
    e_path = Path(editorial_path)
    v_path = Path(validation_path) if validation_path else None

    freshness = validation_freshness(e_path, v_path)
    val_data = load_validation(v_path)
    val_passed = val_data.get("passed") if val_data else None
    val_warnings = val_data.get("warnings", []) if val_data else []

    if decision == "approve":
        if freshness != "fresh":
            raise ReviewRuleError(
                "Không thể duyệt: Kết quả kiểm tra (validation) đang thiếu hoặc đã cũ (stale). "
                "Vui lòng chạy lại 'cyberpulse validate'."
            )
        if not val_passed:
            raise ReviewRuleError("Không thể duyệt: Bản tin còn lỗi kiểm tra (validation errors).")
        if val_warnings and not warnings_acknowledged:
            raise ReviewRuleError("Vui lòng xác nhận đã đọc và chấp nhận các cảnh báo trước khi duyệt.")

    r_path.parent.mkdir(parents=True, exist_ok=True)
    review_data = load_review(r_path)
    review_data["run_id"] = e_path.stem

    editorial_hash = sha256_of_file(e_path)
    validation_hash = sha256_of_file(v_path)

    new_decision = {
        "decision": decision,
        "reviewer": clean_reviewer,
        "note": clean_note,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "editorial_sha256": editorial_hash,
        "validation_sha256": validation_hash,
        "validation_passed": val_passed,
        "warnings_acknowledged": warnings_acknowledged,
        "validation_freshness": freshness,
    }

    review_data["decisions"].append(new_decision)

    # Atomic write
    with tempfile.NamedTemporaryFile("w", dir=r_path.parent, delete=False, encoding="utf-8") as tf:
        json.dump(review_data, tf, indent=2, ensure_ascii=False)
        temp_name = tf.name
    os.replace(temp_name, r_path)

    return new_decision


def _get_nested_field(data: dict, path: str) -> str:
    """Retrieve text value at path."""
    parts = re.split(r"\.|\[|\]", path)
    tokens = [p for p in parts if p]
    cur = data
    for t in tokens:
        if t.isdigit():
            cur = cur[int(t)]
        else:
            cur = cur[t]
    return str(cur)


def _set_nested_field(data: dict, path: str, new_val: str) -> None:
    """Mutate text value at path."""
    parts = re.split(r"\.|\[|\]", path)
    tokens = [p for p in parts if p]
    cur = data
    for t in tokens[:-1]:
        if t.isdigit():
            cur = cur[int(t)]
        else:
            cur = cur[t]
    last_key = int(tokens[-1]) if tokens[-1].isdigit() else tokens[-1]
    cur[last_key] = new_val


def apply_text_edit(
    editorial_path: Path | str,
    review_path: Path | str,
    *,
    path: str,
    new_text: str,
    reviewer: str,
    note: str,
) -> None:
    """Apply a quick text edit to an EditorialPackage.

    Only allowed text fields can be modified. On first edit, copies original to
    <run_id>.original.json. Atomically writes updated JSON and regenerates markdown.
    """
    if not is_editable_path(path):
        raise ValueError(f"Trường '{path}' không được phép chỉnh sửa.")

    if not reviewer or not reviewer.strip():
        raise ValueError("Tên người chỉnh sửa không được để trống.")
    if not note or not note.strip():
        raise ValueError("Ghi chú chỉnh sửa không được để trống.")
    if not new_text or not new_text.strip():
        raise ValueError("Nội dung chỉnh sửa không được để trống.")

    e_path = Path(editorial_path)
    r_path = Path(review_path)

    if not e_path.exists():
        raise ArtifactError(e_path, "Editorial package file not found")

    raw_data = json.loads(e_path.read_text(encoding="utf-8"))
    before_text = _get_nested_field(raw_data, path)

    # Copy to original backup on first edit if not exists
    run_id = e_path.stem
    orig_path = e_path.parent / f"{run_id}.original.json"
    if not orig_path.exists():
        orig_path.write_text(json.dumps(raw_data, indent=2, ensure_ascii=False), encoding="utf-8")

    # Apply edit to in-memory dict
    _set_nested_field(raw_data, path, new_text.strip())

    # Re-validate with Pydantic model BEFORE writing
    pkg = EditorialPackage.model_validate(raw_data)

    # Atomic write to editorial JSON
    with tempfile.NamedTemporaryFile("w", dir=e_path.parent, delete=False, encoding="utf-8") as tf:
        tf.write(pkg.model_dump_json(indent=2))
        temp_name = tf.name
    os.replace(temp_name, e_path)

    # Regenerate preview markdown if research file exists
    research_path = e_path.parent.parent / "research" / f"{run_id}.json"
    if research_path.exists():
        try:
            research = load_research(research_path)
            md_preview = render_markdown(pkg, research)
            md_path = e_path.with_suffix(".md")
            md_path.write_text(md_preview, encoding="utf-8")
        except Exception as err:
            logger.warning("Could not regenerate preview markdown: %s", err)

    # Append edit to review file
    r_path.parent.mkdir(parents=True, exist_ok=True)
    review_data = load_review(r_path)
    review_data["run_id"] = run_id
    edit_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "reviewer": reviewer.strip(),
        "path": path,
        "before": before_text,
        "after": new_text.strip(),
        "note": note.strip(),
    }
    review_data["edits"].append(edit_entry)

    with tempfile.NamedTemporaryFile("w", dir=r_path.parent, delete=False, encoding="utf-8") as tf:
        json.dump(review_data, tf, indent=2, ensure_ascii=False)
        temp_review_name = tf.name
    os.replace(temp_review_name, r_path)

