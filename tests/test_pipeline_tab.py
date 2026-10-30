"""AppTest tests for Pipeline Runner Streamlit tab (Tab 5) (Task T4.4)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest


def _create_test_settings(tmp_path: Path) -> tuple[Path, Path]:
    """Helper to create dummy settings and directory structure for AppTest."""
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "research").mkdir(parents=True, exist_ok=True)
    (output_dir / "editorial").mkdir(parents=True, exist_ok=True)
    (output_dir / "validation").mkdir(parents=True, exist_ok=True)

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
    return settings_file, output_dir


def test_pipeline_tab_renders_and_has_five_buttons(tmp_path: Path) -> None:
    """AC 11 & A7: Tab 5 renders and contains the 5 step run buttons."""
    settings_file, output_dir = _create_test_settings(tmp_path)
    app_path = Path("src/cyberpulse/ui/app.py").resolve()

    sys.argv = ["streamlit", "run", str(app_path), "--", "--settings", str(settings_file)]
    at = AppTest.from_file(str(app_path), default_timeout=15)
    at.run()

    assert not at.exception
    tab_labels = {t.label for t in at.tabs}
    assert "Pipeline Runner" in tab_labels

    button_labels = {b.label for b in at.button}
    expected_buttons = {
        "Chạy Fetch",
        "Chạy Collect",
        "Chạy Research",
        "Chạy Editorial",
        "Chạy Validate",
    }
    assert expected_buttons.issubset(button_labels)


def test_pipeline_tab_buttons_disabled_when_locked(tmp_path: Path) -> None:
    """A7: All 5 run buttons must be disabled when an active lock file exists."""
    settings_file, output_dir = _create_test_settings(tmp_path)
    app_path = Path("src/cyberpulse/ui/app.py").resolve()

    # Create lock file with owner_pid = os.getpid(), child_pid = None
    lock_dir = output_dir / "pipeline"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / "pipeline.lock"
    lock_payload = {
        "owner_pid": os.getpid(),
        "child_pid": None,
        "step": "collect",
        "job_id": "job-test-locked",
        "started_at": "2026-10-30T00:00:00Z",
    }
    lock_file.write_text(json.dumps(lock_payload), encoding="utf-8")

    sys.argv = ["streamlit", "run", str(app_path), "--", "--settings", str(settings_file)]
    at = AppTest.from_file(str(app_path), default_timeout=15)
    at.run()

    assert not at.exception

    expected_buttons = [
        "Chạy Fetch",
        "Chạy Collect",
        "Chạy Research",
        "Chạy Editorial",
        "Chạy Validate",
    ]
    for btn_label in expected_buttons:
        btn = next((b for b in at.button if b.label == btn_label), None)
        assert btn is not None, f"Button '{btn_label}' not found"
        assert btn.disabled is True, f"Button '{btn_label}' should be disabled when locked"

