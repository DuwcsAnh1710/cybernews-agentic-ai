"""Unit tests for Docker containerization and single-command deployment (Task T8.3)."""

from __future__ import annotations

from pathlib import Path
import re


def test_dockerfile_specifications() -> None:
    dockerfile_path = Path("Dockerfile")
    assert dockerfile_path.exists(), "Dockerfile must exist at repository root"

    content = dockerfile_path.read_text(encoding="utf-8")

    # Base image check
    assert "FROM python:3.12-slim-bookworm" in content, "Must use python:3.12-slim-bookworm base image"

    # Security: Non-root user
    assert "useradd -u 1000 -g cyberpulse" in content, "Must create dedicated non-root user cyberpulse (UID 1000)"
    assert "USER cyberpulse" in content, "Must switch to non-root user before running"

    # Environment
    assert "PYTHONUNBUFFERED=1" in content
    assert "PYTHONDONTWRITEBYTECODE=1" in content

    # Directory preparation
    assert "/app/data" in content
    assert "/app/output" in content
    assert "/app/logs" in content
    assert "/app/dist" in content

    # Ports & Healthcheck
    assert "EXPOSE 8501" in content
    assert "HEALTHCHECK" in content
    assert "/_stcore/health" in content

    # Default CMD
    assert 'CMD ["cyberpulse", "ui", "--host", "0.0.0.0", "--port", "8501", "--settings", "config/settings.toml"]' in content

    # Zero-emoji policy
    for forbidden in ["🎙️", "🎧", "🟢", "🟡", "🔴", "⚠️", "✅", "📅", "🎉", "🚀", "🔥"]:
        assert forbidden not in content, f"Forbidden emoji '{forbidden}' found in Dockerfile"


def test_docker_compose_specifications() -> None:
    compose_path = Path("docker-compose.yml")
    assert compose_path.exists(), "docker-compose.yml must exist at repository root"

    content = compose_path.read_text(encoding="utf-8")

    # Required services
    assert "cyberpulse-ui:" in content, "cyberpulse-ui service must be defined"
    assert "cyberpulse-runner:" in content, "cyberpulse-runner service must be defined"
    assert "cyberpulse-portal:" in content, "cyberpulse-portal service must be defined"
    assert "cyberpulse-scheduler:" in content, "cyberpulse-scheduler service must be defined"
    assert "scheduler" in content and "--daemon" in content, "Must configure scheduler in daemon mode"

    # Port mappings
    assert '"8501:8501"' in content, "Must expose Streamlit UI on port 8501"
    assert '"8080:80"' in content, "Must expose Reader Portal on port 8080"

    # Volume mounts for persistence
    assert "./data:/app/data" in content, "data directory must be volume-mounted"
    assert "./output:/app/output" in content, "output directory must be volume-mounted"
    assert "./logs:/app/logs" in content, "logs directory must be volume-mounted"
    assert "./dist:/app/dist" in content, "dist directory must be volume-mounted"

    # Host binding in UI service
    assert "--host" in content and "0.0.0.0" in content

    # Environment pass-through
    assert "GEMINI_API_KEY=${GEMINI_API_KEY:-}" in content
    assert "TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:-}" in content
    assert "TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID:-}" in content

    # Zero-emoji policy
    for forbidden in ["🎙️", "🎧", "🟢", "🟡", "🔴", "⚠️", "✅", "📅", "🎉", "🚀", "🔥"]:
        assert forbidden not in content, f"Forbidden emoji '{forbidden}' found in docker-compose.yml"


def test_dockerignore_specifications() -> None:
    ignore_path = Path(".dockerignore")
    assert ignore_path.exists(), ".dockerignore must exist at repository root"

    content = ignore_path.read_text(encoding="utf-8")
    ignored_patterns = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]

    # Critical security exclusions
    assert ".git" in ignored_patterns
    assert ".env" in ignored_patterns
    assert ".venv/" in ignored_patterns or ".venv" in ignored_patterns
    assert "__pycache__/" in ignored_patterns or "__pycache__" in ignored_patterns
    assert "tests/" in ignored_patterns or "tests" in ignored_patterns


def test_docker_control_script_specifications() -> None:
    script_path = Path("scripts/docker_control.ps1")
    assert script_path.exists(), "scripts/docker_control.ps1 must exist"

    content = script_path.read_text(encoding="utf-8")
    assert "docker compose up -d" in content
    assert "docker compose down" in content
    assert "docker compose run --rm cyberpulse-runner" in content
    assert "[CYBERPULSE INTEL]" in content
