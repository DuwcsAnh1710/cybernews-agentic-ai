"""Integration and AppTest tests for CyberPulse AI Streamlit app (Task T4.3)."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import pytest

from tests.test_ui_data import make_sample_editorial, make_sample_research
from cyberpulse.ui.app import format_run_display
from cyberpulse.ui.data import RunArtifacts
from cyberpulse.ui.review import sha256_of_file

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest


def test_core_modules_do_not_import_streamlit(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC 2: Core modules import without Streamlit installed/available."""
    # Block streamlit import
    monkeypatch.setitem(sys.modules, "streamlit", None)

    # These imports MUST succeed without raising ImportError
    import cyberpulse.agents
    import cyberpulse.cli
    import cyberpulse.ui.data
    import cyberpulse.ui.review

    assert cyberpulse.cli is not None
    assert cyberpulse.agents is not None
    assert cyberpulse.ui.data is not None
    assert cyberpulse.ui.review is not None


def test_ui_app_renders_tabs_and_missing_validation_hint(tmp_path: Path) -> None:
    """AC 7: AppTest runs without exception, displays tabs, and shows validate hint when missing."""
    output_dir = tmp_path / "output"
    r_dir = output_dir / "research"
    e_dir = output_dir / "editorial"
    r_dir.mkdir(parents=True)
    e_dir.mkdir(parents=True)

    run_id = "sample-run-apptest"
    res = make_sample_research(run_id)
    pkg = make_sample_editorial(run_id)

    (r_dir / f"{run_id}.json").write_text(res.model_dump_json(), encoding="utf-8")
    (e_dir / f"{run_id}.json").write_text(pkg.model_dump_json(), encoding="utf-8")

    settings_file = tmp_path / "settings.toml"
    settings_file.write_text(
        f"""project_name = "CyberPulse AI Test"
topics = ["cybersecurity"]
[paths]
data_dir = "{str(tmp_path / "data").replace('\\', '/')}"
output_dir = "{str(output_dir).replace('\\', '/')}"
log_dir = "{str(tmp_path / "logs").replace('\\', '/')}"
[research]
max_per_source = 2
[editorial]
podcast_target_words = 300
podcast_speakers = ["Minh", "Lan"]
video_max_total_sec = 60
[validator]
llm_review = false
""",
        encoding="utf-8",
    )

    app_path = Path("src/cyberpulse/ui/app.py").resolve()
    at = AppTest.from_file(str(app_path), default_timeout=15)
    # Pass --settings argument to app
    sys.argv = ["streamlit", "run", str(app_path), "--", "--settings", str(settings_file)]

    at.run()

    # Assert no unhandled exceptions
    assert not at.exception

    # Check for presence of all 4 main tabs
    tab_labels = {t.label for t in at.tabs}
    expected_main_tabs = {
        "Nguồn & Dữ liệu",
        "Research Agent",
        "Editorial Studio",
        "Đối soát & Duyệt",
    }
    assert expected_main_tabs.issubset(tab_labels)

    # Check missing validation warning hint
    warnings = [w.value for w in at.warning]
    assert any("cyberpulse validate" in w for w in warnings)


def test_format_run_display_standard_and_statuses(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    r_dir = output_dir / "research"
    e_dir = output_dir / "editorial"
    rev_dir = output_dir / "review"
    r_dir.mkdir(parents=True)
    e_dir.mkdir(parents=True)
    rev_dir.mkdir(parents=True)

    run_id = "run-20261102T020834Z-180381df"
    res = make_sample_research(run_id)
    pkg = make_sample_editorial(run_id)

    res_path = r_dir / f"{run_id}.json"
    ed_path = e_dir / f"{run_id}.json"
    rev_path = rev_dir / f"{run_id}.json"

    res_path.write_text(res.model_dump_json(), encoding="utf-8")
    ed_path.write_text(pkg.model_dump_json(), encoding="utf-8")

    r_artifact = RunArtifacts(
        run_id=run_id,
        research_path=res_path,
        editorial_path=ed_path,
        preview_path=None,
        validation_path=None,
        review_path=rev_path,
        mtime=1.0,
        has_research=True,
        has_editorial=True,
        has_validation=False,
        has_review=False,
    )

    # 1. Unreviewed (no decisions recorded yet)
    label_unrev = format_run_display(r_artifact, output_dir)
    assert "📅 02/11/2026 09:08" in label_unrev
    assert "2 tin" in label_unrev
    assert "[🟡 Chờ duyệt]" in label_unrev
    assert "(180381df)" in label_unrev

    # 2. Approved
    ed_hash = sha256_of_file(ed_path)
    rev_data = {
        "run_id": run_id,
        "decisions": [
            {
                "timestamp": "2026-11-02T09:10:00Z",
                "decision": "approve",
                "reviewer": "Alice",
                "editorial_sha256": ed_hash,
            }
        ],
        "edits": [],
    }
    rev_path.write_text(json.dumps(rev_data), encoding="utf-8")
    label_appr = format_run_display(r_artifact, output_dir)
    assert "[🟢 Đã duyệt]" in label_appr

    # 3. Rejected
    rev_data["decisions"].append(
        {"timestamp": "2026-11-02T09:11:00Z", "decision": "reject", "reviewer": "Bob"}
    )
    rev_path.write_text(json.dumps(rev_data), encoding="utf-8")
    label_rej = format_run_display(r_artifact, output_dir)
    assert "[🔴 Từ chối]" in label_rej

    # 4. Changes requested
    rev_data["decisions"].append(
        {"timestamp": "2026-11-02T09:12:00Z", "decision": "request_changes", "reviewer": "Bob"}
    )
    rev_path.write_text(json.dumps(rev_data), encoding="utf-8")
    label_chg = format_run_display(r_artifact, output_dir)
    assert "[⚠️ Cần sửa]" in label_chg


def test_format_run_display_standalone_and_fallback(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True)

    # Standalone run_id
    r_standalone = RunArtifacts(
        run_id="standalone-20261102T020834Z",
        research_path=None,
        editorial_path=None,
        preview_path=None,
        validation_path=None,
        review_path=None,
        mtime=1.0,
        has_research=False,
        has_editorial=False,
        has_validation=False,
        has_review=False,
    )
    label_sa = format_run_display(r_standalone, output_dir)
    assert label_sa == "📅 02/11/2026 09:08 (Standalone)"

    # Fallback non-standard run_id
    r_fallback = RunArtifacts(
        run_id="custom-pipeline-run-001",
        research_path=None,
        editorial_path=None,
        preview_path=None,
        validation_path=None,
        review_path=None,
        mtime=1.0,
        has_research=False,
        has_editorial=False,
        has_validation=False,
        has_review=False,
    )
    label_fb = format_run_display(r_fallback, output_dir)
    assert label_fb == "custom-pipeline-run-001"


def test_ui_app_podcast_audio_player_when_mp3_exists(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    r_dir = output_dir / "research"
    e_dir = output_dir / "editorial"
    media_dir = output_dir / "media" / "podcast"
    r_dir.mkdir(parents=True)
    e_dir.mkdir(parents=True)
    media_dir.mkdir(parents=True)

    run_id = "run-20261102T020834Z-180381df"
    res = make_sample_research(run_id)
    pkg = make_sample_editorial(run_id)

    (r_dir / f"{run_id}.json").write_text(res.model_dump_json(), encoding="utf-8")
    (e_dir / f"{run_id}.json").write_text(pkg.model_dump_json(), encoding="utf-8")

    # Create dummy mp3, srt, json
    mp3_file = media_dir / f"{run_id}.mp3"
    srt_file = media_dir / f"{run_id}.srt"
    json_file = media_dir / f"{run_id}.json"
    mp3_file.write_bytes(b"\xff\xf3\x64\xc4" + b"\x00" * 140)
    srt_file.write_text("1\n00:00:00,000 --> 00:00:02,000\n[Minh] Chào Lan.\n", encoding="utf-8")
    json_file.write_text(json.dumps({"run_id": run_id, "total_duration_sec": 12.5}), encoding="utf-8")

    settings_file = tmp_path / "settings.toml"
    settings_file.write_text(
        f"""project_name = "CyberPulse AI Test"
topics = ["cybersecurity"]
[paths]
data_dir = "{str(tmp_path / "data").replace('\\', '/')}"
output_dir = "{str(output_dir).replace('\\', '/')}"
log_dir = "{str(tmp_path / "logs").replace('\\', '/')}"
[research]
max_per_source = 2
[editorial]
podcast_target_words = 300
podcast_speakers = ["Minh", "Lan"]
video_max_total_sec = 60
[validator]
llm_review = false
""",
        encoding="utf-8",
    )

    app_path = Path("src/cyberpulse/ui/app.py").resolve()
    at = AppTest.from_file(str(app_path), default_timeout=15)
    sys.argv = ["streamlit", "run", str(app_path), "--", "--settings", str(settings_file)]
    at.run()

    assert not at.exception
    # Audio player widget rendered
    assert len(at.get("audio")) >= 1
    # Download button for SRT rendered
    dl_labels = [b.label for b in at.download_button]
    assert any("Tải tệp phụ đề (.srt)" in l for l in dl_labels)
    # Success callout rendered
    successes = [s.value for s in at.success]
    assert any("File âm thanh Podcast hoàn chỉnh" in s for s in successes)


def test_ui_app_podcast_button_when_mp3_absent(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    r_dir = output_dir / "research"
    e_dir = output_dir / "editorial"
    r_dir.mkdir(parents=True)
    e_dir.mkdir(parents=True)

    run_id = "run-20261102T020834Z-180381df"
    res = make_sample_research(run_id)
    pkg = make_sample_editorial(run_id)

    (r_dir / f"{run_id}.json").write_text(res.model_dump_json(), encoding="utf-8")
    (e_dir / f"{run_id}.json").write_text(pkg.model_dump_json(), encoding="utf-8")

    settings_file = tmp_path / "settings.toml"
    settings_file.write_text(
        f"""project_name = "CyberPulse AI Test"
topics = ["cybersecurity"]
[paths]
data_dir = "{str(tmp_path / "data").replace('\\', '/')}"
output_dir = "{str(output_dir).replace('\\', '/')}"
log_dir = "{str(tmp_path / "logs").replace('\\', '/')}"
[research]
max_per_source = 2
[editorial]
podcast_target_words = 300
podcast_speakers = ["Minh", "Lan"]
video_max_total_sec = 60
[validator]
llm_review = false
""",
        encoding="utf-8",
    )

    app_path = Path("src/cyberpulse/ui/app.py").resolve()
    at = AppTest.from_file(str(app_path), default_timeout=15)
    sys.argv = ["streamlit", "run", str(app_path), "--", "--settings", str(settings_file)]
    at.run()

    assert not at.exception
    # Audio player should NOT be rendered
    assert len(at.get("audio")) == 0
    # Info message rendered
    infos = [i.value for i in at.info]
    assert any("Chưa có file âm thanh Podcast" in i for i in infos)
    # 1-click button rendered
    btn_labels = [b.label for b in at.button]
    assert any("Tạo Audio Podcast" in b for b in btn_labels)
