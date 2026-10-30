"""Data helpers and inspection utilities for CyberPulse AI Workbench.

NOTE: This module MUST NOT import Streamlit. It contains pure data extraction,
parsing, querying, and formatting helpers that are independently unit-tested.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import html
import json
import logging
from pathlib import Path
import sqlite3
from typing import Literal

from cyberpulse.models import Article, EditorialPackage, ResearchResult

logger = logging.getLogger("cyberpulse.ui.data")


class ArtifactError(Exception):
    """Raised when an artifact file is missing, invalid, or malformed."""

    def __init__(self, path: Path | str, message: str) -> None:
        super().__init__(f"Error in artifact '{path}': {message}")
        self.path = Path(path)
        self.message = message


@dataclass(frozen=True)
class RunArtifacts:
    """Artifact paths and presence flags for a pipeline run."""

    run_id: str
    research_path: Path | None
    editorial_path: Path | None
    preview_path: Path | None
    validation_path: Path | None
    review_path: Path | None
    mtime: float
    has_research: bool
    has_editorial: bool
    has_validation: bool
    has_review: bool


@dataclass(frozen=True)
class Block:
    """Presentation block linked to source article citations."""

    path: str
    kind: str
    text: str
    cited_ids: tuple[str, ...]


CAPTION_LIMITS: dict[str, int] = {
    "facebook": 600,
    "youtube": 900,
    "tiktok": 250,
    "generic": 400,
}


def list_runs(output_dir: Path | str) -> list[RunArtifacts]:
    """Return union of run_ids found in research, editorial, validation, review directories.

    Sorted newest first by the latest artifact mtime.
    """
    out_path = Path(output_dir)
    if not out_path.exists():
        return []

    research_dir = out_path / "research"
    editorial_dir = out_path / "editorial"
    validation_dir = out_path / "validation"
    review_dir = out_path / "review"

    run_ids: set[str] = set()

    for d in (research_dir, editorial_dir, validation_dir, review_dir):
        if d.exists() and d.is_dir():
            for f in d.glob("*.json"):
                # Ignore .original.json backup files
                if f.name.endswith(".original.json"):
                    continue
                run_ids.add(f.stem)

    runs: list[RunArtifacts] = []
    for rid in run_ids:
        r_file = research_dir / f"{rid}.json"
        e_file = editorial_dir / f"{rid}.json"
        p_file = editorial_dir / f"{rid}.md"
        v_file = validation_dir / f"{rid}.json"
        rev_file = review_dir / f"{rid}.json"

        r_path = r_file if r_file.exists() else None
        e_path = e_file if e_file.exists() else None
        p_path = p_file if p_file.exists() else None
        v_path = v_file if v_file.exists() else None
        rev_path = rev_file if rev_file.exists() else None

        existing_files = [p for p in (r_path, e_path, p_path, v_path, rev_path) if p is not None]
        mtime = max((p.stat().st_mtime for p in existing_files), default=0.0)

        runs.append(
            RunArtifacts(
                run_id=rid,
                research_path=r_path,
                editorial_path=e_path,
                preview_path=p_path,
                validation_path=v_path,
                review_path=rev_path,
                mtime=mtime,
                has_research=r_path is not None,
                has_editorial=e_path is not None,
                has_validation=v_path is not None,
                has_review=rev_path is not None,
            )
        )

    # Newest first
    runs.sort(key=lambda r: r.mtime, reverse=True)
    return runs


def load_research(path: Path | str) -> ResearchResult:
    """Load ResearchResult from path. Raises ArtifactError on failure."""
    p = Path(path)
    if not p.exists():
        raise ArtifactError(p, "File not found")
    try:
        content = p.read_text(encoding="utf-8")
        return ResearchResult.model_validate_json(content)
    except Exception as err:
        raise ArtifactError(p, str(err)) from err


def load_editorial(path: Path | str) -> EditorialPackage:
    """Load EditorialPackage from path. Raises ArtifactError on failure."""
    p = Path(path)
    if not p.exists():
        raise ArtifactError(p, "File not found")
    try:
        content = p.read_text(encoding="utf-8")
        return EditorialPackage.model_validate_json(content)
    except Exception as err:
        raise ArtifactError(p, str(err)) from err


def load_validation(path: Path | str | None) -> dict | None:
    """Load validation report as raw dictionary.

    Returns None if path is None or does not exist. Raises ArtifactError on parse error.
    """
    if path is None:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        content = p.read_text(encoding="utf-8")
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("Validation file root must be a JSON object")
        return data
    except Exception as err:
        raise ArtifactError(p, str(err)) from err


def parse_research_notes(notes: str) -> dict[str, str]:
    """Parse ';'-separated key=value string in research notes into a dictionary."""
    if not notes or not notes.strip():
        return {}
    result: dict[str, str] = {}
    parts = notes.split(";")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            key, val = part.split("=", 1)
            result[key.strip()] = val.strip()
        else:
            result[part] = ""
    return result


def source_cap_view(research: ResearchResult, max_per_source: int) -> list[dict]:
    """Calculate selected count and cap status per source."""
    counts: dict[str, int] = {}
    for item in research.items:
        sname = item.article.source_name
        counts[sname] = counts.get(sname, 0) + 1

    rows: list[dict] = []
    for sname in sorted(counts.keys()):
        count = counts[sname]
        rows.append({
            "source_name": sname,
            "selected_count": count,
            "cap": max_per_source,
            "at_cap": count >= max_per_source,
        })
    return rows


def query_articles(
    db_path: Path | str,
    *,
    sources: list[str] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    date_field: Literal["published_at", "fetched_at"] = "published_at",
    keyword: str | None = None,
    limit: int = 500,
) -> list[Article]:
    """Query articles from SQLite using read-only connection mode=ro.

    Returns [] without error if DB file does not exist.
    """
    p = Path(db_path)
    if not p.exists():
        return []

    # Use URI format for strictly read-only mode
    uri = f"file:{p.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row

    try:
        clauses: list[str] = []
        params: list[object] = []

        if sources:
            placeholders = ",".join("?" for _ in sources)
            clauses.append(f"source_name IN ({placeholders})")
            params.extend(sources)

        date_col = "published_at" if date_field == "published_at" else "fetched_at"

        if since is not None:
            since_utc = since.astimezone(timezone.utc).isoformat()
            clauses.append(f"{date_col} >= ?")
            params.append(since_utc)

        if until is not None:
            until_utc = until.astimezone(timezone.utc).isoformat()
            clauses.append(f"{date_col} <= ?")
            params.append(until_utc)

        if keyword and keyword.strip():
            kw = keyword.strip().lower()
            clauses.append("(LOWER(title) LIKE ? OR LOWER(summary) LIKE ?)")
            params.extend([f"%{kw}%", f"%{kw}%"])

        where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        query = (
            f"SELECT id, source_name, title, url, published_at, summary, fetched_at, topics_json "
            f"FROM articles {where_sql} "
            f"ORDER BY {date_col} DESC LIMIT ?"
        )
        params.append(limit)

        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()

        articles: list[Article] = []
        for row in rows:
            topics_raw = row["topics_json"]
            topics: list[str] = []
            if topics_raw:
                try:
                    topics = json.loads(topics_raw)
                except Exception:
                    topics = [t.strip() for t in topics_raw.split(",") if t.strip()]

            articles.append(
                Article(
                    id=row["id"],
                    source_name=row["source_name"],
                    title=row["title"],
                    url=row["url"],
                    published_at=datetime.fromisoformat(row["published_at"]),
                    summary=row["summary"] or "",
                    fetched_at=datetime.fromisoformat(row["fetched_at"]),
                    topics_matched=topics,
                )
            )
        return articles
    finally:
        conn.close()


def unselected_candidates(
    db_path: Path | str,
    research: ResearchResult,
    lookback_hours: int,
    now: datetime | None = None,
) -> list[Article]:
    """Return candidate articles in lookback window not selected in this run."""
    ref_time = now if now is not None else datetime.now(timezone.utc)
    since = ref_time - timedelta(hours=lookback_hours)
    all_candidates = query_articles(db_path, since=since, limit=1000)

    selected_ids = {item.article.id for item in research.items}
    return [a for a in all_candidates if a.id not in selected_ids]


def caption_stats(pkg: EditorialPackage) -> list[dict]:
    """Calculate platform character count, limit, hashtag count, and over-limit status."""
    stats: list[dict] = []
    for cap in pkg.captions:
        limit = CAPTION_LIMITS.get(cap.platform, 400)
        char_count = len(cap.text)
        stats.append({
            "platform": cap.platform,
            "chars": char_count,
            "limit": limit,
            "hashtag_count": len(cap.hashtags),
            "over_limit": char_count > limit,
        })
    return stats


def podcast_stats(
    pkg: EditorialPackage,
    target_words: int,
    tolerance: float = 0.2,
) -> dict:
    """Calculate word counts per speaker, total words, allowed range, and compliance."""
    words_by_speaker: dict[str, int] = {}
    for seg in pkg.podcast.segments:
        count = len(seg.line.text.split())
        words_by_speaker[seg.speaker] = words_by_speaker.get(seg.speaker, 0) + count

    total = sum(words_by_speaker.values())
    min_allowed = int(target_words * (1.0 - tolerance))
    max_allowed = int(target_words * (1.0 + tolerance))
    in_range = min_allowed <= total <= max_allowed

    return {
        "words_by_speaker": words_by_speaker,
        "total_words": total,
        "target_words": target_words,
        "min_allowed": min_allowed,
        "max_allowed": max_allowed,
        "in_range": in_range,
    }


def video_storyboard(
    pkg: EditorialPackage,
    max_total_sec: float,
) -> tuple[list[dict], float, bool]:
    """Return scene storyboard rows, total seconds, and budget compliance."""
    rows: list[dict] = []
    for sc in pkg.video.scenes:
        rows.append({
            "scene_no": sc.scene_no,
            "duration_sec": sc.duration_sec,
            "on_screen_text": sc.on_screen_text,
            "char_count": len(sc.on_screen_text),
            "narration": sc.narration.text,
        })

    total_duration = sum(sc.duration_sec for sc in pkg.video.scenes)
    in_budget = total_duration <= max_total_sec
    return rows, total_duration, in_budget


def blocks_by_article(
    research: ResearchResult,
    pkg: EditorialPackage,
) -> dict[str, list[Block]]:
    """Map each article_id to list of blocks citing that article."""
    mapping: dict[str, list[Block]] = {item.article.id: [] for item in research.items}

    # Digest sections
    for i, sec in enumerate(pkg.digest.sections):
        b = Block(
            path=f"digest.sections[{i}].body.text",
            kind="digest_section",
            text=sec.body.text,
            cited_ids=tuple(sec.body.cited_article_ids),
        )
        for aid in sec.body.cited_article_ids:
            if aid in mapping:
                mapping[aid].append(b)

    # Podcast segments
    for i, seg in enumerate(pkg.podcast.segments):
        b = Block(
            path=f"podcast.segments[{i}].line.text",
            kind="podcast_segment",
            text=seg.line.text,
            cited_ids=tuple(seg.line.cited_article_ids),
        )
        for aid in seg.line.cited_article_ids:
            if aid in mapping:
                mapping[aid].append(b)

    # Video scenes
    for i, sc in enumerate(pkg.video.scenes):
        b_narration = Block(
            path=f"video.scenes[{i}].narration.text",
            kind="video_narration",
            text=sc.narration.text,
            cited_ids=tuple(sc.narration.cited_article_ids),
        )
        for aid in sc.narration.cited_article_ids:
            if aid in mapping:
                mapping[aid].append(b_narration)

        b_onscreen = Block(
            path=f"video.scenes[{i}].on_screen_text",
            kind="video_on_screen",
            text=sc.on_screen_text,
            cited_ids=tuple(sc.narration.cited_article_ids),
        )
        for aid in sc.narration.cited_article_ids:
            if aid in mapping:
                mapping[aid].append(b_onscreen)

    # Captions
    for i, cap in enumerate(pkg.captions):
        b = Block(
            path=f"captions[{i}].text",
            kind="caption",
            text=cap.text,
            cited_ids=tuple(cap.cited_article_ids),
        )
        for aid in cap.cited_article_ids:
            if aid in mapping:
                mapping[aid].append(b)

    return mapping


def uncited_blocks(pkg: EditorialPackage) -> list[Block]:
    """Return blocks that do not cite specific individual articles."""
    blocks: list[Block] = [
        Block(path="digest.title", kind="digest_title", text=pkg.digest.title, cited_ids=()),
        Block(path="digest.intro", kind="digest_intro", text=pkg.digest.intro, cited_ids=()),
        Block(path="podcast.title", kind="podcast_title", text=pkg.podcast.title, cited_ids=()),
        Block(path="video.title", kind="video_title", text=pkg.video.title, cited_ids=()),
    ]
    return blocks


def issues_by_path(validation: dict | None) -> dict[str, list[dict]]:
    """Group validation issues (errors and warnings) by path."""
    if not validation:
        return {}

    by_path: dict[str, list[dict]] = {}
    for err in validation.get("errors", []):
        p = err.get("path", "")
        by_path.setdefault(p, []).append(err)

    for warn in validation.get("warnings", []):
        p = warn.get("path", "")
        by_path.setdefault(p, []).append(warn)

    return by_path


def highlight(text: str, evidences: list[str]) -> str:
    """Return HTML-escaped text with evidence substrings highlighted via <mark>.

    Both text and evidence substrings are escaped first; only fixed <mark> tags are added.
    """
    escaped_text = html.escape(text)
    for ev in evidences:
        if not ev or not ev.strip():
            continue
        escaped_ev = html.escape(ev.strip())
        if escaped_ev in escaped_text:
            escaped_text = escaped_text.replace(escaped_ev, f"<mark>{escaped_ev}</mark>")
    return escaped_text


def validation_freshness(
    editorial_path: Path | str | None,
    validation_path: Path | str | None,
) -> Literal["missing", "stale", "fresh"]:
    """Compute validation freshness status relative to editorial package file mtime."""
    if validation_path is None:
        return "missing"
    v_path = Path(validation_path)
    if not v_path.exists():
        return "missing"

    if editorial_path is None:
        return "fresh"
    e_path = Path(editorial_path)
    if not e_path.exists():
        return "fresh"

    try:
        e_mtime = e_path.stat().st_mtime
        v_mtime = v_path.stat().st_mtime
        if v_mtime < e_mtime:
            return "stale"
        return "fresh"
    except Exception:
        return "missing"
