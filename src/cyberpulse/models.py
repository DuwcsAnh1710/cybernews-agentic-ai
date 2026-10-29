"""Shared data contracts and Pydantic v2 models for CyberPulse AI."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import hashlib
import re
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    Field,
    field_validator,
    model_validator,
)


class RunStatus(str, Enum):
    """Pipeline execution status."""

    pending = "pending"
    researching = "researching"
    editing = "editing"
    validating = "validating"
    rendering = "rendering"
    awaiting_review = "awaiting_review"
    approved = "approved"
    exported = "exported"
    published = "published"
    partial = "partial"
    failed = "failed"


def make_article_id(canonical_url: str) -> str:
    """Return the first 16 lowercase hex characters of SHA-256 of the URL."""
    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:16]


class Article(BaseModel):
    """Article metadata and content representation."""

    id: str
    source_name: str
    title: str
    url: str
    published_at: AwareDatetime
    summary: str = ""
    fetched_at: AwareDatetime
    topics_matched: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{16}", v):
            raise ValueError(f"id must be exactly 16 lowercase hex characters, got '{v}'")
        return v

    @field_validator("source_name")
    @classmethod
    def validate_source_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("source_name cannot be empty")
        return v.strip()

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("title cannot be empty")
        return v.strip()

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("url must start with http:// or https://")
        return v.strip()

    @field_validator("published_at", "fetched_at")
    @classmethod
    def normalize_utc(cls, v: datetime) -> datetime:
        return v.astimezone(timezone.utc)


class ResearchItem(BaseModel):
    """Individual article ranked and evaluated for inclusion."""

    rank: int = Field(ge=1)
    article: Article
    relevance_reason: str

    @field_validator("relevance_reason")
    @classmethod
    def validate_relevance_reason(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("relevance_reason must be a non-empty string")
        return v.strip()


class ResearchResult(BaseModel):
    """Outcome of research phase containing selected articles."""

    run_id: str
    status: Literal["ok", "insufficient"]
    requested_count: int = Field(ge=1, le=10)
    items: list[ResearchItem]
    notes: str = ""

    @model_validator(mode="after")
    def validate_research_result(self) -> ResearchResult:
        ranks = [item.rank for item in self.items]
        expected_ranks = list(range(1, len(self.items) + 1))
        if sorted(ranks) != expected_ranks:
            raise ValueError(
                f"ranks must be exactly 1..{len(self.items)} with no duplicates, got {ranks}"
            )
        article_ids = [item.article.id for item in self.items]
        if len(article_ids) != len(set(article_ids)):
            raise ValueError("article ids must be unique")
        if self.status == "ok" and len(self.items) != self.requested_count:
            raise ValueError(
                f"status 'ok' requires len(items) == requested_count ({self.requested_count}), got {len(self.items)}"
            )
        if self.status == "insufficient" and len(self.items) >= self.requested_count:
            raise ValueError(
                f"status 'insufficient' requires len(items) < requested_count ({self.requested_count}), got {len(self.items)}"
            )
        return self


class CitedText(BaseModel):
    """Text block citing one or more article IDs."""

    text: str
    cited_article_ids: list[str] = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def validate_text(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("text must be a non-empty string")
        return v.strip()

    @field_validator("cited_article_ids")
    @classmethod
    def validate_cited_article_ids(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("cited_article_ids must be non-empty")
        for aid in v:
            if not isinstance(aid, str) or not aid.strip():
                raise ValueError("cited_article_ids cannot contain empty strings")
        return v


class DigestSection(BaseModel):
    """A section of the written news digest."""

    heading: str
    body: CitedText

    @field_validator("heading")
    @classmethod
    def validate_heading(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("heading cannot be empty")
        return v.strip()


class Digest(BaseModel):
    """Complete written news digest."""

    title: str
    intro: str
    sections: list[DigestSection] = Field(min_length=1)

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("title cannot be empty")
        return v.strip()


class PodcastSegment(BaseModel):
    """Individual spoken line in podcast script."""

    speaker: str
    line: CitedText

    @field_validator("speaker")
    @classmethod
    def validate_speaker(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("speaker cannot be empty")
        return v.strip()


class PodcastScript(BaseModel):
    """Complete multi-speaker podcast script."""

    title: str
    segments: list[PodcastSegment] = Field(min_length=1)

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("title cannot be empty")
        return v.strip()


class VideoScene(BaseModel):
    """A single scene in the video script."""

    scene_no: int = Field(ge=1)
    on_screen_text: str
    narration: CitedText
    duration_sec: float = Field(gt=0, le=30)


class VideoScript(BaseModel):
    """Complete video script divided into scenes."""

    title: str
    scenes: list[VideoScene] = Field(min_length=1)

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("title cannot be empty")
        return v.strip()

    @model_validator(mode="after")
    def validate_scenes(self) -> VideoScript:
        scene_numbers = [s.scene_no for s in self.scenes]
        expected_numbers = list(range(1, len(self.scenes) + 1))
        if scene_numbers != expected_numbers:
            raise ValueError(
                f"scene_no must be exactly 1..{len(self.scenes)} without gaps or duplicates, got {scene_numbers}"
            )
        return self


class Caption(BaseModel):
    """Social media caption targeting a specific platform."""

    platform: Literal["facebook", "youtube", "tiktok", "generic"]
    text: str
    hashtags: list[str] = Field(default_factory=list)
    cited_article_ids: list[str] = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def validate_text(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("text cannot be empty")
        return v.strip()

    @field_validator("cited_article_ids")
    @classmethod
    def validate_cited_article_ids(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("cited_article_ids must be non-empty")
        for aid in v:
            if not isinstance(aid, str) or not aid.strip():
                raise ValueError("cited_article_ids cannot contain empty strings")
        return v


class EditorialPackage(BaseModel):
    """Complete editorial package generated for a run."""

    run_id: str
    language: Literal["vi"]
    digest: Digest
    podcast: PodcastScript
    video: VideoScript
    captions: list[Caption] = Field(min_length=1)

    def all_cited_ids(self) -> set[str]:
        """Return every article id cited anywhere in the editorial package."""
        cited: set[str] = set()
        for section in self.digest.sections:
            cited.update(section.body.cited_article_ids)
        for segment in self.podcast.segments:
            cited.update(segment.line.cited_article_ids)
        for scene in self.video.scenes:
            cited.update(scene.narration.cited_article_ids)
        for caption in self.captions:
            cited.update(caption.cited_article_ids)
        return cited


class StepLog(BaseModel):
    """Log record for an execution step within a pipeline run."""

    run_id: str
    step: str
    status: Literal["started", "succeeded", "failed", "retrying", "skipped"]
    attempt: int = Field(ge=1)
    message: str = ""
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def normalize_utc(cls, v: datetime) -> datetime:
        return v.astimezone(timezone.utc)

