"""RESTful API routes for CyberPulse AI Threat Intelligence Service."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import secrets
from typing import Any
import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import FileResponse, JSONResponse

from cyberpulse.api.models import (
    EditionDetailResponse,
    EditionsListResponse,
    HealthResponse,
    PipelineTriggerRequest,
    PipelineTriggerResponse,
    ThreatRadarResponse,
)
from cyberpulse.api.service import ThreatIntelService
from cyberpulse.config import Settings, load_settings
from cyberpulse.runlock import get_lock_path, is_pid_alive, read_lock_info

logger = logging.getLogger("cyberpulse.api.routes")

router = APIRouter(prefix="/api/v1", tags=["Threat Intelligence"])


def get_settings(request: Request) -> Settings:
    """Dependency retrieving loaded application settings."""
    if hasattr(request.app.state, "settings") and request.app.state.settings is not None:
        return request.app.state.settings
    settings_path = os.environ.get("CYBERPULSE_SETTINGS_PATH", "config/settings.toml")
    return load_settings(settings_path)


def get_service(settings: Settings = Depends(get_settings)) -> ThreatIntelService:
    """Dependency providing ThreatIntelService instance."""
    return ThreatIntelService(settings)


def verify_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    settings: Settings = Depends(get_settings),
) -> str:
    """Validate webhook authentication via X-API-Key or Bearer token."""
    expected_key = os.environ.get("API_SECRET_KEY")
    if not expected_key and hasattr(settings, "api") and settings.api.secret_key:
        expected_key = settings.api.secret_key

    if not expected_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Server API secret key is not configured",
        )

    provided_key: str | None = None
    if x_api_key:
        provided_key = x_api_key.strip()
    elif authorization:
        parts = authorization.strip().split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            provided_key = parts[1]
        elif len(parts) == 1:
            provided_key = parts[0]

    if not provided_key or not secrets.compare_digest(provided_key, expected_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )

    return provided_key


@router.get("/health", response_model=HealthResponse)
def get_health(service: ThreatIntelService = Depends(get_service)) -> HealthResponse:
    """System health check returning operational status, timestamp, and edition metrics."""
    data = service.get_health()
    return HealthResponse(**data)


@router.get("/editions", response_model=EditionsListResponse)
def list_editions(
    limit: int = Query(default=10, ge=1, le=100, description="Items per page"),
    offset: int = Query(default=0, ge=0, description="Offset index"),
    service: ThreatIntelService = Depends(get_service),
) -> EditionsListResponse:
    """List intelligence editions with pagination, sorted newest first."""
    total, items = service.get_editions(limit=limit, offset=offset)
    return EditionsListResponse(total=total, limit=limit, offset=offset, items=items)


@router.get("/editions/latest", response_model=EditionDetailResponse)
def get_latest_edition(
    service: ThreatIntelService = Depends(get_service),
) -> EditionDetailResponse:
    """Fetch complete report data for the most recently published intelligence edition."""
    edition = service.get_latest_edition()
    if edition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No intelligence editions available",
        )
    return edition


@router.get("/editions/{run_id}", response_model=EditionDetailResponse)
def get_edition_by_id(
    run_id: str,
    service: ThreatIntelService = Depends(get_service),
) -> EditionDetailResponse:
    """Fetch complete edition details by run identifier."""
    edition = service.get_edition_detail(run_id)
    if edition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Edition '{run_id}' not found",
        )
    return edition


@router.get("/editions/{run_id}/media/{media_type}")
def get_edition_media(
    run_id: str,
    media_type: str,
    service: ThreatIntelService = Depends(get_service),
):
    """Download audio podcast, subtitle, or video assets associated with an edition."""
    valid_types = {
        "audio": "audio/mpeg",
        "audio_srt": "text/plain",
        "video": "video/mp4",
        "video_srt": "text/plain",
    }
    if media_type not in valid_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid media_type '{media_type}'. Allowed: {sorted(valid_types.keys())}",
        )

    file_path = service.get_media_file(run_id, media_type)
    if file_path is None or not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Media asset '{media_type}' for edition '{run_id}' not found",
        )

    return FileResponse(
        path=str(file_path),
        media_type=valid_types[media_type],
        filename=file_path.name,
    )


@router.get("/threats/cves", response_model=ThreatRadarResponse)
def get_threat_radar(
    vendor: str | None = Query(default=None, description="Filter by affected vendor name"),
    severity: str | None = Query(default=None, description="Filter by threat severity badge"),
    limit: int = Query(default=50, ge=1, le=200, description="Items per page"),
    offset: int = Query(default=0, ge=0, description="Offset index"),
    service: ThreatIntelService = Depends(get_service),
) -> ThreatRadarResponse:
    """Aggregate Threat Radar CVE intelligence across published editions."""
    total, items = service.get_threat_radar(
        vendor=vendor,
        severity=severity,
        limit=limit,
        offset=offset,
    )
    return ThreatRadarResponse(total=total, limit=limit, offset=offset, items=items)


@router.get("/syndication/feed.xml")
def get_rss_feed(
    request: Request,
    service: ThreatIntelService = Depends(get_service),
) -> Response:
    """Syndicate published editions via standard RSS 2.0 XML feed."""
    base_url = str(request.base_url).rstrip("/")
    rss_xml = service.generate_rss_feed(base_url=base_url)
    return Response(content=rss_xml, media_type="application/xml")


@router.get("/syndication/feed.json")
def get_json_feed(
    request: Request,
    service: ThreatIntelService = Depends(get_service),
) -> JSONResponse:
    """Syndicate published editions via JSON Feed v1.1 format."""
    base_url = str(request.base_url).rstrip("/")
    json_data = service.generate_json_feed(base_url=base_url)
    return JSONResponse(content=json_data, media_type="application/json")


@router.post(
    "/pipeline/trigger",
    response_model=PipelineTriggerResponse,
    status_code=status.HTTP_200_OK,
)
def trigger_pipeline(
    request: Request,
    payload: PipelineTriggerRequest | None = None,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    _auth: str = Depends(verify_api_key),
    settings: Settings = Depends(get_settings),
) -> PipelineTriggerResponse:
    """Enterprise webhook triggering intelligence collection and editorial synthesis."""
    req = payload or PipelineTriggerRequest()
    run_id = req.run_id or f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"

    # Check cross-process run lock
    lock_path = get_lock_path(settings.paths.output_dir)
    if lock_path.exists():
        info = read_lock_info(settings.paths.output_dir)
        if info and is_pid_alive(info.get("pid", 0)):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Pipeline is currently executing (PID: {info.get('pid')})",
            )

    # Allow custom/mock runner function if registered on app state
    custom_runner = getattr(request.app.state, "pipeline_runner", None)
    if custom_runner is not None:
        background_tasks.add_task(custom_runner, req, run_id, settings)
    else:
        # Default headless background job launcher
        from cyberpulse.ui.runner import start_job

        def _bg_run() -> None:
            try:
                start_job(
                    step="run",
                    output_dir=settings.paths.output_dir,
                    settings_path=Path(os.environ.get("CYBERPULSE_SETTINGS_PATH", "config/settings.toml")),
                    params={
                        "allow_insufficient": req.allow_insufficient,
                        "max_rewrites": req.max_rewrites,
                        "no_llm_review": req.no_llm_review,
                        "run_id": run_id,
                    },
                )
            except Exception as err:
                logger.error("Failed executing background pipeline run %s: %s", run_id, err)

        background_tasks.add_task(_bg_run)

    return PipelineTriggerResponse(
        status="triggered",
        run_id=run_id,
        message="Intelligence pipeline collection triggered successfully",
        mode="background",
    )
