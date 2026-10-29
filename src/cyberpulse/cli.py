"""Command line interface for CyberPulse AI."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from cyberpulse import __version__
from cyberpulse.config import ConfigError, check_secrets, load_app_config
from cyberpulse.logging_setup import setup_logging


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
    return 0


if __name__ == "__main__":
    sys.exit(main())
