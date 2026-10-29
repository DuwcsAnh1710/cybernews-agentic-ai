"""Pytest fixtures for CyberPulse test suite."""

from __future__ import annotations

from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def _isolate_from_real_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every test hermetic: never read the developer's real .env and
    never inherit GEMINI_* variables, so no test can reach the live API."""
    monkeypatch.setattr("cyberpulse.cli.load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr("cyberpulse.config.load_dotenv", lambda *a, **k: False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)


@pytest.fixture
def valid_settings_toml() -> str:
    return """project_name = "CyberPulse AI"
topics = ["cybersecurity", "vulnerability"]
items_per_run = 5
lookback_hours = 48
output_language = "vi"
log_level = "INFO"

[paths]
data_dir = "data"
output_dir = "output"
log_dir = "logs"
"""


@pytest.fixture
def valid_sources_toml() -> str:
    return """# URLs not yet verified; verified in T2.1

[[sources]]
name = "The Hacker News"
url = "https://feeds.feedburner.com/TheHackersNews"
enabled = true

[[sources]]
name = "BleepingComputer"
url = "https://www.bleepingcomputer.com/feed/"
enabled = false
"""


@pytest.fixture
def config_paths(tmp_path: Path, valid_settings_toml: str, valid_sources_toml: str) -> tuple[Path, Path]:
    settings_file = tmp_path / "settings.toml"
    sources_file = tmp_path / "sources.toml"

    # Make sure paths in settings point inside tmp_path
    data_dir = tmp_path / "data"
    output_dir = tmp_path / "output"
    log_dir = tmp_path / "logs"

    settings_content = f"""project_name = "CyberPulse AI"
topics = ["cybersecurity", "vulnerability"]
items_per_run = 5
lookback_hours = 48
output_language = "vi"
log_level = "INFO"

[paths]
data_dir = "{data_dir.as_posix()}"
output_dir = "{output_dir.as_posix()}"
log_dir = "{log_dir.as_posix()}"
"""
    settings_file.write_text(settings_content, encoding="utf-8")
    sources_file.write_text(valid_sources_toml, encoding="utf-8")

    return settings_file, sources_file
