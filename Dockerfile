# CyberPulse AI - Production Containerization (Task T8.3)
# Multi-stage/Slim Python 3.12 runtime with non-root security and zero-emoji technical standards.

FROM python:3.12-slim-bookworm AS base

# System-level environment configurations
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/home/cyberpulse/.local/bin:${PATH}"

# Install minimal OS dependencies for health check and SSL certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Security: Create dedicated non-root application user
RUN groupadd -g 1000 cyberpulse && \
    useradd -u 1000 -g cyberpulse -m -s /bin/bash cyberpulse

WORKDIR /app

# Step 1: Copy metadata & install Python dependencies for optimal layer caching
COPY --chown=cyberpulse:cyberpulse pyproject.toml README.md ./
RUN pip install --no-cache-dir ".[ui,media]"

# Step 2: Copy application source code and configuration
COPY --chown=cyberpulse:cyberpulse src/ ./src/
COPY --chown=cyberpulse:cyberpulse config/ ./config/
COPY --chown=cyberpulse:cyberpulse scripts/ ./scripts/

# Step 3: Install the package itself
RUN pip install --no-cache-dir --no-deps -e .

# Step 4: Prepare persistent data directories with proper permissions
RUN mkdir -p /app/data /app/output /app/logs /app/dist && \
    chown -R cyberpulse:cyberpulse /app/data /app/output /app/logs /app/dist

# Switch to unprivileged user
USER cyberpulse

# Expose Streamlit Editorial Studio port
EXPOSE 8501

# Container healthcheck monitoring Streamlit core health endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

# Default execution: Launch Streamlit Editorial Studio
CMD ["cyberpulse", "ui", "--host", "0.0.0.0", "--port", "8501", "--settings", "config/settings.toml"]
