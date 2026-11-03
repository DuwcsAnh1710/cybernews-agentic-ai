"""Unit and integration tests for CyberPulse AI Threat Intelligence REST API (Task T10)."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from unittest.mock import MagicMock
import xml.etree.ElementTree as ET

import feedparser
import pytest
from fastapi.testclient import TestClient

from cyberpulse.api.app import create_app
from cyberpulse.api.models import HealthResponse, ThreatRadarResponse
from cyberpulse.config import Settings, load_settings
from cyberpulse.runlock import RunLock


@pytest.fixture
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Fixture providing TestClient backed by existing repository outputs."""
    settings = load_settings("config/settings.toml")
    app = create_app("config/settings.toml")
    app.state.settings = settings
    # Set default test secret key in environment
    monkeypatch.setenv("API_SECRET_KEY", "test-secret-key-12345")
    return TestClient(app)


# ------------------------------------------------------------------------------
# 1. System Health & Docs Verification
# ------------------------------------------------------------------------------


def test_api_health_endpoint(api_client: TestClient) -> None:
    """GET /api/v1/health returns operational status, timestamp, and editions count."""
    resp = api_client.get("/api/v1/health")
    assert resp.status_code == 200

    data = resp.json()
    assert data["status"] == "ok"
    assert "timestamp" in data
    assert isinstance(data["editions_count"], int)
    assert data["editions_count"] >= 0
    assert data["service"] == "CyberPulse AI Threat Intelligence API"
    assert "version" in data


def test_api_swagger_and_openapi(api_client: TestClient) -> None:
    """GET /docs and /openapi.json serve OpenAPI specifications."""
    docs_resp = api_client.get("/docs")
    assert docs_resp.status_code == 200
    assert "swagger-ui" in docs_resp.text.lower() or "html" in docs_resp.headers.get("content-type", "")

    openapi_resp = api_client.get("/openapi.json")
    assert openapi_resp.status_code == 200
    spec = openapi_resp.json()
    assert spec["info"]["title"] == "CyberPulse AI Threat Intelligence API"
    assert "/api/v1/health" in spec["paths"]
    assert "/api/v1/editions" in spec["paths"]
    assert "/api/v1/threats/cves" in spec["paths"]
    assert "/api/v1/pipeline/trigger" in spec["paths"]


# ------------------------------------------------------------------------------
# 2. Editions & Latest Edition Endpoints
# ------------------------------------------------------------------------------


def test_api_editions_pagination(api_client: TestClient) -> None:
    """GET /api/v1/editions returns paginated list of editions ordered newest first."""
    resp = api_client.get("/api/v1/editions?limit=2&offset=0")
    assert resp.status_code == 200

    data = resp.json()
    assert "total" in data
    assert data["limit"] == 2
    assert data["offset"] == 0
    assert isinstance(data["items"], list)
    assert len(data["items"]) <= 2

    if data["items"]:
        item = data["items"][0]
        assert "run_id" in item
        assert "title" in item
        assert "published_at" in item
        assert "article_count" in item
        assert "has_podcast" in item
        assert "has_video" in item
        assert item["language"] == "vi"

    # Test offset pagination
    resp_offset = api_client.get("/api/v1/editions?limit=2&offset=2")
    assert resp_offset.status_code == 200
    data_offset = resp_offset.json()
    assert data_offset["offset"] == 2


def test_api_editions_latest_endpoint(api_client: TestClient) -> None:
    """GET /api/v1/editions/latest returns complete latest report package."""
    resp = api_client.get("/api/v1/editions/latest")
    assert resp.status_code == 200

    data = resp.json()
    assert "run_id" in data
    assert "title" in data
    assert "summary" in data
    assert "published_at" in data
    assert "media" in data
    assert "articles" in data
    assert len(data["articles"]) > 0

    first_story = data["articles"][0]
    assert "heading" in first_story
    assert "text" in first_story
    assert "cves" in first_story
    assert "threat_badge" in first_story
    assert "vendors" in first_story
    assert "sources" in first_story


def test_api_edition_by_id_found(api_client: TestClient) -> None:
    """GET /api/v1/editions/{run_id} returns 200 when edition exists."""
    list_resp = api_client.get("/api/v1/editions?limit=1")
    items = list_resp.json().get("items", [])
    if not items:
        pytest.skip("No editions found in test environment.")

    run_id = items[0]["run_id"]
    resp = api_client.get(f"/api/v1/editions/{run_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == run_id


def test_api_edition_by_id_not_found(api_client: TestClient) -> None:
    """GET /api/v1/editions/{run_id} returns 404 for nonexistent run ID."""
    resp = api_client.get("/api/v1/editions/nonexistent-run-id-99999")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


# ------------------------------------------------------------------------------
# 3. Media Assets Endpoint
# ------------------------------------------------------------------------------


def test_api_media_endpoint_invalid_type(api_client: TestClient) -> None:
    """GET /api/v1/editions/{run_id}/media/{media_type} returns 400 for unknown asset type."""
    resp = api_client.get("/api/v1/editions/any-run-id/media/unknown_format")
    assert resp.status_code == 400
    assert "invalid media_type" in resp.json()["detail"].lower()


def test_api_media_endpoint_nonexistent_asset(api_client: TestClient) -> None:
    """GET /api/v1/editions/{run_id}/media/audio returns 404 if file missing."""
    resp = api_client.get("/api/v1/editions/nonexistent-run-999/media/audio")
    assert resp.status_code == 404


def test_api_media_endpoint_existing_audio(api_client: TestClient) -> None:
    """GET /api/v1/editions/{run_id}/media/audio returns audio/mpeg if file present."""
    podcast_dir = Path("output/media/podcast")
    mp3_files = list(podcast_dir.glob("*.mp3")) if podcast_dir.exists() else []
    if not mp3_files:
        pytest.skip("No mp3 media available in test environment.")

    run_id = mp3_files[0].stem
    resp = api_client.get(f"/api/v1/editions/{run_id}/media/audio")
    assert resp.status_code == 200
    assert "audio/mpeg" in resp.headers.get("content-type", "")


# ------------------------------------------------------------------------------
# 4. Threat Radar CVE Aggregation
# ------------------------------------------------------------------------------


def test_api_threat_radar_cves(api_client: TestClient) -> None:
    """GET /api/v1/threats/cves returns aggregated CVE intelligence with pagination."""
    resp = api_client.get("/api/v1/threats/cves?limit=10&offset=0")
    assert resp.status_code == 200

    data = resp.json()
    assert "total" in data
    assert "items" in data
    assert isinstance(data["items"], list)

    if data["items"]:
        cve_item = data["items"][0]
        assert cve_item["cve_id"].startswith("CVE-")
        assert "risk_level" in cve_item
        assert "severity_badge" in cve_item
        assert "affected_vendors" in cve_item
        assert "editions" in cve_item
        assert "first_seen" in cve_item
        assert "last_seen" in cve_item
        assert cve_item["editions_count"] >= 1


def test_api_threat_radar_vendor_filter(api_client: TestClient) -> None:
    """GET /api/v1/threats/cves?vendor=Ivanti filters items by affected vendor."""
    resp = api_client.get("/api/v1/threats/cves?vendor=Ivanti")
    assert resp.status_code == 200
    data = resp.json()
    for item in data["items"]:
        assert any("ivanti" in v.lower() for v in item["affected_vendors"])


def test_api_threat_radar_severity_filter(api_client: TestClient) -> None:
    """GET /api/v1/threats/cves?severity=patch filters items by severity level."""
    resp = api_client.get("/api/v1/threats/cves?severity=patch")
    assert resp.status_code == 200
    data = resp.json()
    for item in data["items"]:
        sev = (item["risk_level"] + " " + item["severity_badge"]).lower()
        assert "vá" in sev or "patch" in sev


# ------------------------------------------------------------------------------
# 5. Syndication Feeds (RSS 2.0 & JSON Feed v1.1)
# ------------------------------------------------------------------------------


def test_api_syndication_rss_feed_xml(api_client: TestClient) -> None:
    """GET /api/v1/syndication/feed.xml exports valid RSS 2.0 XML."""
    resp = api_client.get("/api/v1/syndication/feed.xml")
    assert resp.status_code == 200
    assert "application/xml" in resp.headers.get("content-type", "")

    # Validate XML syntax with ElementTree
    root = ET.fromstring(resp.content)
    assert root.tag == "rss"
    assert root.attrib.get("version") == "2.0"

    channel = root.find("channel")
    assert channel is not None
    title = channel.find("title")
    assert title is not None
    assert "CyberPulse AI" in (title.text or "")

    # Validate with feedparser
    parsed = feedparser.parse(resp.content)
    assert parsed.bozo == 0 or not parsed.bozo_exception
    assert len(parsed.entries) >= 0


def test_api_syndication_json_feed(api_client: TestClient) -> None:
    """GET /api/v1/syndication/feed.json exports standard JSON Feed v1.1."""
    resp = api_client.get("/api/v1/syndication/feed.json")
    assert resp.status_code == 200
    assert "application/json" in resp.headers.get("content-type", "")

    data = resp.json()
    assert data["version"] == "https://jsonfeed.org/version/1.1"
    assert "title" in data
    assert "items" in data
    assert isinstance(data["items"], list)

    if data["items"]:
        entry = data["items"][0]
        assert "id" in entry
        assert "title" in entry
        assert "content_html" in entry
        assert "date_published" in entry


# ------------------------------------------------------------------------------
# 6. Webhook Authentication & Triggering
# ------------------------------------------------------------------------------


def test_api_pipeline_trigger_unauthorized_missing_key(api_client: TestClient) -> None:
    """POST /api/v1/pipeline/trigger returns 401 when no auth header is provided."""
    resp = api_client.post("/api/v1/pipeline/trigger", json={})
    assert resp.status_code == 401
    assert "unauthorized" in resp.json()["detail"].lower() or "api key" in resp.json()["detail"].lower()


def test_api_pipeline_trigger_unauthorized_invalid_key(api_client: TestClient) -> None:
    """POST /api/v1/pipeline/trigger returns 401 when invalid auth key is provided."""
    resp = api_client.post(
        "/api/v1/pipeline/trigger",
        headers={"X-API-Key": "wrong-secret-key-999"},
        json={},
    )
    assert resp.status_code == 401


def test_api_pipeline_trigger_authorized_x_api_key(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /api/v1/pipeline/trigger returns 200 when authenticated via X-API-Key."""
    # Register mock runner on app.state to avoid real subprocess execution in tests
    mock_runner = MagicMock()
    api_client.app.state.pipeline_runner = mock_runner

    resp = api_client.post(
        "/api/v1/pipeline/trigger",
        headers={"X-API-Key": "test-secret-key-12345"},
        json={"allow_insufficient": True, "max_rewrites": 1},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "triggered"
    assert "run_id" in data
    assert data["mode"] == "background"
    assert mock_runner.called


def test_api_pipeline_trigger_authorized_bearer_token(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /api/v1/pipeline/trigger returns 200 when authenticated via Bearer token."""
    mock_runner = MagicMock()
    api_client.app.state.pipeline_runner = mock_runner

    resp = api_client.post(
        "/api/v1/pipeline/trigger",
        headers={"Authorization": "Bearer test-secret-key-12345"},
        json={"no_llm_review": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "triggered"
    assert mock_runner.called


def test_api_pipeline_trigger_conflict_when_locked(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """POST /api/v1/pipeline/trigger returns 409 Conflict when pipeline lock is active."""
    # Mock active lock
    import cyberpulse.api.routes as routes

    monkeypatch.setattr(routes, "is_pid_alive", lambda pid: True)

    lock = RunLock(output_dir=api_client.app.state.settings.paths.output_dir, step="run")
    lock.acquire()

    try:
        resp = api_client.post(
            "/api/v1/pipeline/trigger",
            headers={"X-API-Key": "test-secret-key-12345"},
            json={},
        )
        assert resp.status_code == 409
        assert "busy" in resp.json()["detail"].lower() or "executing" in resp.json()["detail"].lower()
    finally:
        lock.release()


# ------------------------------------------------------------------------------
# 7. Scheduler Status & Webhook Triggering (Task T12)
# ------------------------------------------------------------------------------


def test_api_scheduler_status_default(api_client: TestClient) -> None:
    """GET /api/v1/scheduler/status returns default status when no status file exists."""
    from cyberpulse.scheduler import get_scheduler_status_path

    status_file = get_scheduler_status_path(api_client.app.state.settings.paths.output_dir)
    if status_file.exists():
        status_file.unlink()

    resp = api_client.get("/api/v1/scheduler/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("idle", "stopped")
    assert "interval_hours" in data
    assert "config" in data
    assert data["runs_completed"] >= 0


def test_api_scheduler_status_with_file(
    api_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /api/v1/scheduler/status returns serialized status from scheduler_status.json."""
    from cyberpulse.scheduler import get_scheduler_status_path, write_scheduler_status

    out_dir = api_client.app.state.settings.paths.output_dir
    test_data = {
        "status": "idle",
        "pid": 5555,
        "runs_completed": 42,
        "last_run_id": "run-test-12345",
        "last_run_time": "2026-11-03T18:00:00Z",
        "last_status": "success",
        "last_error": None,
        "next_run_time": "2026-11-04T06:00:00Z",
        "interval_hours": 12.0,
        "config": {"run_pipeline": True},
    }
    write_scheduler_status(out_dir, test_data)

    try:
        resp = api_client.get("/api/v1/scheduler/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["runs_completed"] == 42
        assert data["last_run_id"] == "run-test-12345"
        assert data["last_status"] == "success"
    finally:
        status_file = get_scheduler_status_path(out_dir)
        if status_file.exists():
            status_file.unlink()


def test_api_scheduler_status_detects_crashed_pid(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /api/v1/scheduler/status reports 'crashed' if PID died while status was 'running'."""
    from cyberpulse.scheduler import get_scheduler_status_path, write_scheduler_status
    import cyberpulse.api.service as service_mod

    # Force is_pid_alive to return False for dead PID
    monkeypatch.setattr(service_mod, "is_pid_alive", lambda pid: False)

    out_dir = api_client.app.state.settings.paths.output_dir
    write_scheduler_status(
        out_dir,
        {
            "status": "running",
            "pid": 999999,
            "runs_completed": 0,
            "interval_hours": 12.0,
        },
    )

    try:
        resp = api_client.get("/api/v1/scheduler/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "crashed"
    finally:
        status_file = get_scheduler_status_path(out_dir)
        if status_file.exists():
            status_file.unlink()


def test_api_scheduler_trigger_unauthorized(api_client: TestClient) -> None:
    """POST /api/v1/scheduler/trigger returns 401 when no auth header is provided."""
    resp = api_client.post("/api/v1/scheduler/trigger", json={})
    assert resp.status_code == 401


def test_api_scheduler_trigger_authorized(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /api/v1/scheduler/trigger returns 200 when authenticated via X-API-Key."""
    mock_runner = MagicMock()
    api_client.app.state.scheduler_runner = mock_runner

    resp = api_client.post(
        "/api/v1/scheduler/trigger",
        headers={"X-API-Key": "test-secret-key-12345"},
        json={"run_pipeline": True, "generate_video": False},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "triggered"
    assert "Autonomous scheduler cycle triggered" in data["message"]
    assert mock_runner.called


def test_api_scheduler_trigger_conflict_when_locked(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /api/v1/scheduler/trigger returns 409 Conflict when pipeline lock is active."""
    import cyberpulse.api.routes as routes

    monkeypatch.setattr(routes, "is_pid_alive", lambda pid: True)
    mock_runner = MagicMock()
    api_client.app.state.scheduler_runner = mock_runner

    lock = RunLock(output_dir=api_client.app.state.settings.paths.output_dir, step="run")
    lock.acquire()

    try:
        resp = api_client.post(
            "/api/v1/scheduler/trigger",
            headers={"X-API-Key": "test-secret-key-12345"},
            json={},
        )
        assert resp.status_code == 409
        assert "executing" in resp.json()["detail"].lower() or "busy" in resp.json()["detail"].lower()
        assert not mock_runner.called
    finally:
        lock.release()


def test_api_scheduler_trigger_conflict_when_scheduler_running(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /api/v1/scheduler/trigger returns 409 Conflict when scheduler status is already running."""
    import cyberpulse.api.routes as routes
    import cyberpulse.api.service as service_mod

    monkeypatch.setattr(routes, "is_pid_alive", lambda pid: True)
    monkeypatch.setattr(service_mod, "is_pid_alive", lambda pid: True)
    monkeypatch.setattr("cyberpulse.runlock.is_pid_alive", lambda pid: True)

    mock_runner = MagicMock()
    api_client.app.state.scheduler_runner = mock_runner

    from cyberpulse.scheduler import get_scheduler_status_path, write_scheduler_status

    out_dir = api_client.app.state.settings.paths.output_dir
    write_scheduler_status(
        out_dir,
        {
            "status": "running",
            "pid": 88888,
            "interval_hours": 12.0,
        },
    )

    try:
        resp = api_client.post(
            "/api/v1/scheduler/trigger",
            headers={"X-API-Key": "test-secret-key-12345"},
            json={},
        )
        assert resp.status_code == 409
        assert "currently running" in resp.json()["detail"].lower()
        assert not mock_runner.called
    finally:
        status_file = get_scheduler_status_path(out_dir)
        if status_file.exists():
            status_file.unlink()


