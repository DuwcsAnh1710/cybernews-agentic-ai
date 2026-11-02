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

    # Check for presence of all 4 main tabs with Material Symbols
    tab_labels = {t.label for t in at.tabs}
    expected_main_tabs = {
        ":material/table_chart: Bảng tin đã duyệt",
        ":material/article: Tóm tắt & Báo cáo",
        ":material/edit_document: Phòng Biên tập & Xuất bản",
        ":material/health_and_safety: Giám sát Hệ thống",
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
    assert "02/11/2026 09:08" in label_unrev
    assert "📅" not in label_unrev
    assert "2 tin" in label_unrev
    assert "[Chờ duyệt]" in label_unrev
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
    assert "[Đã duyệt]" in label_appr
    assert "🟢" not in label_appr

    # 3. Rejected
    rev_data["decisions"].append(
        {"timestamp": "2026-11-02T09:11:00Z", "decision": "reject", "reviewer": "Bob"}
    )
    rev_path.write_text(json.dumps(rev_data), encoding="utf-8")
    label_rej = format_run_display(r_artifact, output_dir)
    assert "[Từ chối]" in label_rej
    assert "🔴" not in label_rej

    # 4. Changes requested
    rev_data["decisions"].append(
        {"timestamp": "2026-11-02T09:12:00Z", "decision": "request_changes", "reviewer": "Bob"}
    )
    rev_path.write_text(json.dumps(rev_data), encoding="utf-8")
    label_chg = format_run_display(r_artifact, output_dir)
    assert "[Cần sửa]" in label_chg
    assert "⚠️" not in label_chg


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
    assert label_sa == "02/11/2026 09:08 (Standalone)"
    assert "📅" not in label_sa

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


def test_ui_app_action_bar_buttons_and_material_symbols(tmp_path: Path) -> None:
    """Verify Action Bar buttons, Material Symbols, and 1-Click Approve & Export."""
    output_dir = tmp_path / "output"
    r_dir = output_dir / "research"
    e_dir = output_dir / "editorial"
    r_dir.mkdir(parents=True)
    e_dir.mkdir(parents=True)

    run_id = "test-action-bar-run"
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

    # Check Action Bar buttons rendered
    btn_labels = [b.label for b in at.button]
    assert any("Phê duyệt" in b for b in btn_labels)
    assert any("Yêu cầu sửa" in b for b in btn_labels)
    assert any("Từ chối" in b for b in btn_labels)
    assert any("Phê duyệt & Xuất bản trọn bộ" in b for b in btn_labels)

    # Check Material Symbol icons on buttons
    btn_icons = [getattr(b, "icon", None) for b in at.button]
    assert ":material/check_circle:" in btn_icons
    assert ":material/edit_note:" in btn_icons
    assert ":material/cancel:" in btn_icons
    assert ":material/publish:" in btn_icons


def test_ui_app_zip_export_download_button_when_bundle_exists(tmp_path: Path) -> None:
    """Verify ZIP download button renders with :material/folder_zip: when export bundle exists."""
    output_dir = tmp_path / "output"
    r_dir = output_dir / "research"
    e_dir = output_dir / "editorial"
    r_dir.mkdir(parents=True)
    e_dir.mkdir(parents=True)

    run_id = "test-export-zip-run"
    res = make_sample_research(run_id)
    pkg = make_sample_editorial(run_id)

    (r_dir / f"{run_id}.json").write_text(res.model_dump_json(), encoding="utf-8")
    (e_dir / f"{run_id}.json").write_text(pkg.model_dump_json(), encoding="utf-8")

    # Generate bundle in export/run_id
    from cyberpulse.export import export_editorial_bundle
    export_root = Path("export")
    export_editorial_bundle(
        package=pkg,
        run_id=run_id,
        output_dir=output_dir,
        export_root=export_root,
    )

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

    try:
        app_path = Path("src/cyberpulse/ui/app.py").resolve()
        at = AppTest.from_file(str(app_path), default_timeout=15)
        sys.argv = ["streamlit", "run", str(app_path), "--", "--settings", str(settings_file)]
        at.run()

        assert not at.exception
        dl_labels = [b.label for b in at.download_button]
        assert any("Tải trọn bộ xuất bản (.zip)" in l for l in dl_labels)
        dl_icons = [getattr(b, "icon", None) for b in at.download_button]
        assert ":material/folder_zip:" in dl_icons
    finally:
        # Cleanup created export dir
        bundle_path = export_root / run_id
        if bundle_path.exists():
            import shutil
            shutil.rmtree(bundle_path, ignore_errors=True)


def test_ui_app_public_portal_button_and_generation(tmp_path: Path) -> None:
    """Verify Tab 3 renders Public Portal button with :material/public: and clicking it generates portal."""
    output_dir = tmp_path / "output"
    r_dir = output_dir / "research"
    e_dir = output_dir / "editorial"
    r_dir.mkdir(parents=True)
    e_dir.mkdir(parents=True)

    run_id = "test-portal-ui-run"
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

    dist_dir = Path("dist")
    try:
        app_path = Path("src/cyberpulse/ui/app.py").resolve()
        at = AppTest.from_file(str(app_path), default_timeout=15)
        sys.argv = ["streamlit", "run", str(app_path), "--", "--settings", str(settings_file)]
        at.run()

        assert not at.exception
        # 1. Verify button presence and icon
        portal_btns = [b for b in at.button if "Trang Độc Giả" in b.label]
        assert len(portal_btns) >= 1
        assert getattr(portal_btns[0], "icon", None) == ":material/public:"

        # 2. Click button to trigger portal build
        portal_btns[0].click().run()
        assert not at.exception

        # 3. Verify success message and generated files
        success_msgs = [s.value for s in at.success]
        assert any("Đã tạo trang web độc giả thành công" in msg for msg in success_msgs)
        assert (dist_dir / "index.html").exists()
        assert (dist_dir / "archive.html").exists()
    finally:
        if dist_dir.exists():
            import shutil
            shutil.rmtree(dist_dir, ignore_errors=True)


