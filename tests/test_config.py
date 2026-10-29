"""Tests for configuration parsing and validation."""

from __future__ import annotations

from pathlib import Path
import pytest

from cyberpulse.config import ConfigError, load_app_config, load_settings, load_sources


def test_valid_config_loads(config_paths: tuple[Path, Path]) -> None:
    """1. Valid config loads; items_per_run == 5 and runtime directories created."""
    settings_path, sources_path = config_paths
    settings, sources = load_app_config(settings_path, sources_path)

    assert settings.project_name == "CyberPulse AI"
    assert settings.items_per_run == 5
    assert settings.lookback_hours == 48
    assert settings.output_language == "vi"
    assert settings.log_level == "INFO"
    assert len(sources) == 2
    assert sources[0].enabled is True

    # Check directory creation
    assert settings.paths.data_dir.exists()
    assert settings.paths.output_dir.exists()
    assert settings.paths.log_dir.exists()


def test_missing_settings_file(tmp_path: Path) -> None:
    """2. Missing settings file -> ConfigError with file name."""
    missing_file = tmp_path / "nonexistent_settings.toml"
    with pytest.raises(ConfigError) as exc_info:
        load_settings(missing_file)

    msg = str(exc_info.value)
    assert str(missing_file) in msg or missing_file.name in msg
    assert "not found" in msg.lower()


@pytest.mark.parametrize("invalid_items", [0, 11, -1])
def test_invalid_items_per_run(tmp_path: Path, invalid_items: int) -> None:
    """3. items_per_run = 0 and = 11 -> ConfigError naming items_per_run."""
    settings_file = tmp_path / "settings_bad_items.toml"
    settings_file.write_text(
        f"""project_name = "CyberPulse AI"
topics = ["cybersecurity"]
items_per_run = {invalid_items}
lookback_hours = 48
output_language = "vi"
log_level = "INFO"

[paths]
data_dir = "data"
output_dir = "output"
log_dir = "logs"
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError) as exc_info:
        load_settings(settings_file)

    msg = str(exc_info.value)
    assert "items_per_run" in msg
    assert str(settings_file) in msg or settings_file.name in msg


def test_source_missing_url(tmp_path: Path) -> None:
    """4. Source missing url -> ConfigError naming url."""
    sources_file = tmp_path / "sources_missing_url.toml"
    sources_file.write_text(
        """[[sources]]
name = "The Hacker News"
enabled = true
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError) as exc_info:
        load_sources(sources_file)

    msg = str(exc_info.value)
    assert "url" in msg
    assert str(sources_file) in msg or sources_file.name in msg


def test_all_sources_disabled(tmp_path: Path) -> None:
    """5. All sources disabled -> ConfigError."""
    sources_file = tmp_path / "sources_disabled.toml"
    sources_file.write_text(
        """[[sources]]
name = "The Hacker News"
url = "https://feeds.feedburner.com/TheHackersNews"
enabled = false

[[sources]]
name = "BleepingComputer"
url = "https://www.bleepingcomputer.com/feed/"
enabled = false
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError) as exc_info:
        load_sources(sources_file)

    msg = str(exc_info.value)
    assert "at least one source must be enabled" in msg.lower()
    assert str(sources_file) in msg or sources_file.name in msg


def test_malformed_toml_syntax(tmp_path: Path) -> None:
    """Malformed TOML syntax raises ConfigError with file name."""
    bad_file = tmp_path / "malformed.toml"
    bad_file.write_text("invalid_toml = [unclosed_bracket", encoding="utf-8")

    with pytest.raises(ConfigError) as exc_info:
        load_settings(bad_file)

    msg = str(exc_info.value)
    assert "malformed toml" in msg.lower()
    assert str(bad_file) in msg or bad_file.name in msg


def test_invalid_lookback_hours(tmp_path: Path) -> None:
    """lookback_hours <= 0 raises ConfigError naming lookback_hours."""
    settings_file = tmp_path / "bad_lookback.toml"
    settings_file.write_text(
        """project_name = "CyberPulse AI"
topics = ["cybersecurity"]
items_per_run = 5
lookback_hours = 0
output_language = "vi"
log_level = "INFO"

[paths]
data_dir = "data"
output_dir = "output"
log_dir = "logs"
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError) as exc_info:
        load_settings(settings_file)

    msg = str(exc_info.value)
    assert "lookback_hours" in msg


def test_validator_config_defaults(tmp_path: Path) -> None:
    """ValidatorConfig defaults loaded correctly when not specified."""
    settings_file = tmp_path / "settings_no_validator.toml"
    settings_file.write_text(
        """project_name = "CyberPulse AI"
topics = ["cybersecurity"]
""",
        encoding="utf-8",
    )
    settings = load_settings(settings_file)
    assert settings.validator.llm_review is True
    assert settings.validator.podcast_word_tolerance == 0.2
    assert settings.validator.section_words_min == 60
    assert settings.validator.section_words_max == 120
    assert settings.validator.term_allowlist == ["Trung"]


@pytest.mark.parametrize("tol", [0.01, 0.6])
def test_validator_invalid_podcast_tolerance(tmp_path: Path, tol: float) -> None:
    """podcast_word_tolerance outside [0.05, 0.5] raises ConfigError."""
    settings_file = tmp_path / "settings_bad_tol.toml"
    settings_file.write_text(
        f"""project_name = "CyberPulse AI"
topics = ["cybersecurity"]
[validator]
podcast_word_tolerance = {tol}
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_settings(settings_file)
    assert "podcast_word_tolerance" in str(exc_info.value)


def test_validator_invalid_section_words_range(tmp_path: Path) -> None:
    """section_words_min >= section_words_max raises ConfigError."""
    settings_file = tmp_path / "settings_bad_range.toml"
    settings_file.write_text(
        """project_name = "CyberPulse AI"
topics = ["cybersecurity"]
[validator]
section_words_min = 100
section_words_max = 50
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_settings(settings_file)
    assert "section_words_min" in str(exc_info.value)

