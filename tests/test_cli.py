"""Tests for CyberPulse CLI commands."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import pytest

from cyberpulse import __version__
from cyberpulse.cli import main
from cyberpulse.fetcher import FetchReport, RawItem, SourceResult


def test_cli_help(capsys: pytest.CaptureFixture[str]) -> None:
    """8. --help exits 0 and lists version and check-config."""
    ret = main(["--help"])
    assert ret == 0
    captured = capsys.readouterr()
    stdout = captured.out
    assert "version" in stdout
    assert "check-config" in stdout


def test_cli_version(capsys: pytest.CaptureFixture[str]) -> None:
    """version command exits 0 and prints package version."""
    ret = main(["version"])
    assert ret == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == f"cyberpulse {__version__}"


def test_cli_check_config_valid(
    config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valid config check exits 0 and prints details."""
    settings_path, sources_path = config_paths
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    ret = main(["check-config", "--settings", str(settings_path), "--sources", str(sources_path)])
    assert ret == 0

    captured = capsys.readouterr()
    out = captured.out
    assert "Project name: CyberPulse AI" in out
    assert "Topics: cybersecurity, vulnerability" in out
    assert "Items per run: 5" in out
    assert "Lookback hours: 48" in out
    assert "Output language: vi" in out
    assert "Enabled sources: 1" in out


def test_cli_secret_set_not_leaked(
    config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """6. Secret set to 'sk-test-SECRET123' -> output shows 'set'; stdout and log file do not contain 'SECRET123'."""
    settings_path, sources_path = config_paths
    secret_value = "sk-test-SECRET123"
    monkeypatch.setenv("GEMINI_API_KEY", secret_value)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    ret = main(["check-config", "--settings", str(settings_path), "--sources", str(sources_path)])
    assert ret == 0

    captured = capsys.readouterr()
    stdout = captured.out

    # Must show 'set'
    assert "GEMINI_API_KEY: set" in stdout

    # Must NOT contain secret or substring
    assert "SECRET123" not in stdout
    assert secret_value not in stdout

    # Verify log file also does not contain the secret
    log_file = settings_path.parent / "logs" / "cyberpulse.log"
    assert log_file.exists()
    log_content = log_file.read_text(encoding="utf-8")
    assert "SECRET123" not in log_content
    assert secret_value not in log_content


def test_cli_secret_unset(
    config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """7. Secret unset -> output shows missing; exit code 0."""
    settings_path, sources_path = config_paths
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    ret = main(["check-config", "--settings", str(settings_path), "--sources", str(sources_path)])
    assert ret == 0

    captured = capsys.readouterr()
    assert "GEMINI_API_KEY: missing" in captured.out
    assert "GEMINI_MODEL: missing" in captured.out


def test_cli_malformed_settings_exits_1(
    tmp_path: Path,
    config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """3. Malformed settings -> check-config exits 1 and names the bad field."""
    _, sources_path = config_paths
    bad_settings = tmp_path / "bad_settings.toml"
    bad_settings.write_text(
        """project_name = "CyberPulse AI"
topics = ["cybersecurity"]
items_per_run = 99
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

    ret = main(["check-config", "--settings", str(bad_settings), "--sources", str(sources_path)])
    assert ret == 1

    captured = capsys.readouterr()
    err = captured.err
    assert "items_per_run" in err
    assert "ConfigError" in err


def test_cli_init_db(
    config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """17. init-db with tmp settings (data_dir in tmp_path) -> exit 0, db file exists."""
    settings_path, _ = config_paths
    ret = main(["init-db", "--settings", str(settings_path)])
    assert ret == 0

    captured = capsys.readouterr()
    stdout = captured.out
    assert "Database initialized:" in stdout
    assert "(schema version 1)" in stdout

    db_path = settings_path.parent / "data" / "cyberpulse.db"
    assert db_path.exists()


def test_cli_collect_dry_run_exits_0_no_db(
    config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """17. CLI collect --dry-run with monkeypatched fetch_all -> exit 0, no DB file created."""
    settings_path, sources_path = config_paths
    now = datetime.now(timezone.utc)

    mock_items = [
        RawItem(
            source_name="Sample Source A",
            title="SAMPLE — Security Advisory",
            link="https://example.com/sample/item",
            published_at=now,
            fetched_at=now,
        )
    ]
    mock_results = [
        SourceResult(
            source_name="Sample Source A",
            url="https://example.com/sample",
            ok=True,
            item_count=1,
            elapsed_ms=45,
        )
    ]

    def mock_fetch_all(sources: list[object], cfg: object, client: object = None) -> FetchReport:
        return FetchReport(items=mock_items, results=mock_results)

    monkeypatch.setattr("cyberpulse.cli.fetch_all", mock_fetch_all)

    ret = main(["collect", "--dry-run", "--settings", str(settings_path), "--sources", str(sources_path)])
    assert ret == 0

    captured = capsys.readouterr()
    assert "Dry run: nothing saved" in captured.out
    assert "Kept articles: 1" in captured.out

    db_path = settings_path.parent / "data" / "cyberpulse.db"
    assert not db_path.exists()


def test_cli_collect_only_invalid_items_exits_3(
    config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """18. CLI collect with monkeypatched fetch_all returning only invalid items -> exit 3."""
    settings_path, sources_path = config_paths
    now = datetime.now(timezone.utc)

    mock_items = [
        RawItem(
            source_name="Sample Source A",
            title=None,
            link=None,
            published_at=now,
            fetched_at=now,
        ),
        RawItem(
            source_name="Sample Source A",
            title="SAMPLE — No Date",
            link="https://example.com/sample/no-date",
            published_at=None,
            fetched_at=now,
        ),
    ]
    mock_results = [
        SourceResult(
            source_name="Sample Source A",
            url="https://example.com/sample",
            ok=True,
            item_count=2,
            elapsed_ms=40,
        )
    ]

    def mock_fetch_all(sources: list[object], cfg: object, client: object = None) -> FetchReport:
        return FetchReport(items=mock_items, results=mock_results)

    monkeypatch.setattr("cyberpulse.cli.fetch_all", mock_fetch_all)

    ret = main(["collect", "--settings", str(settings_path), "--sources", str(sources_path)])
    assert ret == 3

    captured = capsys.readouterr()
    assert "Kept articles: 0" in captured.out
    assert "Dropped: 2" in captured.out
    assert "missing_link" in captured.out


def test_cli_collect_all_sources_failed_exits_2(
    config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """19. CLI collect with all sources failed -> exit 2."""
    settings_path, sources_path = config_paths

    mock_results = [
        SourceResult(
            source_name="Sample Source A",
            url="https://example.com/sample",
            ok=False,
            item_count=0,
            error="HTTP 500",
            http_status=500,
            elapsed_ms=10,
        )
    ]

    def mock_fetch_all(sources: list[object], cfg: object, client: object = None) -> FetchReport:
        return FetchReport(items=[], results=mock_results)

    monkeypatch.setattr("cyberpulse.cli.fetch_all", mock_fetch_all)

    ret = main(["collect", "--settings", str(settings_path), "--sources", str(sources_path)])
    assert ret == 2

    captured = capsys.readouterr()
    assert "[FAIL] Sample Source A: HTTP 500" in captured.out


def test_cli_llm_check_key_unset(
    config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """16. CLI llm-check with key unset -> exit 4 and exact skip message."""
    settings_path, _ = config_paths
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    ret = main(["llm-check", "--settings", str(settings_path)])
    assert ret == 4

    captured = capsys.readouterr()
    assert "GEMINI_API_KEY: missing — live LLM check skipped. See README \"LLM setup\"." in captured.out


def test_cli_llm_check_ratelimit_error_exits_5(
    config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """17. CLI llm-check with monkeypatched client raising LLMRateLimitError -> exit 5."""
    settings_path, _ = config_paths
    sentinel = "AIzaTEST-SENTINEL-123"
    monkeypatch.setenv("GEMINI_API_KEY", sentinel)

    from cyberpulse.llm import LLMRateLimitError

    class MockFailingClient:
        def generate_json(self, **kwargs: object) -> object:
            raise LLMRateLimitError("Quota limit reached")

    monkeypatch.setattr("cyberpulse.cli.create_llm_client", lambda cfg: MockFailingClient())

    ret = main(["llm-check", "--settings", str(settings_path)])
    assert ret == 5

    captured = capsys.readouterr()
    assert "[LLM ERROR] LLMRateLimitError: Quota limit reached" in captured.out
    assert sentinel not in captured.out
    assert sentinel not in captured.err


def test_cli_llm_check_success_exits_0(
    config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """18. CLI llm-check with monkeypatched client returning valid LLMCheckResponse -> exit 0."""
    settings_path, _ = config_paths
    monkeypatch.setenv("GEMINI_API_KEY", "mock-valid-key")

    from cyberpulse.cli import LLMCheckResponse
    from cyberpulse.llm import LLMResult

    class MockSuccessClient:
        def generate_json(self, **kwargs: object) -> object:
            return LLMResult(
                data=LLMCheckResponse(ok=True, language="vi", message="Xin chào! Chúc một ngày tốt lành."),
                raw_text='{"ok": true, "language": "vi", "message": "Xin chào! Chúc một ngày tốt lành."}',
                model="gemini-3.8-flash",
                input_tokens=15,
                output_tokens=10,
                attempts=1,
                latency_ms=180,
            )

    monkeypatch.setattr("cyberpulse.cli.create_llm_client", lambda cfg: MockSuccessClient())

    ret = main(["llm-check", "--settings", str(settings_path)])
    assert ret == 0

    captured = capsys.readouterr()
    out = captured.out
    assert "Model: gemini-3.8-flash" in out
    assert "Attempts: 1" in out
    assert "Tokens: in=15 out=10" in out
    assert "Latency: 180 ms" in out
    assert "Response: Xin chào! Chúc một ngày tốt lành." in out


def test_cli_llm_check_bad_config_exits_1(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI llm-check with malformed settings -> exit 1 and ConfigError."""
    bad_settings = tmp_path / "bad.toml"
    bad_settings.write_text("invalid = toml [broken", encoding="utf-8")

    ret = main(["llm-check", "--settings", str(bad_settings)])
    assert ret == 1

    captured = capsys.readouterr()
    assert "ConfigError" in captured.err


def test_cli_validate_success(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI validate exits 0 on valid package and writes report JSON."""
    from tests.test_validator import make_test_editorial, make_test_research

    research = make_test_research()
    pkg = make_test_editorial()

    r_file = tmp_path / "research.json"
    e_file = tmp_path / "editorial.json"
    r_file.write_text(research.model_dump_json(indent=2), encoding="utf-8")
    e_file.write_text(pkg.model_dump_json(indent=2), encoding="utf-8")

    settings_file = tmp_path / "settings.toml"
    settings_file.write_text(
        f"""project_name = "CyberPulse AI"
topics = ["cybersecurity"]
[paths]
data_dir = "{str(tmp_path / "data").replace('\\', '/')}"
output_dir = "{str(tmp_path / "output").replace('\\', '/')}"
log_dir = "{str(tmp_path / "logs").replace('\\', '/')}"
[editorial]
podcast_target_words = 300
podcast_speakers = ["Minh", "Lan"]
[validator]
llm_review = false
""",
        encoding="utf-8",
    )

    ret = main([
        "validate",
        "--research", str(r_file),
        "--editorial", str(e_file),
        "--settings", str(settings_file),
        "--no-llm-review",
    ])
    assert ret == 0

    captured = capsys.readouterr()
    assert "Passed: yes" in captured.out
    assert "Errors: 0" in captured.out

    report_path = tmp_path / "output" / "validation" / f"{pkg.run_id}.json"
    assert report_path.exists()


def test_cli_validate_errors_exit_8(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI validate exits 8 when validation errors are present."""
    from tests.test_validator import make_test_editorial, make_test_research

    research = make_test_research()
    pkg = make_test_editorial()
    # Introduce run_id mismatch
    pkg.run_id = "different-run-id"

    r_file = tmp_path / "research.json"
    e_file = tmp_path / "editorial.json"
    r_file.write_text(research.model_dump_json(indent=2), encoding="utf-8")
    e_file.write_text(pkg.model_dump_json(indent=2), encoding="utf-8")

    settings_file = tmp_path / "settings.toml"
    settings_file.write_text(
        f"""project_name = "CyberPulse AI"
topics = ["cybersecurity"]
[paths]
data_dir = "{str(tmp_path / "data").replace('\\', '/')}"
output_dir = "{str(tmp_path / "output").replace('\\', '/')}"
log_dir = "{str(tmp_path / "logs").replace('\\', '/')}"
[editorial]
podcast_target_words = 300
podcast_speakers = ["Minh", "Lan"]
[validator]
llm_review = false
""",
        encoding="utf-8",
    )

    ret = main([
        "validate",
        "--research", str(r_file),
        "--editorial", str(e_file),
        "--settings", str(settings_file),
    ])
    assert ret == 8

    captured = capsys.readouterr()
    assert "Passed: no" in captured.out
    assert "[ERROR] E_RUN_ID_MISMATCH" in captured.out


def test_cli_validate_missing_file_exit_7(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI validate exits 7 when research or editorial file is missing."""
    settings_file = tmp_path / "settings.toml"
    settings_file.write_text(
        """project_name = "CyberPulse AI"
topics = ["cybersecurity"]
""",
        encoding="utf-8",
    )

    missing_r = tmp_path / "nonexistent_research.json"
    missing_e = tmp_path / "nonexistent_editorial.json"

    ret = main([
        "validate",
        "--research", str(missing_r),
        "--editorial", str(missing_e),
        "--settings", str(settings_file),
    ])
    assert ret == 7

    captured = capsys.readouterr()
    assert "not found" in captured.err.lower()


def test_cli_validate_db_unchanged(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI validate performs NO database writes and leaves row counts unchanged."""
    import sqlite3
    from tests.test_validator import make_test_editorial, make_test_research
    from cyberpulse.storage import get_db_path, init_db

    research = make_test_research()
    pkg = make_test_editorial()

    r_file = tmp_path / "research.json"
    e_file = tmp_path / "editorial.json"
    r_file.write_text(research.model_dump_json(indent=2), encoding="utf-8")
    e_file.write_text(pkg.model_dump_json(indent=2), encoding="utf-8")

    db_file = tmp_path / "data" / "cyberpulse.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_file)
    init_db(conn)
    cursor = conn.cursor()
    cursor.execute("SELECT count(*) FROM articles")
    count_before = cursor.fetchone()[0]
    conn.close()

    settings_file = tmp_path / "settings.toml"
    settings_file.write_text(
        f"""project_name = "CyberPulse AI"
topics = ["cybersecurity"]
[paths]
data_dir = "{str(tmp_path / "data").replace('\\', '/')}"
output_dir = "{str(tmp_path / "output").replace('\\', '/')}"
log_dir = "{str(tmp_path / "logs").replace('\\', '/')}"
[editorial]
podcast_target_words = 300
podcast_speakers = ["Minh", "Lan"]
[validator]
llm_review = false
""",
        encoding="utf-8",
    )

    ret = main([
        "validate",
        "--research", str(r_file),
        "--editorial", str(e_file),
        "--settings", str(settings_file),
    ])
    assert ret == 0

    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    cursor.execute("SELECT count(*) FROM articles")
    count_after = cursor.fetchone()[0]
    conn.close()

    assert count_before == count_after == 0



