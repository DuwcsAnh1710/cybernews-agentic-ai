"""FastAPI Application factory for CyberPulse AI Threat Intelligence API."""

from __future__ import annotations

from contextlib import asynccontextmanager
import os
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cyberpulse import __version__
from cyberpulse.api.routes import router
from cyberpulse.config import Settings, load_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Lifespan context manager initializing application settings."""
    settings_path_str = os.environ.get("CYBERPULSE_SETTINGS_PATH", "config/settings.toml")
    settings_path = Path(settings_path_str)
    if settings_path.exists():
        try:
            app.state.settings = load_settings(settings_path)
        except Exception:
            app.state.settings = None
    yield


def create_app(settings_path: Path | str | None = None) -> FastAPI:
    """Create and configure FastAPI application instance."""
    if settings_path is not None:
        os.environ["CYBERPULSE_SETTINGS_PATH"] = str(Path(settings_path).resolve())

    app = FastAPI(
        title="CyberPulse AI Threat Intelligence API",
        description=(
            "Enterprise RESTful Threat Intelligence and Webhook Service for SOC / SIEM "
            "integration, security feeds, and automated news pipeline control."
        ),
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # Cross-Origin Resource Sharing (CORS) for SOC dashboards & SIEM portals
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Load initial settings onto app state if available
    current_settings_path = os.environ.get("CYBERPULSE_SETTINGS_PATH", "config/settings.toml")
    p = Path(current_settings_path)
    if p.exists():
        try:
            app.state.settings = load_settings(p)
        except Exception:
            app.state.settings = None
    else:
        app.state.settings = None

    # Register API endpoints
    app.include_router(router)

    return app


# Module-level default application instance
app = create_app()
