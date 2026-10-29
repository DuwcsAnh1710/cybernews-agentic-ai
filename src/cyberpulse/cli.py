"""Command line interface for CyberPulse AI."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from cyberpulse import __version__
from cyberpulse.config import ConfigError, check_secrets, load_app_config, load_settings
from cyberpulse.fetcher import RawItem, fetch_all
from cyberpulse.logging_setup import setup_logging
from cyberpulse.storage import connect, get_db_path, init_db


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="cyberpulse",
        description="CyberPulse AI - Automated cybersecurity news intelligence and multimedia pipeline",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

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


def main(argv: list[str] | None = None) -> int:
    """CLI main entry point."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0

    if args.command == "version":
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
