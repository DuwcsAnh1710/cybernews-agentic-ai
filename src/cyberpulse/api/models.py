"""Pydantic v2 schemas and data contracts for CyberPulse AI API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Health status and telemetry response model."""

    status: str = Field(default="ok", description="Operational health status")
    timestamp: datetime = Field(description="Current server UTC timestamp")
    editions_count: int = Field(description="Total number of stored editions")
    version: str = Field(description="CyberPulse AI version string")
    service: str = Field(
        default="CyberPulse AI Threat Intelligence API",
        description="Service identifier",
    )


class EditionSummary(BaseModel):
    """Summarized metadata for a single news intelligence edition."""

    run_id: str
    title: str
    published_at: datetime
    summary: str
    language: str = "vi"
    article_count: int
    has_podcast: bool
    has_video: bool
    detail_url: str


class EditionsListResponse(BaseModel):
    """Paginated collection of published intelligence editions."""

    total: int
    limit: int
    offset: int
    items: list[EditionSummary]


class MediaLinks(BaseModel):
    """Direct downloadable media asset references."""

    audio_mp3_url: str | None = None
    audio_srt_url: str | None = None
    video_mp4_url: str | None = None
    video_srt_url: str | None = None
    has_audio: bool = False
    has_video: bool = False


class ArticleSourceInfo(BaseModel):
    """Original primary source article citation details."""

    id: str
    source_name: str
    title: str
    url: str
    published_at: datetime | None = None
    summary: str = ""
    topics_matched: list[str] = Field(default_factory=list)


class ArticleThreatBadge(BaseModel):
    """Severity classification badge metadata."""

    label: str
    css_class: str
    text: str


class AnalyzedArticle(BaseModel):
    """Deeply analyzed threat intelligence story item."""

    heading: str
    text: str
    cves: list[str] = Field(default_factory=list)
    threat_badge: ArticleThreatBadge
    vendors: list[str] = Field(default_factory=list)
    cited_article_ids: list[str] = Field(default_factory=list)
    sources: list[ArticleSourceInfo] = Field(default_factory=list)


class EditionDetailResponse(BaseModel):
    """Full threat intelligence edition report package."""

    run_id: str
    title: str
    summary: str
    published_at: datetime
    language: str = "vi"
    media: MediaLinks
    articles: list[AnalyzedArticle]
    podcast_script: Any | None = None
    video_script: Any | None = None
    captions: list[Any] = Field(default_factory=list)


class CveThreatItem(BaseModel):
    """Aggregated Threat Radar CVE entity intelligence."""

    cve_id: str
    risk_level: str
    severity_badge: str
    affected_vendors: list[str] = Field(default_factory=list)
    first_seen: datetime
    last_seen: datetime
    editions_count: int
    editions: list[str] = Field(default_factory=list)
    headlines: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)


class ThreatRadarResponse(BaseModel):
    """Aggregated CVE radar response with pagination."""

    total: int
    limit: int
    offset: int
    items: list[CveThreatItem]


class PipelineTriggerRequest(BaseModel):
    """Parameters for on-demand intelligence collection trigger."""

    allow_insufficient: bool = False
    max_rewrites: int = Field(default=2, ge=0, le=3)
    no_llm_review: bool = False
    run_id: str | None = None


class PipelineTriggerResponse(BaseModel):
    """Webhook response acknowledging pipeline trigger."""

    status: str = "triggered"
    run_id: str
    message: str
    mode: str = "background"
