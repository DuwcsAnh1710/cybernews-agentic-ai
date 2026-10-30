"""Unit tests for cyberpulse.ui.review module (Task T4.3)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import pytest

from cyberpulse.ui.data import validation_freshness
from cyberpulse.ui.review import (
    ReviewRuleError,
    apply_text_edit,
    current_status,
    is_editable_path,
    load_review,
    record_decision,
)
from tests.test_ui_data import make_sample_editorial, make_sample_research


def test_is_editable_path() -> None:
    assert is_editable_path("digest.title") is True
    assert is_editable_path("digest.intro") is True
    assert is_editable_path("digest.sections[0].heading") is True
    assert is_editable_path("digest.sections[0].body.text") is True
    assert is_editable_path("podcast.segments[1].line.text") is True
    assert is_editable_path("video.scenes[0].on_screen_text") is True
    assert is_editable_path("video.scenes[0].narration.text") is True
    assert is_editable_path("captions[0].text") is True

    # Non-editable paths
    assert is_editable_path("digest.sections[0].body.cited_article_ids") is False
    assert is_editable_path("video.scenes[0].duration_sec") is False
    assert is_editable_path("captions[0].platform") is False
    assert is_editable_path("podcast.segments[0].speaker") is False


def test_record_decision_validation_rules(tmp_path: Path) -> None:
    e_file = tmp_path / "editorial.json"
    v_file = tmp_path / "validation.json"
    r_file = tmp_path / "review.json"

    pkg = make_sample_editorial("run-001")
    e_file.write_text(pkg.model_dump_json(), encoding="utf-8")

    # 1. Reviewer name empty
    with pytest.raises(ReviewRuleError, match="Tên người duyệt"):
        record_decision(r_file, e_file, v_file, decision="approve", reviewer="", note="")

    # 2. Reject / Request changes requires note
    with pytest.raises(ReviewRuleError, match="Ghi chú bắt buộc"):
        record_decision(r_file, e_file, v_file, decision="reject", reviewer="Alice", note="")
    with pytest.raises(ReviewRuleError, match="Ghi chú bắt buộc"):
        record_decision(r_file, e_file, v_file, decision="request_changes", reviewer="Alice", note="")

    # 3. Approve when validation missing
    with pytest.raises(ReviewRuleError, match="Kết quả kiểm tra.*thiếu hoặc đã cũ"):
        record_decision(r_file, e_file, None, decision="approve", reviewer="Alice", note="")

    # 4. Approve when validation stale
    v_file.write_text(json.dumps({"run_id": "run-001", "passed": True, "errors": [], "warnings": []}), encoding="utf-8")
    past = e_file.stat().st_mtime - 100
    os.utime(v_file, (past, past))
    with pytest.raises(ReviewRuleError, match="Kết quả kiểm tra.*thiếu hoặc đã cũ"):
        record_decision(r_file, e_file, v_file, decision="approve", reviewer="Alice", note="")

    # 5. Approve when validation failed
    now_time = e_file.stat().st_mtime + 10
    os.utime(v_file, (now_time, now_time))
    v_file.write_text(json.dumps({"run_id": "run-001", "passed": False, "errors": [{"code": "E_RUN_ID_MISMATCH"}], "warnings": []}), encoding="utf-8")
    with pytest.raises(ReviewRuleError, match="còn lỗi kiểm tra"):
        record_decision(r_file, e_file, v_file, decision="approve", reviewer="Alice", note="")

    # 6. Approve when warnings exist but not acknowledged
    v_file.write_text(json.dumps({
        "run_id": "run-001",
        "passed": True,
        "errors": [],
        "warnings": [{"code": "W_TERM_UNSUPPORTED", "message": "Unknown term"}],
    }), encoding="utf-8")
    os.utime(v_file, (now_time + 10, now_time + 10))
    with pytest.raises(ReviewRuleError, match="chấp nhận các cảnh báo"):
        record_decision(r_file, e_file, v_file, decision="approve", reviewer="Alice", note="", warnings_acknowledged=False)

    # 7. Approve succeeds when fresh, passed, and warnings acknowledged
    dec = record_decision(
        r_file,
        e_file,
        v_file,
        decision="approve",
        reviewer="Alice",
        note="Approved after warning ack",
        warnings_acknowledged=True,
    )
    assert dec["decision"] == "approve"
    assert dec["reviewer"] == "Alice"
    assert dec["warnings_acknowledged"] is True

    review_data = load_review(r_file)
    assert len(review_data["decisions"]) == 1


def test_current_status_reverts_on_editorial_change(tmp_path: Path) -> None:
    e_file = tmp_path / "editorial.json"
    v_file = tmp_path / "validation.json"
    r_file = tmp_path / "review.json"

    pkg = make_sample_editorial("run-001")
    e_file.write_text(pkg.model_dump_json(), encoding="utf-8")
    v_file.write_text(json.dumps({"run_id": "run-001", "passed": True, "errors": [], "warnings": []}), encoding="utf-8")

    # Record approve
    record_decision(r_file, e_file, v_file, decision="approve", reviewer="Alice", note="")
    rev_data = load_review(r_file)
    assert current_status(rev_data, e_file) == "approved"

    # Modify editorial file
    pkg.digest.title = "Tiêu đề mới sau khi duyệt"
    e_file.write_text(pkg.model_dump_json(), encoding="utf-8")

    # Status MUST revert to unreviewed because sha256 changed
    assert current_status(rev_data, e_file) == "unreviewed"


def test_apply_text_edit(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    e_dir = output_dir / "editorial"
    r_dir = output_dir / "research"
    rev_dir = output_dir / "review"
    e_dir.mkdir(parents=True)
    r_dir.mkdir(parents=True)
    rev_dir.mkdir(parents=True)

    e_file = e_dir / "run-001.json"
    r_file = r_dir / "run-001.json"
    rev_file = rev_dir / "run-001.json"
    v_file = output_dir / "validation" / "run-001.json"
    v_file.parent.mkdir(parents=True)

    pkg = make_sample_editorial("run-001")
    res = make_sample_research("run-001")
    e_file.write_text(pkg.model_dump_json(), encoding="utf-8")
    r_file.write_text(res.model_dump_json(), encoding="utf-8")
    v_file.write_text(json.dumps({"run_id": "run-001", "passed": True}), encoding="utf-8")
    # Validation file was created in the past before the edit
    past_val = e_file.stat().st_mtime - 100
    os.utime(v_file, (past_val, past_val))

    # 1. Non-editable path raises ValueError
    with pytest.raises(ValueError, match="không được phép chỉnh sửa"):
        apply_text_edit(
            e_file,
            rev_file,
            path="video.scenes[0].duration_sec",
            new_text="15.0",
            reviewer="Bob",
            note="Invalid",
        )

    # 2. Invalid empty text raises ValueError
    with pytest.raises(ValueError, match="không được để trống"):
        apply_text_edit(
            e_file,
            rev_file,
            path="digest.sections[0].heading",
            new_text="   ",
            reviewer="Bob",
            note="Clear heading",
        )

    # 3. Valid edit: changes section heading
    apply_text_edit(
        e_file,
        rev_file,
        path="digest.sections[0].heading",
        new_text="Tiêu đề đã được sửa bởi BTV (mẫu)",
        reviewer="Bob",
        note="Sửa tiêu đề rõ nghĩa hơn",
    )

    # Check original backup created once
    orig_file = e_dir / "run-001.original.json"
    assert orig_file.exists()
    orig_data = json.loads(orig_file.read_text(encoding="utf-8"))
    assert orig_data["digest"]["sections"][0]["heading"] == "Tiêu đề mục 1 (mẫu)"

    # Check editorial file updated
    updated_pkg = json.loads(e_file.read_text(encoding="utf-8"))
    assert updated_pkg["digest"]["sections"][0]["heading"] == "Tiêu đề đã được sửa bởi BTV (mẫu)"

    # Check markdown preview regenerated
    md_file = e_dir / "run-001.md"
    assert md_file.exists()
    assert "Tiêu đề đã được sửa bởi BTV (mẫu)" in md_file.read_text(encoding="utf-8")

    # Check review file has edit entry
    review_data = load_review(rev_file)
    assert len(review_data["edits"]) == 1
    edit_entry = review_data["edits"][0]
    assert edit_entry["path"] == "digest.sections[0].heading"
    assert edit_entry["before"] == "Tiêu đề mục 1 (mẫu)"
    assert edit_entry["after"] == "Tiêu đề đã được sửa bởi BTV (mẫu)"
    assert edit_entry["reviewer"] == "Bob"

    # Check that validation freshness is now stale (because e_file was touched)
    assert validation_freshness(e_file, v_file) == "stale"

    # Second edit does NOT overwrite original backup
    apply_text_edit(
        e_file,
        rev_file,
        path="digest.sections[0].heading",
        new_text="Tiêu đề lần 2 (mẫu)",
        reviewer="Bob",
        note="Sửa lần 2",
    )
    orig_data2 = json.loads(orig_file.read_text(encoding="utf-8"))
    assert orig_data2["digest"]["sections"][0]["heading"] == "Tiêu đề mục 1 (mẫu)"
