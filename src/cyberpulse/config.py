"""Configuration loading and validation for CyberPulse AI."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal
import tomllib
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError, field_validator


class ConfigError(Exception):
    """Raised when configuration file is missing, malformed, or invalid."""


KNOWN_SECRETS: tuple[str, ...] = ("GEMINI_API_KEY", "GEMINI_MODEL")


class PathsConfig(BaseModel):
    """Runtime directory paths."""

    data_dir: Path = Field(default_factory=lambda: Path("data"))
    output_dir: Path = Field(default_factory=lambda: Path("output"))
    log_dir: Path = Field(default_factory=lambda: Path("logs"))

    def ensure_dirs(self) -> None:
        """Create directories at runtime if missing."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


class Source(BaseModel):
    """RSS / News source definition."""

    name: str
    url: str
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("name cannot be empty")
        return v.strip()

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("url cannot be empty")
        return v.strip()


class FetchConfig(BaseModel):
    """RSS fetcher configuration."""

    timeout_sec: int = 15
    max_bytes: int = 5_000_000
    retries: int = 1
    user_agent: str = (
        "CyberPulseAI/0.1 (+https://github.com/DuwcsAnh1710/cybernews-agentic-ai)"
    )

    @field_validator("timeout_sec")
    @classmethod
    def validate_timeout_sec(cls, v: int) -> int:
        if not (1 <= v <= 60):
            raise ValueError(f"timeout_sec must be between 1 and 60, got {v}")
        return v

    @field_validator("max_bytes")
    @classmethod
    def validate_max_bytes(cls, v: int) -> int:
        if v < 10_000:
            raise ValueError(f"max_bytes must be >= 10000, got {v}")
        return v

    @field_validator("retries")
    @classmethod
    def validate_retries(cls, v: int) -> int:
        if not (0 <= v <= 3):
            raise ValueError(f"retries must be between 0 and 3, got {v}")
        return v

    @field_validator("user_agent")
    @classmethod
    def validate_user_agent(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("user_agent cannot be empty")
        return v.strip()


class NormalizeConfig(BaseModel):
    """Article normalization, filtering, and deduplication configuration."""

    title_similarity: float = 0.9
    summary_max_chars: int = 1000
    future_tolerance_minutes: int = 60

    @field_validator("title_similarity")
    @classmethod
    def validate_title_similarity(cls, v: float) -> float:
        if not (0.5 <= v <= 1.0):
            raise ValueError(f"title_similarity must be between 0.5 and 1.0, got {v}")
        return v

    @field_validator("summary_max_chars")
    @classmethod
    def validate_summary_max_chars(cls, v: int) -> int:
        if not (100 <= v <= 5000):
            raise ValueError(f"summary_max_chars must be between 100 and 5000, got {v}")
        return v

    @field_validator("future_tolerance_minutes")
    @classmethod
    def validate_future_tolerance_minutes(cls, v: int) -> int:
        if not (0 <= v <= 1440):
            raise ValueError(f"future_tolerance_minutes must be between 0 and 1440, got {v}")
        return v


class LLMConfig(BaseModel):
    """LLM client configuration."""

    provider: Literal["gemini", "fake"] = "gemini"
    model: str = "gemini-3.8-flash"
    thinking_level: Literal["low", "medium", "high"] = "low"
    max_output_tokens: int = 8192
    timeout_sec: int = 60
    max_retries: int = 2
    schema_retries: int = 1
    backoff_base_sec: float = 2.0

    @field_validator("model")
    @classmethod
    def validate_model(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("model cannot be empty")
        return v.strip()

    @field_validator("max_output_tokens")
    @classmethod
    def validate_max_output_tokens(cls, v: int) -> int:
        if not (1024 <= v <= 65536):
            raise ValueError(f"max_output_tokens must be between 1024 and 65536, got {v}")
        return v

    @field_validator("timeout_sec")
    @classmethod
    def validate_timeout_sec(cls, v: int) -> int:
        if not (5 <= v <= 300):
            raise ValueError(f"timeout_sec must be between 5 and 300, got {v}")
        return v

    @field_validator("max_retries")
    @classmethod
    def validate_max_retries(cls, v: int) -> int:
        if not (0 <= v <= 5):
            raise ValueError(f"max_retries must be between 0 and 5, got {v}")
        return v

    @field_validator("schema_retries")
    @classmethod
    def validate_schema_retries(cls, v: int) -> int:
        if not (0 <= v <= 2):
            raise ValueError(f"schema_retries must be between 0 and 2, got {v}")
        return v

    @field_validator("backoff_base_sec")
    @classmethod
    def validate_backoff_base_sec(cls, v: float) -> float:
        if not (0.0 <= v <= 30.0):
            raise ValueError(f"backoff_base_sec must be between 0.0 and 30.0, got {v}")
        return v


class ResearchConfig(BaseModel):
    """Research agent configuration."""

    max_candidates: int = 30
    shortlist_extra: int = 5
    max_per_source: int = 2
    summary_chars_in_prompt: int = 300

    @field_validator("max_candidates")
    @classmethod
    def validate_max_candidates(cls, v: int) -> int:
        if not (5 <= v <= 60):
            raise ValueError(f"max_candidates must be between 5 and 60, got {v}")
        return v

    @field_validator("shortlist_extra")
    @classmethod
    def validate_shortlist_extra(cls, v: int) -> int:
        if not (0 <= v <= 10):
            raise ValueError(f"shortlist_extra must be between 0 and 10, got {v}")
        return v

    @field_validator("max_per_source")
    @classmethod
    def validate_max_per_source(cls, v: int) -> int:
        if not (1 <= v <= 10):
            raise ValueError(f"max_per_source must be between 1 and 10, got {v}")
        return v

    @field_validator("summary_chars_in_prompt")
    @classmethod
    def validate_summary_chars_in_prompt(cls, v: int) -> int:
        if not (50 <= v <= 1000):
            raise ValueError(f"summary_chars_in_prompt must be between 50 and 1000, got {v}")
        return v


class Settings(BaseModel):
    """Application settings."""

    project_name: str
    topics: list[str]
    items_per_run: int = 5
    lookback_hours: int = 48
    output_language: str = "vi"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    paths: PathsConfig = Field(default_factory=PathsConfig)
    fetch: FetchConfig = Field(default_factory=FetchConfig)
    normalize: NormalizeConfig = Field(default_factory=NormalizeConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    research: ResearchConfig = Field(default_factory=ResearchConfig)

    @field_validator("project_name")
    @classmethod
    def validate_project_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("project_name cannot be empty")
        return v.strip()

    @field_validator("topics")
    @classmethod
    def validate_topics(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("topics must be a non-empty list")
        clean_topics = [t.strip() for t in v if isinstance(t, str) and t.strip()]
        if not clean_topics or len(clean_topics) != len(v):
            raise ValueError("topics cannot contain empty strings")
        return clean_topics

    @field_validator("items_per_run")
    @classmethod
    def validate_items_per_run(cls, v: int) -> int:
        if not (1 <= v <= 10):
            raise ValueError(f"items_per_run must be between 1 and 10, got {v}")
        return v

    @field_validator("lookback_hours")
    @classmethod
    def validate_lookback_hours(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"lookback_hours must be greater than 0, got {v}")
        return v

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, v: object) -> str:
        if isinstance(v, str) and v.upper() in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            return v.upper()
        raise ValueError("log_level must be one of DEBUG, INFO, WARNING, ERROR")


def _format_validation_error(err: ValidationError, filepath: Path | str) -> str:
    messages: list[str] = []
    for error in err.errors():
        field = ".".join(str(loc) for loc in error.get("loc", [])) or "root"
        msg = error.get("msg", "invalid value")
        messages.append(f"field '{field}': {msg}")
    return f"Invalid configuration in '{filepath}': " + "; ".join(messages)


def load_settings(path: Path | str = "config/settings.toml") -> Settings:
    """Load and validate settings from a TOML file.

    Raises:
        ConfigError: If the file is missing, malformed, or fails validation.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise ConfigError(f"Configuration file not found: '{file_path}'")

    try:
        with open(file_path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Malformed TOML in '{file_path}': {e}") from e
    except Exception as e:
        raise ConfigError(f"Error reading '{file_path}': {e}") from e

    if not isinstance(data, dict):
        raise ConfigError(f"Invalid configuration in '{file_path}': expected TOML table at root")

    try:
        settings = Settings.model_validate(data)
    except ValidationError as e:
        raise ConfigError(_format_validation_error(e, file_path)) from e

    # Create runtime directories if missing
    settings.paths.ensure_dirs()
    return settings


def load_sources(path: Path | str = "config/sources.toml") -> list[Source]:
    """Load and validate sources from a TOML file.

    Raises:
        ConfigError: If the file is missing, malformed, or has no enabled sources.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise ConfigError(f"Configuration file not found: '{file_path}'")

    try:
        with open(file_path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Malformed TOML in '{file_path}': {e}") from e
    except Exception as e:
        raise ConfigError(f"Error reading '{file_path}': {e}") from e

    if not isinstance(data, dict) or "sources" not in data or not isinstance(data["sources"], list):
        raise ConfigError(f"Invalid configuration in '{file_path}': field 'sources' must be a list of sources")

    sources_raw = data["sources"]
    if not sources_raw:
        raise ConfigError(f"Invalid configuration in '{file_path}': field 'sources' cannot be empty")

    sources: list[Source] = []
    for idx, item in enumerate(sources_raw):
        if not isinstance(item, dict):
            raise ConfigError(f"Invalid configuration in '{file_path}': field 'sources[{idx}]' must be a table")
        try:
            sources.append(Source.model_validate(item))
        except ValidationError as e:
            errors: list[str] = []
            for err in e.errors():
                loc = ".".join(str(l) for l in err.get("loc", []))
                field_name = f"sources[{idx}].{loc}" if loc else f"sources[{idx}]"
                errors.append(f"field '{field_name}' ({loc or 'source'}): {err.get('msg')}")
            raise ConfigError(f"Invalid configuration in '{file_path}': " + "; ".join(errors)) from e

    enabled_count = sum(1 for s in sources if s.enabled)
    if enabled_count == 0:
        raise ConfigError(f"Invalid configuration in '{file_path}': at least one source must be enabled")

    return sources


def load_app_config(
    settings_path: Path | str = "config/settings.toml",
    sources_path: Path | str = "config/sources.toml",
) -> tuple[Settings, list[Source]]:
    """Load both settings and sources."""
    settings = load_settings(settings_path)
    sources = load_sources(sources_path)
    return settings, sources


def check_secrets(env_file: Path | str | None = None) -> dict[str, str]:
    """Check known secrets status from environment and .env file.

    Returns mapping of secret name to 'set' or 'missing'. Never reveals values.
    """
    if env_file is not None:
        load_dotenv(dotenv_path=env_file, override=False)
    else:
        load_dotenv(override=False)

    status: dict[str, str] = {}
    for secret_name in KNOWN_SECRETS:
        val = os.environ.get(secret_name)
        if val is not None and val.strip() != "":
            status[secret_name] = "set"
        else:
            status[secret_name] = "missing"
    return status
