"""Integration and AppTest tests for CyberPulse AI Streamlit app (Task T4.3)."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import pytest

from tests.test_ui_data import make_sample_editorial, make_sample_research

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
