"""Normalization, URL canonicalization, topic matching, and deduplication for CyberPulse AI."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import difflib
from enum import Enum
import html
from html.parser import HTMLParser
import re
import urllib.parse

from pydantic import BaseModel, Field

from cyberpulse.config import Settings
from cyberpulse.fetcher import RawItem
from cyberpulse.models import Article, make_article_id

TRACKING_QUERY_PARAMS: frozenset[str] = frozenset(
    {
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "ref",
        "ref_src",
        "cmpid",
        "_ga",
    }
)


def canonicalize_url(url: str) -> str | None:
    """Canonicalize an HTTP/HTTPS URL.

    - Strips whitespace; returns None if scheme is not http/https or host is empty.
    - Lowercases scheme and host; drops leading 'www.'; drops default ports (:80, :443); drops fragment.
    - Strips tracking query params (starts with 'utm_' or in TRACKING_QUERY_PARAMS).
    - Retains other query params, sorted by parameter name.
    - Strips trailing '/' from path unless path is exactly '/'.
    - Preserves scheme as given (no http -> https conversion).
    """
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url:
        return None

    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception:
        return None

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        return None

    if not parsed.hostname:
        return None

    host = parsed.hostname.lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return None

    port = parsed.port
    if port is not None:
        if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
            netloc = host
        else:
            netloc = f"{host}:{port}"
    else:
        netloc = host

    path = parsed.path
    if path.endswith("/") and path != "/":
        path = path.rstrip("/")

    query_params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    filtered_params = [
        (k, v)
        for k, v in query_params
        if not (k.lower().startswith("utm_") or k.lower() in TRACKING_QUERY_PARAMS)
    ]
    filtered_params.sort(key=lambda item: (item[0], item[1]))
    query = urllib.parse.urlencode(filtered_params) if filtered_params else ""

    return urllib.parse.urlunsplit((scheme, netloc, path, query, ""))


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.reset()
        self.parts: list[str] = []

    def handle_data(self, d: str) -> None:
        self.parts.append(d)

    def get_data(self) -> str:
        return " ".join(self.parts)


def clean_text(text: str, max_chars: int) -> str:
    """Strip HTML tags, unescape entities, collapse whitespace, and bound length.

    If longer than max_chars, cuts at the last space before the limit and appends '…'.
    """
    if not text:
        return ""

    try:
        stripper = _HTMLStripper()
        stripper.feed(text)
        cleaned = stripper.get_data()
    except Exception:
        cleaned = re.sub(r"<[^>]+>", " ", text)

    cleaned = html.unescape(cleaned)
    cleaned = " ".join(cleaned.split())

    if len(cleaned) <= max_chars:
        return cleaned

    candidate = cleaned[:max_chars]
    if len(cleaned) > max_chars and cleaned[max_chars] == " ":
        return candidate.rstrip() + "…"

    last_space = candidate.rfind(" ")
    if last_space != -1:
        return candidate[:last_space].rstrip() + "…"
    return candidate + "…"


def normalize_title_key(title: str) -> str:
    """Lowercase, unescape, remove punctuation, collapse whitespace. Used only for comparison."""
    if not title:
        return ""
    text = html.unescape(title).lower()
    text = re.sub(r"[^\w\s]|_", " ", text)
    return " ".join(text.split())


def match_topics(title: str, summary: str, topics: list[str]) -> list[str]:
    """Tag title + summary with case-insensitive whole-phrase matches of configured topics.

    Returns matched topics preserving config order. Tagging only; does not drop articles.
    """
    combined = f"{title} {summary}"
    matched: list[str] = []
    for topic in topics:
        if not topic or not topic.strip():
            continue
        cleaned_topic = topic.strip()
        pattern = rf"\b{re.escape(cleaned_topic)}\b"
        if re.search(pattern, combined, re.IGNORECASE):
            matched.append(cleaned_topic)
    return matched


class DropReason(str, Enum):
    """Reason for dropping a raw item from inclusion as an Article."""

    missing_link = "missing_link"
    invalid_url = "invalid_url"
    missing_title = "missing_title"
    missing_date = "missing_date"
    too_old = "too_old"
    future_date = "future_date"
    duplicate_url = "duplicate_url"
    duplicate_title = "duplicate_title"


class DroppedItem(BaseModel):
    """Audit entry for an item that was dropped during normalization."""

    source_name: str
    title: str | None = None
    link: str | None = None
    reason: DropReason
    detail: str = ""
    duplicate_of: str | None = None


class NormalizeReport(BaseModel):
    """Aggregated normalization and deduplication result."""

    articles: list[Article] = Field(default_factory=list)
    dropped: list[DroppedItem] = Field(default_factory=list)
    input_count: int = 0

    @property
    def counts_by_reason(self) -> dict[str, int]:
        """Distribution of dropped items by drop reason."""
        counts: dict[str, int] = {r.value: 0 for r in DropReason}
        for item in self.dropped:
            counts[item.reason.value] = counts.get(item.reason.value, 0) + 1
        return counts


class _Candidate:
    __slots__ = (
        "raw",
        "canonical_url",
        "clean_title",
        "title_key",
        "published_at",
        "input_order",
    )

    def __init__(
        self,
        raw: RawItem,
        canonical_url: str,
        clean_title: str,
        title_key: str,
        published_at: datetime,
        input_order: int,
    ) -> None:
        self.raw = raw
        self.canonical_url = canonical_url
        self.clean_title = clean_title
        self.title_key = title_key
        self.published_at = published_at
        self.input_order = input_order


def normalize_items(
    raw_items: list[RawItem],
    settings: Settings,
    now: datetime | None = None,
) -> NormalizeReport:
    """Normalize, filter, and deduplicate raw items into Article models."""
    now_utc = (
        now.astimezone(timezone.utc)
        if now is not None
        else datetime.now(timezone.utc)
    )
    too_old_cutoff = now_utc - timedelta(hours=settings.lookback_hours)
    future_cutoff = now_utc + timedelta(
        minutes=settings.normalize.future_tolerance_minutes
    )

    dropped: list[DroppedItem] = []
    candidates: list[_Candidate] = []

    for idx, raw in enumerate(raw_items):
        # 1. link None/empty -> missing_link; canonicalize_url is None -> invalid_url
        if not raw.link or not raw.link.strip():
            dropped.append(
                DroppedItem(
                    source_name=raw.source_name,
                    title=raw.title,
                    link=raw.link,
                    reason=DropReason.missing_link,
                )
            )
            continue

        canonical_url = canonicalize_url(raw.link)
        if canonical_url is None:
            dropped.append(
                DroppedItem(
                    source_name=raw.source_name,
                    title=raw.title,
                    link=raw.link,
                    reason=DropReason.invalid_url,
                )
            )
            continue

        # 2. title None/empty after clean_text -> missing_title
        cleaned_title = clean_text(raw.title or "", 300)
        if not cleaned_title:
            dropped.append(
                DroppedItem(
                    source_name=raw.source_name,
                    title=raw.title,
                    link=raw.link,
                    reason=DropReason.missing_title,
                )
            )
            continue

        # 3. published_at None -> missing_date
        if raw.published_at is None:
            dropped.append(
                DroppedItem(
                    source_name=raw.source_name,
                    title=raw.title,
                    link=raw.link,
                    reason=DropReason.missing_date,
                )
            )
            continue

        # 4. too_old / future_date
        pub = (
            raw.published_at.astimezone(timezone.utc)
            if raw.published_at.tzinfo
            else raw.published_at.replace(tzinfo=timezone.utc)
        )
        if pub < too_old_cutoff:
            dropped.append(
                DroppedItem(
                    source_name=raw.source_name,
                    title=raw.title,
                    link=raw.link,
                    reason=DropReason.too_old,
                )
            )
            continue

        if pub > future_cutoff:
            dropped.append(
                DroppedItem(
                    source_name=raw.source_name,
                    title=raw.title,
                    link=raw.link,
                    reason=DropReason.future_date,
                )
            )
            continue

        candidates.append(
            _Candidate(
                raw=raw,
                canonical_url=canonical_url,
                clean_title=cleaned_title,
                title_key=normalize_title_key(cleaned_title),
                published_at=pub,
                input_order=idx,
            )
        )

    # Dedup winner rule: sort candidates by published_at ascending (ties: original input order)
    candidates.sort(key=lambda c: (c.published_at, c.input_order))

    kept_articles: list[Article] = []
    kept_title_keys: list[str] = []

    for candidate in candidates:
        # 5. Exact duplicate URL
        matching_url_kept = next(
            (a for a in kept_articles if a.url == candidate.canonical_url), None
        )
        if matching_url_kept is not None:
            dropped.append(
                DroppedItem(
                    source_name=candidate.raw.source_name,
                    title=candidate.clean_title,
                    link=candidate.canonical_url,
                    reason=DropReason.duplicate_url,
                    duplicate_of=matching_url_kept.id,
                )
            )
            continue

        # 6. Near-duplicate title
        duplicate_title_kept: Article | None = None
        matched_ratio = 0.0
        for kept_art, kept_key in zip(kept_articles, kept_title_keys):
            ratio = difflib.SequenceMatcher(
                None, candidate.title_key, kept_key
            ).ratio()
            if ratio >= settings.normalize.title_similarity:
                duplicate_title_kept = kept_art
                matched_ratio = ratio
                break

        if duplicate_title_kept is not None:
            dropped.append(
                DroppedItem(
                    source_name=candidate.raw.source_name,
                    title=candidate.clean_title,
                    link=candidate.canonical_url,
                    reason=DropReason.duplicate_title,
                    detail=f"{matched_ratio:.3f}",
                    duplicate_of=duplicate_title_kept.id,
                )
            )
            continue

        # Kept article creation
        article_id = make_article_id(candidate.canonical_url)
        summary = clean_text(
            candidate.raw.summary_raw, settings.normalize.summary_max_chars
        )
        topics_matched = match_topics(
            candidate.clean_title, summary, settings.topics
        )
        article = Article(
            id=article_id,
            source_name=candidate.raw.source_name,
            title=candidate.clean_title,
            url=candidate.canonical_url,
            published_at=candidate.published_at,
            summary=summary,
            fetched_at=candidate.raw.fetched_at,
            topics_matched=topics_matched,
        )
        kept_articles.append(article)
        kept_title_keys.append(candidate.title_key)

    # Kept articles sorted by published_at descending
    kept_articles.sort(key=lambda a: a.published_at, reverse=True)

    return NormalizeReport(
        articles=kept_articles,
        dropped=dropped,
        input_count=len(raw_items),
    )