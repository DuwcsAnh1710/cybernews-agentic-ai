"""Command line interface for CyberPulse AI."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Literal, Sequence

from dotenv import load_dotenv
from pydantic import BaseModel

from cyberpulse import __version__
from cyberpulse.agents import (
    EditorialAgent,
    EditorialFormatError,
    EditorialRefError,
    ResearchAgent,
)
from cyberpulse.config import ConfigError, check_secrets, load_app_config, load_settings
from cyberpulse.fetcher import RawItem, fetch_all
from cyberpulse.llm import LLMError, create_llm_client
from cyberpulse.logging_setup import setup_logging
from cyberpulse.models import Article, EditorialPackage, ResearchResult, RunStatus
from cyberpulse.normalize import normalize_items
from cyberpulse.orchestrator import FailureReason, Orchestrator, RunResult, sanitize_error_message
from cyberpulse.render_preview import render_markdown
from cyberpulse.runlock import RunLock, RunLockBusyError
from cyberpulse.storage import connect, get_db_path, init_db, list_recent_articles, upsert_article
from cyberpulse.validator import ValidationIssue, ValidationReport, validate_package
from cyberpulse.media.podcast import (
    MediaDependencyError,
    MediaInputError,
    MediaSynthesisError,
    PodcastMediaOptions,
    synthesize_podcast,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="cyberpulse",
        description="CyberPulse AI - Automated cybersecurity news intelligence and multimedia pipeline",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # run subcommand (T5.2)
    run_parser = subparsers.add_parser(
        "run", help="Execute complete automated pipeline run (Agent 1 -> Agent 2 -> Validate -> Layer 3)"
    )
    run_parser.add_argument(
        "--settings",
        type=Path,
        default=Path("config/settings.toml"),
        help="Path to settings.toml (default: config/settings.toml)",
    )
    run_parser.add_argument(
        "--sources",
        type=Path,
        default=Path("config/sources.toml"),
        help="Path to sources.toml (default: config/sources.toml)",
    )
    run_parser.add_argument(
        "--allow-insufficient",
        action="store_true",
        default=False,
        help="Allow run to proceed in degraded mode if candidates are insufficient",
    )
    run_parser.add_argument(
        "--max-rewrites",
        type=int,
        default=2,
        help="Maximum rewrite attempts (0 to 3, default: 2)",
    )
    run_parser.add_argument(
        "--no-llm-review",
        action="store_true",
        default=False,
        help="Skip post-loop Layer 3 LLM claim review",
    )
    run_parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Custom run ID (optional, default auto-generated)",
    )

    # version subcommand
    subparsers.add_parser("version", help="Show application version")

    # check-config subcommand
    check_cfg_parser = subparsers.add_parser(
        "check-config", help="Validate configuration files and secret status"
    )
    check_cfg_parser.add_argument(
        "--settings",
        type=Path,
        default=Path("config/settings.toml"),
        help="Path to settings.toml (default: config/settings.toml)",
    )
    check_cfg_parser.add_argument(
        "--sources",
        type=Path,
        default=Path("config/sources.toml"),
        help="Path to sources.toml (default: config/sources.toml)",
    )

    # init-db subcommand
    init_db_parser = subparsers.add_parser(
        "init-db", help="Initialize the SQLite database"
    )
    init_db_parser.add_argument(
        "--settings",
        type=Path,
        default=Path("config/settings.toml"),
        help="Path to settings.toml (default: config/settings.toml)",
    )

    # fetch subcommand
    fetch_parser = subparsers.add_parser(
        "fetch", help="Fetch raw items from enabled RSS sources"
    )
    fetch_parser.add_argument(
        "--settings",
        type=Path,
        default=Path("config/settings.toml"),
        help="Path to settings.toml (default: config/settings.toml)",
    )
    fetch_parser.add_argument(
        "--sources",
        type=Path,
        default=Path("config/sources.toml"),
        help="Path to sources.toml (default: config/sources.toml)",
    )
    fetch_parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="Maximum items to display per source (default: 3)",
    )

    # collect subcommand
    collect_parser = subparsers.add_parser(
        "collect",
        help="Fetch, normalize, deduplicate, and persist articles to SQLite",
    )
    collect_parser.add_argument(
        "--settings",
        type=Path,
        default=Path("config/settings.toml"),
        help="Path to settings.toml (default: config/settings.toml)",
    )
    collect_parser.add_argument(
        "--sources",
        type=Path,
        default=Path("config/sources.toml"),
        help="Path to sources.toml (default: config/sources.toml)",
    )
    collect_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Run fetch and normalization without saving to database",
    )

    # llm-check subcommand
    llm_check_parser = subparsers.add_parser(
        "llm-check", help="Verify LLM client connectivity and structured output"
    )
    llm_check_parser.add_argument(
        "--settings",
        type=Path,
        default=Path("config/settings.toml"),
        help="Path to settings.toml (default: config/settings.toml)",
    )

    # research subcommand
    research_parser = subparsers.add_parser(
        "research", help="Rank and select candidate articles using LLM (Agent 1)"
    )
    research_parser.add_argument(
        "--settings",
        type=Path,
        default=Path("config/settings.toml"),
        help="Path to settings.toml (default: config/settings.toml)",
    )
    research_parser.add_argument(
        "--sources",
        type=Path,
        default=Path("config/sources.toml"),
        help="Path to sources.toml (default: config/sources.toml)",
    )

    # editorial subcommand
    editorial_parser = subparsers.add_parser(
        "editorial",
        help="Generate Vietnamese digest, captions, podcast and video scripts (Agent 2)",
    )
    editorial_parser.add_argument(
        "--research",
        type=Path,
        required=True,
        help="Path to ResearchResult JSON file",
    )
    editorial_parser.add_argument(
        "--settings",
        type=Path,
        default=Path("config/settings.toml"),
        help="Path to settings.toml (default: config/settings.toml)",
    )

    # validate subcommand
    validate_parser = subparsers.add_parser(
        "validate",
        help="Run structural, source-consistency, and claim review checks on editorial output",
    )
    validate_parser.add_argument(
        "--research",
        type=Path,
        required=True,
        help="Path to ResearchResult JSON file",
    )
    validate_parser.add_argument(
        "--editorial",
        type=Path,
        required=True,
        help="Path to EditorialPackage JSON file",
    )
    validate_parser.add_argument(
        "--no-llm-review",
        action="store_true",
        default=False,
        help="Skip Layer 3 LLM claim review",
    )
    validate_parser.add_argument(
        "--settings",
        type=Path,
        default=Path("config/settings.toml"),
        help="Path to settings.toml (default: config/settings.toml)",
    )

    # ui subcommand
    ui_parser = subparsers.add_parser(
        "ui",
        help="Launch the Streamlit inspection and review workbench",
    )
    ui_parser.add_argument(
        "--port",
        type=int,
        default=8501,
        help="Port to bind the Streamlit server (default: 8501, range: 1024-65535)",
    )
    ui_parser.add_argument(
        "--settings",
        type=Path,
        default=Path("config/settings.toml"),
        help="Path to settings.toml (default: config/settings.toml)",
    )
    ui_parser.add_argument(
        "--print-command",
        action="store_true",
        default=False,
        help="Print the Streamlit command line arguments without starting the server",
    )

    # media subcommand (media podcast ...)
    media_parser = subparsers.add_parser(
        "media",
        help="Generate multimedia assets (podcast, video)",
    )
    media_subparsers = media_parser.add_subparsers(dest="subcommand", required=True)
    podcast_parser = media_subparsers.add_parser(
        "podcast",
        help="Synthesize multi-speaker podcast audio and synchronized SRT",
    )
    podcast_parser.add_argument(
        "run_id",
        type=str,
        help="Run identifier of the editorial package to synthesize",
    )
    podcast_parser.add_argument(
        "--settings",
        type=Path,
        default=Path("config/settings.toml"),
        help="Path to settings.toml (default: config/settings.toml)",
    )
    podcast_parser.add_argument(
        "--voice",
        action="append",
        default=[],
        help="Speaker voice override in SPEAKER=VOICE format (repeatable)",
    )
    podcast_parser.add_argument(
        "--pause-ms",
        type=int,
        default=350,
        help="Pause duration between different speakers in milliseconds (default: 350)",
    )
    podcast_parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Overwrite existing output files",
    )
    podcast_parser.add_argument(
        "--allow-unvalidated",
        action="store_true",
        default=False,
        help="Synthesize podcast audio even if validation report is missing or failed",
    )

    # export subcommand (export bundle ...)
    export_parser = subparsers.add_parser(
        "export",
        help="Export and package editorial assets",
    )
    export_subparsers = export_parser.add_subparsers(dest="subcommand", required=True)
    bundle_parser = export_subparsers.add_parser(
        "bundle",
        help="Export complete multi-channel distribution bundle",
    )
    bundle_parser.add_argument(
        "run_id",
        type=str,
        help="Run identifier of the editorial package to export",
    )
    bundle_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="Base output directory containing run artifacts (default: output)",
    )
    bundle_parser.add_argument(
        "--export-dir",
        type=Path,
        default=Path("export"),
        help="Destination directory for exported bundles (default: export)",
    )

    # export portal subcommand (T9)
    portal_parser = export_subparsers.add_parser(
        "portal",
        help="Generate public reader static editorial portal website (The Hacker News style)",
    )
    portal_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="Base output directory containing run artifacts (default: output)",
    )
    portal_parser.add_argument(
        "--dist-dir",
        type=Path,
        default=Path("dist"),
        help="Destination directory for static website (default: dist)",
    )
    portal_parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Specific edition to feature on index.html (default: newest)",
    )
    portal_parser.add_argument(
        "--serve",
        action="store_true",
        default=False,
        help="Start local HTTP server to preview static portal",
    )
    portal_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for local HTTP server (default: 8000)",
    )

    return parser


def run_version() -> int:
    """Print application version."""
    print(f"cyberpulse {__version__}")
    return 0


def run_check_config(settings_path: Path, sources_path: Path) -> int:
    """Validate configuration and print status."""
    try:
        settings, sources = load_app_config(
            settings_path=settings_path,
            sources_path=sources_path,
        )
        logger = setup_logging(
            log_level=settings.log_level,
            log_dir=settings.paths.log_dir,
            logger_name="cyberpulse",
        )
        enabled_count = sum(1 for s in sources if s.enabled)
        secrets = check_secrets()

        print(f"Project name: {settings.project_name}")
        print(f"Topics: {', '.join(settings.topics)}")
        print(f"Items per run: {settings.items_per_run}")
        print(f"Lookback hours: {settings.lookback_hours}")
        print(f"Output language: {settings.output_language}")
        print(f"Enabled sources: {enabled_count}")

        for secret_name, status in secrets.items():
            print(f"{secret_name}: {status}")

        logger.info("Configuration check succeeded.")
        return 0
    except ConfigError as err:
        print(f"ConfigError: {err}", file=sys.stderr)
        return 1


def run_init_db(settings_path: Path) -> int:
    """Initialize SQLite database and output status."""
    try:
        settings = load_settings(settings_path)
        db_path = get_db_path(settings)
        conn = connect(db_path)
        try:
            init_db(conn)
        finally:
            conn.close()
        print(f"Database initialized: {db_path} (schema version 1)")
        return 0
    except ConfigError as err:
        print(f"ConfigError: {err}", file=sys.stderr)
        return 1


def run_fetch(settings_path: Path, sources_path: Path, limit: int) -> int:
    """Fetch raw items from RSS sources and display results."""
    try:
        settings, sources = load_app_config(
            settings_path=settings_path,
            sources_path=sources_path,
        )
    except ConfigError as err:
        print(f"ConfigError: {err}", file=sys.stderr)
        return 1

    setup_logging(
        log_level=settings.log_level,
        log_dir=settings.paths.log_dir,
        logger_name="cyberpulse",
    )

    report = fetch_all(sources=sources, cfg=settings.fetch)

    items_by_source: dict[str, list[RawItem]] = {}
    for item in report.items:
        items_by_source.setdefault(item.source_name, []).append(item)

    for result in report.results:
        if result.ok:
            print(f"[OK] {result.source_name}: {result.item_count} items ({result.elapsed_ms} ms)")
            if limit > 0:
                source_items = items_by_source.get(result.source_name, [])
                for it in source_items[:limit]:
                    pub_str = str(it.published_at) if it.published_at else "no date"
                    title_str = it.title if it.title else "NO TITLE"
                    link_str = it.link if it.link else "NO LINK"
                    print(f"- {pub_str} | {title_str} | {link_str}")
        else:
            print(f"[FAIL] {result.source_name}: {result.error}")

    print(f"Total raw items: {len(report.items)}")

    if report.all_failed:
        return 2
    return 0


def run_collect(settings_path: Path, sources_path: Path, dry_run: bool) -> int:
    """Fetch, normalize, deduplicate, and save articles."""
    try:
        settings, sources = load_app_config(
            settings_path=settings_path,
            sources_path=sources_path,
        )
    except ConfigError as err:
        print(f"ConfigError: {err}", file=sys.stderr)
        return 1

    logger = setup_logging(
        log_level=settings.log_level,
        log_dir=settings.paths.log_dir,
        logger_name="cyberpulse",
    )

    report = fetch_all(sources=sources, cfg=settings.fetch)

    for result in report.results:
        if result.ok:
            print(f"[OK] {result.source_name}: {result.item_count} items ({result.elapsed_ms} ms)")
        else:
            print(f"[FAIL] {result.source_name}: {result.error}")

    norm_report = normalize_items(report.items, settings)

    counts_by_reason = norm_report.counts_by_reason
    dropped_parts = [f"{k}={v}" for k, v in counts_by_reason.items() if v > 0]
    dropped_detail = f" ({', '.join(dropped_parts)})" if dropped_parts else ""

    print(f"Raw items: {len(report.items)}")
    print(f"Kept articles: {len(norm_report.articles)}")
    print(f"Dropped: {len(norm_report.dropped)}{dropped_detail}")

    for article in norm_report.articles[:5]:
        topics_str = ", ".join(article.topics_matched) if article.topics_matched else "none"
        print(f"- {article.published_at} | {article.source_name} | {article.title} | topics={topics_str}")

    if not dry_run:
        db_path = get_db_path(settings)
        conn = connect(db_path)
        try:
            init_db(conn)
            with conn:
                for article in norm_report.articles:
                    upsert_article(conn, article)
        finally:
            conn.close()
        print(f"Saved to database: {len(norm_report.articles)} articles")
    else:
        print("Dry run: nothing saved")

    logger.info(
        "Collect finished: raw=%d, kept=%d, dropped=%d, dry_run=%s",
        len(report.items),
        len(norm_report.articles),
        len(norm_report.dropped),
        dry_run,
    )

    if report.all_failed:
        return 2
    if len(norm_report.articles) == 0:
        return 3
    return 0


class LLMCheckResponse(BaseModel):
    """Schema for llm-check response verification."""

    ok: bool
    language: Literal["vi"]
    message: str


def run_llm_check(settings_path: Path) -> int:
    """Run a single live request to verify LLM client connectivity."""
    load_dotenv(override=False)

    try:
        settings = load_settings(settings_path)
    except ConfigError as err:
        print(f"ConfigError: {err}", file=sys.stderr)
        return 1

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or not api_key.strip():
        print("GEMINI_API_KEY: missing — live LLM check skipped. See README \"LLM setup\".")
        return 4

    try:
        client = create_llm_client(settings)
        result = client.generate_json(
            system="You are CyberPulse AI assistant.",
            prompt='Reply with ok=true, language="vi", and message = a one-sentence Vietnamese greeting.',
            schema=LLMCheckResponse,
            task_name="llm_check",
        )
        in_tok = result.input_tokens if result.input_tokens is not None else "unknown"
        out_tok = result.output_tokens if result.output_tokens is not None else "unknown"
        print(f"Model: {result.model}")
        print(f"Attempts: {result.attempts}")
        print(f"Tokens: in={in_tok} out={out_tok}")
        print(f"Latency: {result.latency_ms} ms")
        print(f"Response: {result.data.message}")
        return 0
    except LLMError as err:
        print(f"[LLM ERROR] {err.__class__.__name__}: {err}")
        return 5


def run_research(settings_path: Path, sources_path: Path | None = None) -> int:
    """Rank and select candidate articles using Agent 1 (News Research Agent)."""
    try:
        settings = load_settings(settings_path)
    except ConfigError as err:
        print(f"ConfigError: {err}", file=sys.stderr)
        return 1

    setup_logging(
        log_level=settings.log_level,
        log_dir=settings.paths.log_dir,
        logger_name="cyberpulse",
    )

    db_path = get_db_path(settings)
    conn = connect(db_path)
    try:
        init_db(conn)
        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=settings.lookback_hours)
        candidates = list_recent_articles(
            conn,
            since=since,
            limit=settings.research.max_candidates,
        )
    finally:
        conn.close()

    if not candidates:
        print("No candidate articles in the database. Run: cyberpulse collect")
        return 6

    if settings.llm.provider == "gemini":
        load_dotenv(override=False)
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key or not api_key.strip():
            print("GEMINI_API_KEY: missing — live LLM check skipped. See README \"LLM setup\".")
            return 4

    run_id = "standalone-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    try:
        client = create_llm_client(settings)
        agent = ResearchAgent(llm=client, settings=settings)
        result = agent.run(run_id=run_id, candidates=candidates)
    except LLMError as err:
        print(f"[LLM ERROR] {err.__class__.__name__}: {err}")
        return 5

    for item in result.items:
        print(f"{item.rank}. [{item.article.source_name}] {item.article.title}")
        print(f"   {item.article.url}")
        print(f"   Lý do: {item.relevance_reason}")

    print(f"Status: {result.status}")
    if result.notes:
        print(f"Notes: {result.notes}")

    research_dir = settings.paths.output_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    json_path = research_dir / f"{run_id}.json"
    json_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    print(f"Saved research result: {json_path}")

    return 0 if result.status == "ok" else 3


def run_editorial(research_path: Path, settings_path: Path) -> int:
    """Run Editorial Agent (Agent 2) to generate editorial package and preview."""
    try:
        settings = load_settings(settings_path)
    except ConfigError as err:
        print(f"ConfigError: {err}", file=sys.stderr)
        return 1

    if not research_path.exists():
        print(f"Error: research file not found: '{research_path}'", file=sys.stderr)
        return 7

    try:
        raw_text = research_path.read_text(encoding="utf-8")
        research = ResearchResult.model_validate_json(raw_text)
    except Exception as err:
        print(f"Error: invalid research file '{research_path}': {err}", file=sys.stderr)
        return 7

    if not research.items:
        print(f"Error: research result contains no items: '{research_path}'", file=sys.stderr)
        return 7

    if settings.llm.provider == "gemini":
        load_dotenv(override=False)
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key or not api_key.strip():
            print('GEMINI_API_KEY: missing — live LLM check skipped. See README "LLM setup".')
            return 4

    setup_logging(
        log_level=settings.log_level,
        log_dir=settings.paths.log_dir,
        logger_name="cyberpulse",
    )

    try:
        client = create_llm_client(settings)
        agent = EditorialAgent(llm=client, settings=settings)
        pkg = agent.run(research)
    except LLMError as err:
        print(f"[LLM ERROR] {err.__class__.__name__}: {err}")
        return 5
    except EditorialRefError as err:
        print(f"EditorialRefError: {err}", file=sys.stderr)
        print(f"Unknown refs: {err.unknown_refs}", file=sys.stderr)
        print(f"Field paths: {err.field_paths}", file=sys.stderr)
        return 7
    except EditorialFormatError as err:
        print(f"EditorialFormatError: {err}", file=sys.stderr)
        if err.field_paths:
            print(f"Field paths: {err.field_paths}", file=sys.stderr)
        return 7
    except ValueError as err:
        print(f"ValueError: {err}", file=sys.stderr)
        return 7

    preview_md = render_markdown(pkg, research)

    editorial_dir = settings.paths.output_dir / "editorial"
    editorial_dir.mkdir(parents=True, exist_ok=True)
    json_path = editorial_dir / f"{research.run_id}.json"
    md_path = editorial_dir / f"{research.run_id}.md"

    json_path.write_text(pkg.model_dump_json(indent=2), encoding="utf-8")
    md_path.write_text(preview_md, encoding="utf-8")

    podcast_words = sum(len(seg.line.text.split()) for seg in pkg.podcast.segments)
    video_total_sec = sum(sc.duration_sec for sc in pkg.video.scenes)
    caption_platforms_str = ", ".join(c.platform for c in pkg.captions)

    print(f"Saved editorial package: {json_path}")
    print(f"Saved editorial preview: {md_path}")
    print("Editorial Summary:")
    print(f"- Sections: {len(pkg.digest.sections)}")
    print(f"- Captions: {len(pkg.captions)} ({caption_platforms_str})")
    print(f"- Podcast segments: {len(pkg.podcast.segments)} (~{podcast_words} words)")
    print(f"- Video scenes: {len(pkg.video.scenes)} ({video_total_sec:.1f}s total)")
    return 0


def run_validate(
    research_path: Path,
    editorial_path: Path,
    settings_path: Path,
    no_llm_review: bool = False,
) -> int:
    """Run validation on research and editorial packages."""
    try:
        settings = load_settings(settings_path)
    except ConfigError as err:
        print(f"ConfigError: {err}", file=sys.stderr)
        return 1

    if not research_path.exists():
        print(f"Error: research file not found: '{research_path}'", file=sys.stderr)
        return 7

    try:
        raw_research = research_path.read_text(encoding="utf-8")
        research = ResearchResult.model_validate_json(raw_research)
    except Exception as err:
        print(f"Error: invalid research file '{research_path}': {err}", file=sys.stderr)
        return 7

    if not editorial_path.exists():
        print(f"Error: editorial file not found: '{editorial_path}'", file=sys.stderr)
        return 7

    try:
        raw_editorial = editorial_path.read_text(encoding="utf-8")
        pkg = EditorialPackage.model_validate_json(raw_editorial)
    except Exception as err:
        print(f"Error: invalid editorial file '{editorial_path}': {err}", file=sys.stderr)
        return 7

    setup_logging(
        log_level=settings.log_level,
        log_dir=settings.paths.log_dir,
        logger_name="cyberpulse",
    )

    llm: LLMClient | None = None
    missing_key_warning = False

    if not no_llm_review and settings.validator.llm_review:
        if settings.llm.provider == "gemini":
            load_dotenv(override=False)
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key or not api_key.strip():
                print("LLM review skipped: GEMINI_API_KEY missing")
                missing_key_warning = True
            else:
                try:
                    llm = create_llm_client(settings)
                except Exception as err:
                    print(f"Error creating LLM client: {err}", file=sys.stderr)
                    missing_key_warning = True
        elif settings.llm.provider == "fake":
            llm = create_llm_client(settings)

    report = validate_package(
        research=research,
        pkg=pkg,
        settings=settings,
        llm=llm,
    )

    if missing_key_warning:
        report.warnings.append(
            ValidationIssue(
                code="W_LLM_REVIEW_UNAVAILABLE",
                severity="warning",
                path="llm_review",
                message="LLM claim review skipped: GEMINI_API_KEY missing",
                evidence="GEMINI_API_KEY missing",
            )
        )

    # Write report JSON
    validation_dir = settings.paths.output_dir / "validation"
    validation_dir.mkdir(parents=True, exist_ok=True)
    report_path = validation_dir / f"{pkg.run_id}.json"
    report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    # Console output
    passed_str = "yes" if report.passed else "no"
    print(f"Passed: {passed_str}")
    print(f"Errors: {len(report.errors)}, Warnings: {len(report.warnings)}")

    for issue in report.errors:
        evidence_part = f" | {issue.evidence}" if issue.evidence else ""
        print(f"[ERROR] {issue.code} {issue.path} — {issue.message}{evidence_part}")

    for issue in report.warnings:
        evidence_part = f" | {issue.evidence}" if issue.evidence else ""
        print(f"[WARN] {issue.code} {issue.path} — {issue.message}{evidence_part}")

    print(report.scope_note)
    print(f"Saved validation report: {report_path}")

    return 0 if report.passed else 8


def run_ui(
    port: int,
    settings_path: Path,
    print_command: bool = False,
) -> int:
    """Launch or print command for the Streamlit workbench."""
    if not (1024 <= port <= 65535):
        print(f"Error: port must be between 1024 and 65535, got {port}", file=sys.stderr)
        return 1

    try:
        load_settings(settings_path)
    except ConfigError as err:
        print(f"ConfigError: {err}", file=sys.stderr)
        return 1

    if importlib.util.find_spec("streamlit") is None:
        print(
            "Streamlit is not installed. Run: .\\.venv\\Scripts\\python.exe -m pip install -e \".[ui]\"",
            file=sys.stderr,
        )
        return 9

    app_path = Path(__file__).parent / "ui" / "app.py"
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path.resolve()),
        "--server.address",
        "127.0.0.1",
        "--server.port",
        str(port),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
        "--",
        "--settings",
        str(settings_path.resolve()),
    ]

    if print_command:
        for arg in cmd:
            print(arg)
        return 0

    print(f"Workbench: http://127.0.0.1:{port}  (Ctrl+C to stop)")
    try:
        res = subprocess.run(cmd)
        return res.returncode
    except KeyboardInterrupt:
        return 0


def run_media_podcast(
    run_id: str,
    settings_path: Path,
    voice_args: list[str] | None = None,
    pause_ms: int = 350,
    force: bool = False,
    allow_unvalidated: bool = False,
) -> int:
    """Run podcast audio synthesis and subtitle generation."""
    if not re.match(r"^[A-Za-z0-9._-]+$", run_id):
        print(f"Error: invalid run_id '{run_id}'. Allowed characters: [A-Za-z0-9._-]", file=sys.stderr)
        return 7

    try:
        settings = load_settings(settings_path)
    except ConfigError as err:
        print(f"ConfigError: {err}", file=sys.stderr)
        return 1

    editorial_dir = (settings.paths.output_dir / "editorial").resolve()
    editorial_path = (settings.paths.output_dir / "editorial" / f"{run_id}.json").resolve()

    try:
        editorial_path.relative_to(editorial_dir)
    except ValueError:
        print(f"Error: path traversal attempt with run_id '{run_id}'", file=sys.stderr)
        return 7

    if not editorial_path.exists() or not editorial_path.is_file():
        print(f"Error: editorial file not found: '{editorial_path}'", file=sys.stderr)
        return 7

    try:
        raw_editorial = editorial_path.read_text(encoding="utf-8")
        editorial_pkg = EditorialPackage.model_validate_json(raw_editorial)
    except Exception as err:
        print(f"Error: invalid editorial file '{editorial_path}': {err}", file=sys.stderr)
        return 7

    # Validation Gate (D3)
    val_path = (settings.paths.output_dir / "validation" / f"{run_id}.json").resolve()
    is_validated = False
    if val_path.exists() and val_path.is_file():
        try:
            val_data = json.loads(val_path.read_text(encoding="utf-8"))
            if val_data.get("passed") is True:
                is_validated = True
        except Exception:
            pass

    if not is_validated and not allow_unvalidated:
        print(
            f"Error: Run '{run_id}' has not passed validation (passed=False or report missing). "
            f"Use --allow-unvalidated to override.",
            file=sys.stderr,
        )
        return 8

    if not is_validated and allow_unvalidated:
        print("[WARNING] Synthesizing podcast from UNVALIDATED editorial package!", file=sys.stderr)

    # Voice overrides parsing
    voice_overrides: dict[str, str] = {}
    for item in voice_args or []:
        if "=" not in item:
            print(f"Error: invalid voice argument '{item}'. Expected format SPEAKER=VOICE.", file=sys.stderr)
            return 7
        spk, vname = item.split("=", 1)
        voice_overrides[spk.strip()] = vname.strip()

    options = PodcastMediaOptions(
        voice_overrides=voice_overrides,
        pause_diff_speaker_ms=pause_ms,
        pause_same_speaker_ms=int(pause_ms * 200 / 350) if pause_ms > 0 else 0,
        force=force,
        allow_unvalidated=allow_unvalidated,
    )
    output_media_dir = settings.paths.output_dir / "media" / "podcast"

    print(
        f"Đang tạo audio podcast cho phiên chạy '{run_id}' ({len(editorial_pkg.podcast.segments)} đoạn hội thoại)...",
        flush=True,
    )

    try:
        result = synthesize_podcast(
            run_id=run_id,
            editorial_pkg=editorial_pkg,
            settings=settings,
            options=options,
            output_media_dir=output_media_dir,
        )
    except MediaDependencyError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 12
    except MediaInputError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 7
    except MediaSynthesisError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 13
    except Exception as err:
        print(f"Error: synthesis failed: {err}", file=sys.stderr)
        return 13

    minutes, seconds = divmod(int(result.total_duration_sec), 60)
    dur_str = f"{result.total_duration_sec:.2f}s ({minutes:02d}:{seconds:02d})"
    voice_desc = ", ".join(f"{s} -> {v}" for s, v in sorted(result.voices.items()))

    print("=== Podcast Audio Synthesized ===")
    print(f"Run ID:         {result.run_id}")
    print(f"Segments:       {result.segment_count}")
    print(f"Total Duration: {dur_str}")
    print(f"Voices:         {voice_desc}")
    print(f"Audio File:     {result.audio_path}")
    print(f"Subtitle File:  {result.srt_path}")
    print(f"Metadata File:  {result.meta_path}")
    return 0


def run_export_bundle(
    run_id: str,
    output_dir: Path = Path("output"),
    export_dir: Path = Path("export"),
) -> int:
    """Export complete multi-channel distribution bundle for a run."""
    editorial_path = output_dir / "editorial" / f"{run_id}.json"
    if not editorial_path.exists() or not editorial_path.is_file():
        print(f"Error: Editorial package not found at {editorial_path}", file=sys.stderr)
        return 2

    try:
        raw_editorial = editorial_path.read_text(encoding="utf-8")
        pkg = EditorialPackage.model_validate_json(raw_editorial)
    except Exception as err:
        print(f"Error loading editorial package from {editorial_path}: {err}", file=sys.stderr)
        return 2

    try:
        from cyberpulse.export import export_editorial_bundle

        result = export_editorial_bundle(
            package=pkg,
            run_id=run_id,
            output_dir=output_dir,
            export_root=export_dir,
        )
        print(f"Successfully exported bundle for run '{run_id}' to: {result.export_dir}")
        print(f"Created {len(result.files_created)} files: {', '.join(result.files_created)}")
        return 0
    except Exception as err:
        print(f"Error exporting bundle: {err}", file=sys.stderr)
        return 1


def run_export_portal(
    output_dir: Path = Path("output"),
    dist_dir: Path = Path("dist"),
    run_id: str | None = None,
    serve: bool = False,
    port: int = 8000,
) -> int:
    """Generate public reader static portal website and optionally serve it."""
    if not output_dir.exists() or not output_dir.is_dir():
        print(f"Error: Output directory not found at {output_dir}", file=sys.stderr)
        return 2

    try:
        from cyberpulse.portal import build_public_portal

        result = build_public_portal(
            output_dir=output_dir,
            dist_dir=dist_dir,
            featured_run_id=run_id,
        )
        index_path = result.dist_dir.resolve() / "index.html"
        print(f"Successfully generated CyberPulse Reader portal at: {index_path}")
        print(f"Editions processed: {result.editions_count} (Featured: {result.featured_run_id})")
        print(f"Created {len(result.files_created)} files in {result.dist_dir}")

        if serve:
            import functools
            import http.server

            handler = functools.partial(
                http.server.SimpleHTTPRequestHandler,
                directory=str(result.dist_dir.resolve()),
            )
            print(f"Trang web độc giả đang chạy tại: http://localhost:{port} (Bấm Ctrl+C để dừng)")
            try:
                with http.server.ThreadingHTTPServer(("", port), handler) as httpd:
                    httpd.serve_forever()
            except KeyboardInterrupt:
                print("\nĐã dừng máy chủ web.")
                return 0
        return 0
    except Exception as err:
        print(f"Error generating public portal: {err}", file=sys.stderr)
        return 1


EXIT_CODE_MAP: dict[FailureReason, int] = {
    FailureReason.insufficient_research: 3,
    FailureReason.llm_error: 5,
    FailureReason.validation_failed: 8,
    FailureReason.research_failed: 11,
    FailureReason.editorial_failed: 11,
    FailureReason.internal_error: 11,
    FailureReason.interrupted: 11,
}


def _print_run_summary(result: RunResult) -> None:
    """Print ASCII-safe summary of run outcome with masked secrets."""
    val_errors = 0
    val_warnings = 0
    val_path = result.artifacts.get("validation")
    if val_path and val_path.exists():
        try:
            data = json.loads(val_path.read_text(encoding="utf-8"))
            val_errors = len(data.get("errors", []))
            val_warnings = len(data.get("warnings", []))
        except Exception:
            pass

    print("=" * 60)
    print("CyberPulse Run Summary")
    print("=" * 60)
    print(f"Run ID: {result.run_id}")
    print(f"Status: {result.status.value}")
    print(f"Failure Reason: {result.failure_reason.value if result.failure_reason else 'None'}")
    if result.error_message:
        print(f"Error: {sanitize_error_message(result.error_message)}")
    print(f"Degraded: {result.degraded}")
    print(f"Rewrite Attempts: {result.rewrite_attempts}")
    print(f"Stop Reason: {result.stop_reason or 'None'}")
    print(f"Step Attempts: {result.step_attempts}")
    print("Artifacts:")
    if result.artifacts:
        for k, p in sorted(result.artifacts.items()):
            print(f"  - {k}: {p}")
    else:
        print("  (None)")
    print(f"Validation: {val_errors} error(s), {val_warnings} warning(s)")
    print("=" * 60)


def run_run(
    settings_path: Path,
    sources_path: Path,
    allow_insufficient: bool = False,
    max_rewrites: int = 2,
    no_llm_review: bool = False,
    run_id: str | None = None,
) -> int:
    """Execute end-to-end cyberpulse pipeline run with cross-process run lock."""
    # 1. Load configuration
    try:
        settings = load_settings(settings_path)
    except ConfigError as err:
        print(f"ConfigError: {err}", file=sys.stderr)
        return 1

    # 2. Validate input options
    if not (0 <= max_rewrites <= 3):
        print(f"Error: --max-rewrites must be between 0 and 3, got {max_rewrites}", file=sys.stderr)
        return 7

    if run_id is not None and not run_id.strip():
        print("Error: --run-id cannot be empty", file=sys.stderr)
        return 7

    # 3. Require API key if using Gemini provider
    if settings.llm.provider == "gemini":
        load_dotenv(override=False)
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key or not api_key.strip():
            print("GEMINI_API_KEY: missing — live LLM check skipped. See README \"LLM setup\".")
            return 4

    # 4. Open/init DB
    db_path = get_db_path(settings)
    conn = connect(db_path)

    # 5. Acquire cross-process lock BEFORE any LLM call
    adopt_token = os.environ.get("CYBERPULSE_LOCK_TOKEN")
    lock = RunLock(output_dir=settings.paths.output_dir, step="run", adopt_token=adopt_token)
    try:
        lock.acquire()
    except RunLockBusyError as err:
        print(f"[PIPELINE BUSY] {err}", file=sys.stderr)
        conn.close()
        return 10

    try:
        init_db(conn)
        setup_logging(
            log_level=settings.log_level,
            log_dir=settings.paths.log_dir,
            logger_name="cyberpulse",
        )

        client = create_llm_client(settings)
        research_agent = ResearchAgent(llm=client, settings=settings)
        editorial_agent = EditorialAgent(llm=client, settings=settings)

        def research_step(r_id: str, candidates: list[Article]) -> ResearchResult:
            return research_agent.run(run_id=r_id, candidates=candidates)

        def editorial_step(research: ResearchResult) -> EditorialPackage:
            return editorial_agent.run(research)

        def rewrite_step(research: ResearchResult, prev: EditorialPackage, feedback: Sequence[Any]) -> EditorialPackage:
            return editorial_agent.rewrite(research, prev, feedback)

        def validate_step(research: ResearchResult, pkg: EditorialPackage) -> Any:
            return validate_package(research, pkg, settings, llm=None)

        final_review_step = None
        if not no_llm_review:
            def final_review_step(research: ResearchResult, pkg: EditorialPackage, report: Any) -> Any:
                return validate_package(research, pkg, settings, llm=client)

        orchestrator = Orchestrator(
            conn=conn,
            settings=settings,
            research_step=research_step,
            editorial_step=editorial_step,
            validate_step=validate_step,
            rewrite_step=rewrite_step,
            max_rewrites=max_rewrites,
            allow_insufficient=allow_insufficient,
            final_review_step=final_review_step,
        )

        result = orchestrator.run(run_id=run_id)
        _print_run_summary(result)

        if result.status == RunStatus.awaiting_review:
            return 0
        if result.failure_reason:
            return EXIT_CODE_MAP.get(result.failure_reason, 11)
        return 11
    except KeyboardInterrupt:
        print("\n[PIPELINE INTERRUPTED] Run cancelled by user.", file=sys.stderr)
        return 11
    except LLMError as err:
        print(f"[LLM ERROR] {err.__class__.__name__}: {err}", file=sys.stderr)
        return 5
    except Exception as err:
        print(f"[ERROR] {err.__class__.__name__}: {err}", file=sys.stderr)
        return 11
    finally:
        conn.close()
        lock.release()


def main(argv: list[str] | None = None) -> int:
    """CLI main entry point."""
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0

    if args.command == "run":
        return run_run(
            settings_path=args.settings,
            sources_path=args.sources,
            allow_insufficient=args.allow_insufficient,
            max_rewrites=args.max_rewrites,
            no_llm_review=args.no_llm_review,
            run_id=args.run_id,
        )
    elif args.command == "version":
        return run_version()
    elif args.command == "check-config":
        return run_check_config(
            settings_path=args.settings,
            sources_path=args.sources,
        )
    elif args.command == "init-db":
        return run_init_db(settings_path=args.settings)
    elif args.command == "fetch":
        return run_fetch(
            settings_path=args.settings,
            sources_path=args.sources,
            limit=args.limit,
        )
    elif args.command == "collect":
        return run_collect(
            settings_path=args.settings,
            sources_path=args.sources,
            dry_run=args.dry_run,
        )
    elif args.command == "llm-check":
        return run_llm_check(settings_path=args.settings)
    elif args.command == "research":
        return run_research(
            settings_path=args.settings,
            sources_path=args.sources,
        )
    elif args.command == "editorial":
        return run_editorial(
            research_path=args.research,
            settings_path=args.settings,
        )
    elif args.command == "validate":
        return run_validate(
            research_path=args.research,
            editorial_path=args.editorial,
            settings_path=args.settings,
            no_llm_review=args.no_llm_review,
        )
    elif args.command == "ui":
        return run_ui(
            port=args.port,
            settings_path=args.settings,
            print_command=args.print_command,
        )
    elif args.command == "media":
        if args.subcommand == "podcast":
            return run_media_podcast(
                run_id=args.run_id,
                settings_path=args.settings,
                voice_args=args.voice,
                pause_ms=args.pause_ms,
                force=args.force,
                allow_unvalidated=args.allow_unvalidated,
            )
    elif args.command == "export":
        if args.subcommand == "bundle":
            return run_export_bundle(
                run_id=args.run_id,
                output_dir=args.output_dir,
                export_dir=args.export_dir,
            )
        elif args.subcommand == "portal":
            return run_export_portal(
                output_dir=args.output_dir,
                dist_dir=args.dist_dir,
                run_id=args.run_id,
                serve=args.serve,
                port=args.port,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
