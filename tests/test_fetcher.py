"""Offline unit tests for RSS fetcher and CLI fetch command."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from cyberpulse.cli import main
from cyberpulse.config import ConfigError, FetchConfig, Settings, Source, load_settings, load_sources
from cyberpulse.fetcher import FetchReport, RawItem, SourceResult, fetch_all, fetch_source

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "rss"


def _read_fixture(filename: str) -> bytes:
    """Read binary content of a fixture file."""
    return (FIXTURES_DIR / filename).read_bytes()


def test_valid_rss() -> None:
    """1. valid_rss -> 3 items, published_at aware UTC."""
    content = _read_fixture("valid_rss.xml")
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=content))
    client = httpx.Client(transport=transport)
    source = Source(name="Valid RSS", url="https://example.com/sample/feed")
    cfg = FetchConfig()

    items, result = fetch_source(client, source, cfg)

    assert result.ok is True
    assert result.item_count == 3
    assert result.error is None
    assert result.http_status == 200
    assert len(items) == 3

    for item in items:
        assert item.source_name == "Valid RSS"
        assert item.title is not None and item.title.startswith("SAMPLE —")
        assert item.link is not None and item.link.startswith("https://example.com/sample/")
        assert item.published_at is not None
        assert item.published_at.tzinfo is not None
        assert item.published_at.utcoffset() == timezone.utc.utcoffset(None)
        assert item.fetched_at.tzinfo is not None


def test_valid_atom() -> None:
    """2. valid_atom -> 2 items, dates from updated."""
    content = _read_fixture("valid_atom.xml")
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=content))
    client = httpx.Client(transport=transport)
    source = Source(name="Valid Atom", url="https://example.com/sample/atom-feed")
    cfg = FetchConfig()

    items, result = fetch_source(client, source, cfg)

    assert result.ok is True
    assert result.item_count == 2
    assert len(items) == 2

    # Check updated dates parsed correctly to aware UTC
    assert items[0].published_at == datetime(2026, 10, 28, 14, 20, 0, tzinfo=timezone.utc)
    assert items[1].published_at == datetime(2026, 10, 29, 9, 15, 0, tzinfo=timezone.utc)
    assert items[0].title == "SAMPLE — Threat Actor Exploiting Cloud Misconfigurations"
    assert items[1].title == "SAMPLE — Supply Chain Advisory for Open Source Packages"


def test_missing_fields() -> None:
    """3. missing_fields -> 3 items with correct None fields, none invented or dropped."""
    content = _read_fixture("missing_fields.xml")
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=content))
    client = httpx.Client(transport=transport)
    source = Source(name="Missing Fields", url="https://example.com/sample/incomplete")
    cfg = FetchConfig()

    items, result = fetch_source(client, source, cfg)

    assert result.ok is True
    assert result.item_count == 3
    assert len(items) == 3

    # Item 1: Missing link
    assert items[0].title == "SAMPLE — Item Without Link"
    assert items[0].link is None
    assert items[0].published_at is not None

    # Item 2: Missing pubDate
    assert items[1].title == "SAMPLE — Item Without Date"
    assert items[1].link == "https://example.com/sample/item-no-date"
    assert items[1].published_at is None
    assert items[1].published_raw is None

    # Item 3: Missing title
    assert items[2].title is None
    assert items[2].link == "https://example.com/sample/item-no-title"
    assert items[2].published_at is not None


def test_empty_feed() -> None:
    """4. empty_feed -> ok=True, item_count=0."""
    content = _read_fixture("empty_feed.xml")
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=content))
    client = httpx.Client(transport=transport)
    source = Source(name="Empty Feed", url="https://example.com/sample/empty")
    cfg = FetchConfig()

    items, result = fetch_source(client, source, cfg)

    assert result.ok is True
    assert result.item_count == 0
    assert result.error is None
    assert len(items) == 0


def test_not_a_feed_html() -> None:
    """5. not_a_feed.html -> ok=False, error starts with 'invalid feed'."""
    content = _read_fixture("not_a_feed.html")
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=content))
    client = httpx.Client(transport=transport)
    source = Source(name="Not A Feed", url="https://example.com/sample/page")
    cfg = FetchConfig()

    items, result = fetch_source(client, source, cfg)

    assert result.ok is False
    assert result.item_count == 0
    assert result.error is not None
    assert result.error.startswith("invalid feed")
    assert len(items) == 0


def test_retries_on_500() -> None:
    """6. HTTP 500 twice then 200 with retries=1 -> ok=False after 2 calls; with retries=2 -> ok=True after 3 calls."""
    source = Source(name="Retry Source", url="https://example.com/retry")
    content = _read_fixture("valid_rss.xml")

    # Case A: retries=1 -> fails after 2 calls
    calls_a = 0

    def handler_a(req: httpx.Request) -> httpx.Response:
        nonlocal calls_a
        calls_a += 1
        if calls_a <= 2:
            return httpx.Response(500, text="Internal Server Error")
        return httpx.Response(200, content=content)

    client_a = httpx.Client(transport=httpx.MockTransport(handler_a))
    cfg_a = FetchConfig(retries=1)
    items_a, result_a = fetch_source(client_a, source, cfg_a)

    assert result_a.ok is False
    assert calls_a == 2
    assert result_a.http_status == 500
    assert result_a.error == "HTTP 500"

    # Case B: retries=2 -> succeeds on 3rd call
    calls_b = 0

    def handler_b(req: httpx.Request) -> httpx.Response:
        nonlocal calls_b
        calls_b += 1
        if calls_b <= 2:
            return httpx.Response(500, text="Internal Server Error")
        return httpx.Response(200, content=content)

    client_b = httpx.Client(transport=httpx.MockTransport(handler_b))
    cfg_b = FetchConfig(retries=2)
    items_b, result_b = fetch_source(client_b, source, cfg_b)

    assert result_b.ok is True
    assert calls_b == 3
    assert result_b.http_status == 200
    assert len(items_b) == 3


def test_no_retry_on_404() -> None:
    """7. HTTP 404 -> ok=False, exactly 1 call."""
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404, text="Not Found")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = Source(name="404 Source", url="https://example.com/not-found")
    cfg = FetchConfig(retries=3)

    items, result = fetch_source(client, source, cfg)

    assert result.ok is False
    assert calls == 1
    assert result.http_status == 404
    assert result.error == "HTTP 404"
    assert len(items) == 0


def test_timeout() -> None:
    """8. Timeout (httpx.ReadTimeout raised by transport) -> ok=False, error mentions timeout."""
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("Socket read timed out")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = Source(name="Timeout Source", url="https://example.com/timeout")
    cfg = FetchConfig(retries=1)

    items, result = fetch_source(client, source, cfg)

    assert result.ok is False
    assert calls == 2  # 1 initial + 1 retry
    assert result.error is not None
    assert "timeout" in result.error.lower()
    assert len(items) == 0


def test_oversized_response() -> None:
    """9. Body larger than max_bytes -> ok=False, 'response too large'."""
    calls = 0
    large_content = b"A" * 20_000

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=large_content)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = Source(name="Large Source", url="https://example.com/large")
    cfg = FetchConfig(max_bytes=10_000, retries=2)

    items, result = fetch_source(client, source, cfg)

    assert result.ok is False
    assert calls == 1  # Oversized response must not be retried
    assert result.error == "response too large"
    assert len(items) == 0


def test_fetch_all_partial_failure() -> None:
    """10. fetch_all with 3 sources (ok, 500, html) -> 1 ok, 2 failed, items only from ok, all_failed False."""
    rss_content = _read_fixture("valid_rss.xml")
    html_content = _read_fixture("not_a_feed.html")

    def handler(req: httpx.Request) -> httpx.Response:
        url_str = str(req.url)
        if "source-ok" in url_str:
            return httpx.Response(200, content=rss_content)
        elif "source-500" in url_str:
            return httpx.Response(500, text="Internal Server Error")
        elif "source-html" in url_str:
            return httpx.Response(200, content=html_content)
        return httpx.Response(404, text="Not Found")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sources = [
        Source(name="Source OK", url="https://example.com/source-ok"),
        Source(name="Source 500", url="https://example.com/source-500"),
        Source(name="Source HTML", url="https://example.com/source-html"),
    ]
    cfg = FetchConfig(retries=0)

    report = fetch_all(sources, cfg, client=client)

    assert len(report.results) == 3
    assert len(report.ok_sources) == 1
    assert len(report.failed_sources) == 2
    assert report.all_failed is False
    assert len(report.items) == 3
    assert all(it.source_name == "Source OK" for it in report.items)


def test_fetch_all_all_failed() -> None:
    """11. All sources failing -> all_failed True."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Server Down")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sources = [
        Source(name="Source 1", url="https://example.com/source-1"),
        Source(name="Source 2", url="https://example.com/source-2"),
    ]
    cfg = FetchConfig(retries=0)

    report = fetch_all(sources, cfg, client=client)

    assert len(report.results) == 2
    assert len(report.ok_sources) == 0
    assert len(report.failed_sources) == 2
    assert report.all_failed is True
    assert len(report.items) == 0


def test_disabled_sources_skipped() -> None:
    """12. Disabled sources are skipped."""
    rss_content = _read_fixture("valid_rss.xml")
    called_urls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        called_urls.append(str(req.url))
        return httpx.Response(200, content=rss_content)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sources = [
        Source(name="Enabled Source", url="https://example.com/enabled", enabled=True),
        Source(name="Disabled Source", url="https://example.com/disabled", enabled=False),
    ]
    cfg = FetchConfig()

    report = fetch_all(sources, cfg, client=client)

    assert len(report.results) == 1
    assert report.results[0].source_name == "Enabled Source"
    assert "https://example.com/disabled" not in called_urls


def test_fetch_config_validation(tmp_path: Path) -> None:
    """13. Invalid [fetch] values (timeout_sec=0, retries=5) -> ConfigError."""
    base_toml = """
    project_name = "CyberPulse"
    topics = ["security"]
    """

    # Test invalid timeout_sec=0
    cfg_file = tmp_path / "invalid_timeout.toml"
    cfg_file.write_text(base_toml + "\n[fetch]\ntimeout_sec = 0\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc_info:
        load_settings(cfg_file)
    assert "timeout_sec" in str(exc_info.value)

    # Test invalid retries=5
    cfg_file = tmp_path / "invalid_retries.toml"
    cfg_file.write_text(base_toml + "\n[fetch]\nretries = 5\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc_info:
        load_settings(cfg_file)
    assert "retries" in str(exc_info.value)

    # Test invalid max_bytes=500
    cfg_file = tmp_path / "invalid_bytes.toml"
    cfg_file.write_text(base_toml + "\n[fetch]\nmax_bytes = 500\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc_info:
        load_settings(cfg_file)
    assert "max_bytes" in str(exc_info.value)


def test_cli_fetch_all_failed_exit_code_2(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """14. CLI fetch with an injected/mocked failure path for all sources -> exit code 2."""
    def mock_fetch_all(sources: list[Source], cfg: FetchConfig, client: httpx.Client | None = None) -> FetchReport:
        return FetchReport(
            items=[],
            results=[
                SourceResult(
                    source_name="Mocked Source 1",
                    url="https://example.com/1",
                    ok=False,
                    item_count=0,
                    error="HTTP 500",
                    http_status=500,
                    elapsed_ms=45,
                )
            ],
        )

    monkeypatch.setattr("cyberpulse.cli.fetch_all", mock_fetch_all)

    exit_code = main(["fetch"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "[FAIL] Mocked Source 1: HTTP 500" in captured.out
    assert "Total raw items: 0" in captured.out


def test_cli_fetch_success(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI fetch with successful mocked fetch -> exit code 0 and limit output."""
    mock_now = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)
    mock_items = [
        RawItem(
            source_name="Mocked Source",
            title="SAMPLE — Security Advisory",
            link="https://example.com/sample/adv",
            published_at=mock_now,
            published_raw="Thu, 29 Oct 2026 12:00:00 GMT",
            summary_raw="Summary text",
            fetched_at=mock_now,
        )
    ]
    mock_results = [
        SourceResult(
            source_name="Mocked Source",
            url="https://example.com/mock",
            ok=True,
            item_count=1,
            error=None,
            http_status=200,
            elapsed_ms=120,
        )
    ]

    def mock_fetch_all(sources: list[Source], cfg: FetchConfig, client: httpx.Client | None = None) -> FetchReport:
        return FetchReport(items=mock_items, results=mock_results)

    monkeypatch.setattr("cyberpulse.cli.fetch_all", mock_fetch_all)

    exit_code = main(["fetch", "--limit", "2"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "[OK] Mocked Source: 1 items (120 ms)" in captured.out
    assert "SAMPLE — Security Advisory" in captured.out
    assert "https://example.com/sample/adv" in captured.out
    assert "Total raw items: 1" in captured.out


def test_cli_fetch_config_error(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI fetch with invalid settings path -> exit code 1."""
    exit_code = main(["fetch", "--settings", "nonexistent_settings.toml"])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "ConfigError" in captured.err


def test_fetch_source_propagates_non_httpx_exception() -> None:
    """15. fetch_source with a transport that raises ValueError -> the ValueError propagates."""
    def handler(req: httpx.Request) -> httpx.Response:
        raise ValueError("Programming error in transport")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = Source(name="Error Source", url="https://example.com/sample/error")
    cfg = FetchConfig()

    with pytest.raises(ValueError, match="Programming error in transport"):
        fetch_source(client, source, cfg)


def test_user_agent_header_sent() -> None:
    """16. The User-Agent sent on each request equals cfg.user_agent."""
    content = _read_fixture("valid_rss.xml")
    captured_headers: list[dict[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured_headers.append(dict(req.headers))
        return httpx.Response(200, content=content)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = Source(name="UA Test Source", url="https://example.com/ua-test")
    cfg = FetchConfig()

    items, result = fetch_source(client, source, cfg)

    assert result.ok is True
    assert len(captured_headers) == 1
    assert "user-agent" in captured_headers[0]
    assert captured_headers[0]["user-agent"] == cfg.user_agent


def test_403_not_retried_other_sources_unaffected() -> None:
    """17. A 403 source is NOT retried (exactly 1 request) and does not affect other sources."""
    rss_content = _read_fixture("valid_rss.xml")
    call_counts: dict[str, int] = {"forbidden": 0, "healthy": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        url_str = str(req.url)
        if "forbidden" in url_str:
            call_counts["forbidden"] += 1
            return httpx.Response(403, text="Forbidden")
        call_counts["healthy"] += 1
        return httpx.Response(200, content=rss_content)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sources = [
        Source(name="Forbidden Source", url="https://example.com/forbidden"),
        Source(name="Healthy Source", url="https://example.com/healthy"),
    ]
    cfg = FetchConfig(retries=2)

    report = fetch_all(sources, cfg, client=client)

    # 403 source: exactly 1 request (no retry), failed
    assert call_counts["forbidden"] == 1
    forbidden_result = next(r for r in report.results if r.source_name == "Forbidden Source")
    assert forbidden_result.ok is False
    assert forbidden_result.http_status == 403
    assert forbidden_result.error == "HTTP 403"

    # Healthy source: unaffected, succeeded
    assert call_counts["healthy"] == 1
    healthy_result = next(r for r in report.results if r.source_name == "Healthy Source")
    assert healthy_result.ok is True
    assert healthy_result.item_count == 3
    assert len(report.items) == 3
    assert all(it.source_name == "Healthy Source" for it in report.items)


def test_sources_toml_loads_without_error() -> None:
    """18. config/sources.toml loads through the existing loader; at least 5 enabled; no BleepingComputer."""
    sources = load_sources("config/sources.toml")

    enabled = [s for s in sources if s.enabled]
    assert len(enabled) >= 5

    names = {s.name for s in sources}
    assert "BleepingComputer" not in names


