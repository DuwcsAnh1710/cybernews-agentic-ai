"""RSS/Atom feed fetcher and raw item extraction for CyberPulse AI."""

from __future__ import annotations

import calendar
from datetime import datetime, timezone
import logging
import time
from typing import Any

import feedparser
import httpx
from pydantic import AwareDatetime, BaseModel, Field

from cyberpulse.config import FetchConfig, Source

logger = logging.getLogger("cyberpulse.fetcher")


class RawItem(BaseModel):
    """Raw article item collected directly from an RSS or Atom feed."""

    source_name: str
    title: str | None = None
    link: str | None = None
    published_at: AwareDatetime | None = None
    published_raw: str | None = None
    summary_raw: str = ""
    fetched_at: AwareDatetime


class SourceResult(BaseModel):
    """Outcome and diagnostics of fetching a single source."""

    source_name: str
    url: str
    ok: bool
    item_count: int
    error: str | None = None
    http_status: int | None = None
    elapsed_ms: int


class FetchReport(BaseModel):
    """Aggregated report of fetching across sources."""

    items: list[RawItem] = Field(default_factory=list)
    results: list[SourceResult] = Field(default_factory=list)

    @property
    def ok_sources(self) -> list[SourceResult]:
        """List of sources that were fetched successfully."""
        return [r for r in self.results if r.ok]

    @property
    def failed_sources(self) -> list[SourceResult]:
        """List of sources that failed to fetch."""
        return [r for r in self.results if not r.ok]

    @property
    def all_failed(self) -> bool:
        """True if any sources were attempted and every single one failed."""
        return len(self.results) > 0 and len(self.ok_sources) == 0


def _struct_time_to_utc_datetime(st: Any) -> datetime | None:
    """Convert time.struct_time to timezone-aware UTC datetime."""
    if st is None:
        return None
    try:
        return datetime(
            year=st.tm_year,
            month=st.tm_mon,
            day=st.tm_mday,
            hour=st.tm_hour,
            minute=st.tm_min,
            second=st.tm_sec,
            tzinfo=timezone.utc,
        )
    except Exception:
        try:
            return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)
        except Exception:
            return None


def fetch_source(
    client: httpx.Client,
    source: Source,
    cfg: FetchConfig,
) -> tuple[list[RawItem], SourceResult]:
    """Fetch an individual RSS/Atom feed source with bounded size, retries, and error isolation.

    Never raises for network, HTTP, or feed parsing failures; failures are captured in SourceResult.
    """
    start_time = time.perf_counter()
    max_attempts = 1 + cfg.retries
    http_status: int | None = None
    content: bytes | None = None

    for attempt in range(1, max_attempts + 1):
        is_last_attempt = attempt == max_attempts
        try:
            with client.stream(
                "GET",
                source.url,
                headers={"User-Agent": cfg.user_agent},
                follow_redirects=True,
                timeout=float(cfg.timeout_sec),
            ) as resp:
                http_status = resp.status_code

                # 5xx Server Errors are eligible for retry
                if 500 <= http_status < 600:
                    if not is_last_attempt:
                        continue
                    elapsed_ms = int((time.perf_counter() - start_time) * 1000)
                    error_msg = f"HTTP {http_status}"
                    logger.warning(
                        "Source '%s' (%s) failed with %s after %d attempts",
                        source.name,
                        source.url,
                        error_msg,
                        attempt,
                    )
                    return [], SourceResult(
                        source_name=source.name,
                        url=source.url,
                        ok=False,
                        item_count=0,
                        error=error_msg,
                        http_status=http_status,
                        elapsed_ms=elapsed_ms,
                    )

                # Non-2xx (e.g. 4xx) are NOT retried
                if not (200 <= http_status < 300):
                    elapsed_ms = int((time.perf_counter() - start_time) * 1000)
                    error_msg = f"HTTP {http_status}"
                    logger.warning(
                        "Source '%s' (%s) returned %s",
                        source.name,
                        source.url,
                        error_msg,
                    )
                    return [], SourceResult(
                        source_name=source.name,
                        url=source.url,
                        ok=False,
                        item_count=0,
                        error=error_msg,
                        http_status=http_status,
                        elapsed_ms=elapsed_ms,
                    )

                # Stream response body bounded by max_bytes
                chunks: list[bytes] = []
                total_bytes = 0
                too_large = False
                for chunk in resp.iter_bytes():
                    total_bytes += len(chunk)
                    if total_bytes > cfg.max_bytes:
                        too_large = True
                        break
                    chunks.append(chunk)

                if too_large:
                    elapsed_ms = int((time.perf_counter() - start_time) * 1000)
                    error_msg = "response too large"
                    logger.warning(
                        "Source '%s' (%s) exceeded max_bytes (%d): %s",
                        source.name,
                        source.url,
                        cfg.max_bytes,
                        error_msg,
                    )
                    return [], SourceResult(
                        source_name=source.name,
                        url=source.url,
                        ok=False,
                        item_count=0,
                        error=error_msg,
                        http_status=http_status,
                        elapsed_ms=elapsed_ms,
                    )

                content = b"".join(chunks)
                break

        except httpx.TimeoutException as exc:
            if not is_last_attempt:
                continue
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            error_msg = f"timeout: {exc or 'request timed out'}"
            logger.warning(
                "Source '%s' (%s) timed out after %d attempts: %s",
                source.name,
                source.url,
                attempt,
                exc,
            )
            return [], SourceResult(
                source_name=source.name,
                url=source.url,
                ok=False,
                item_count=0,
                error=error_msg,
                http_status=http_status,
                elapsed_ms=elapsed_ms,
            )
        except (httpx.NetworkError, httpx.TransportError) as exc:
            if not is_last_attempt:
                continue
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            error_msg = f"connection error: {exc}"
            logger.warning(
                "Source '%s' (%s) network error after %d attempts: %s",
                source.name,
                source.url,
                attempt,
                exc,
            )
            return [], SourceResult(
                source_name=source.name,
                url=source.url,
                ok=False,
                item_count=0,
                error=error_msg,
                http_status=http_status,
                elapsed_ms=elapsed_ms,
            )
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            error_msg = f"unexpected error: {exc}"
            logger.error(
                "Source '%s' (%s) failed with unexpected error: %s",
                source.name,
                source.url,
                exc,
            )
            return [], SourceResult(
                source_name=source.name,
                url=source.url,
                ok=False,
                item_count=0,
                error=error_msg,
                http_status=http_status,
                elapsed_ms=elapsed_ms,
            )

    if content is None:
        elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        return [], SourceResult(
            source_name=source.name,
            url=source.url,
            ok=False,
            item_count=0,
            error="no content received",
            http_status=http_status,
            elapsed_ms=elapsed_ms,
        )

    # Parse feed content with feedparser
    parsed = feedparser.parse(content)

    if parsed.bozo and len(parsed.entries) == 0:
        bozo_exc = getattr(parsed, "bozo_exception", None)
        exc_type = type(bozo_exc).__name__ if bozo_exc is not None else "malformed feed"
        elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        error_msg = f"invalid feed: {exc_type}"
        logger.warning(
            "Source '%s' (%s) is invalid feed: %s",
            source.name,
            source.url,
            error_msg,
        )
        return [], SourceResult(
            source_name=source.name,
            url=source.url,
            ok=False,
            item_count=0,
            error=error_msg,
            http_status=http_status,
            elapsed_ms=elapsed_ms,
        )

    if not parsed.version and len(parsed.entries) == 0:
        elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        error_msg = "invalid feed: not a feed"
        logger.warning(
            "Source '%s' (%s) is not a recognizable feed: %s",
            source.name,
            source.url,
            error_msg,
        )
        return [], SourceResult(
            source_name=source.name,
            url=source.url,
            ok=False,
            item_count=0,
            error=error_msg,
            http_status=http_status,
            elapsed_ms=elapsed_ms,
        )

    if parsed.bozo and len(parsed.entries) > 0:
        logger.warning(
            "Source '%s' (%s) parsed with bozo exception: %s",
            source.name,
            source.url,
            getattr(parsed, "bozo_exception", "unknown error"),
        )

    # Extract raw items
    fetched_at = datetime.now(timezone.utc)
    items: list[RawItem] = []

    for entry in parsed.entries:
        raw_title = entry.get("title")
        title = (
            raw_title.strip()
            if raw_title and isinstance(raw_title, str) and raw_title.strip()
            else (raw_title if raw_title and not isinstance(raw_title, str) else None)
        )

        raw_link = entry.get("link")
        link = (
            raw_link.strip()
            if raw_link and isinstance(raw_link, str) and raw_link.strip()
            else (raw_link if raw_link and not isinstance(raw_link, str) else None)
        )

        parsed_time = entry.get("published_parsed") or entry.get("updated_parsed")
        published_at = _struct_time_to_utc_datetime(parsed_time)

        raw_pub = entry.get("published") or entry.get("updated")
        published_raw = (
            raw_pub.strip()
            if raw_pub and isinstance(raw_pub, str) and raw_pub.strip()
            else (raw_pub if raw_pub and not isinstance(raw_pub, str) else None)
        )

        summary_val = entry.get("summary") or ""
        summary_raw = summary_val if isinstance(summary_val, str) else str(summary_val)

        items.append(
            RawItem(
                source_name=source.name,
                title=title,
                link=link,
                published_at=published_at,
                published_raw=published_raw,
                summary_raw=summary_raw,
                fetched_at=fetched_at,
            )
        )

    elapsed_ms = int((time.perf_counter() - start_time) * 1000)
    logger.info(
        "Fetched source '%s': ok=True, items=%d, elapsed=%dms",
        source.name,
        len(items),
        elapsed_ms,
    )
    return items, SourceResult(
        source_name=source.name,
        url=source.url,
        ok=True,
        item_count=len(items),
        error=None,
        http_status=http_status,
        elapsed_ms=elapsed_ms,
    )


def fetch_all(
    sources: list[Source],
    cfg: FetchConfig,
    client: httpx.Client | None = None,
) -> FetchReport:
    """Fetch every enabled source sequentially and aggregate into a FetchReport."""
    enabled_sources = [s for s in sources if s.enabled]
    all_items: list[RawItem] = []
    all_results: list[SourceResult] = []

    if client is not None:
        for source in enabled_sources:
            items, result = fetch_source(client, source, cfg)
            all_items.extend(items)
            all_results.append(result)
    else:
        with httpx.Client() as managed_client:
            for source in enabled_sources:
                items, result = fetch_source(managed_client, source, cfg)
                all_items.extend(items)
                all_results.append(result)

    return FetchReport(items=all_items, results=all_results)

