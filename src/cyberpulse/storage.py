"""SQLite storage layer for CyberPulse AI."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any
import uuid

from cyberpulse.config import Settings
from cyberpulse.models import (
    Article,
    EditorialPackage,
    ResearchResult,
    RunStatus,
    StepLog,
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS articles (
    id TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT UNIQUE NOT NULL,
    published_at TEXT NOT NULL,
    summary TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    topics_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_articles (
    run_id TEXT NOT NULL,
    article_id TEXT NOT NULL,
    rank INTEGER NOT NULL,
    relevance_reason TEXT NOT NULL,
    PRIMARY KEY (run_id, article_id),
    UNIQUE (run_id, rank),
    FOREIGN KEY (run_id) REFERENCES runs (id) ON DELETE CASCADE,
    FOREIGN KEY (article_id) REFERENCES articles (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS contents (
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, kind),
    FOREIGN KEY (run_id) REFERENCES runs (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS step_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    step TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs (id) ON DELETE CASCADE
);
"""


def get_db_path(settings: Settings) -> Path:
    """Return default database path from settings."""
    return settings.paths.data_dir / "cyberpulse.db"


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Create a SQLite connection, ensure directory exists, enable foreign keys, and set row_factory."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Initialize database schema idempotently and set pragma user_version to 1."""
    with conn:
        conn.executescript(SCHEMA_SQL)
        conn.execute("PRAGMA user_version = 1")


def create_run(conn: sqlite3.Connection, run_id: str | None = None) -> str:
    """Create a new run entry with status 'pending'."""
    actual_run_id = run_id if run_id is not None else uuid.uuid4().hex
    now_str = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute(
            """
            INSERT INTO runs (id, status, created_at, updated_at, error)
            VALUES (?, ?, ?, ?, ?)
            """,
            (actual_run_id, RunStatus.pending.value, now_str, now_str, None),
        )
    return actual_run_id


def update_run_status(
    conn: sqlite3.Connection,
    run_id: str,
    status: RunStatus | str,
    error: str | None = None,
) -> None:
    """Update status and updated_at timestamp of a run. Raises KeyError if run_id not found."""
    status_str = status.value if isinstance(status, RunStatus) else str(status)
    now_str = datetime.now(timezone.utc).isoformat()
    with conn:
        cursor = conn.execute(
            """
            UPDATE runs
            SET status = ?, updated_at = ?, error = ?
            WHERE id = ?
            """,
            (status_str, now_str, error, run_id),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"Run '{run_id}' not found")


def get_run(conn: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    """Retrieve run record by id, returning dict or None."""
    cursor = conn.execute(
        """
        SELECT id, status, created_at, updated_at, error
        FROM runs
        WHERE id = ?
        """,
        (run_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return dict(row)


def _upsert_article_stmt(conn: sqlite3.Connection, article: Article) -> None:
    topics_json = json.dumps(article.topics_matched)
    published_at_str = article.published_at.astimezone(timezone.utc).isoformat()
    fetched_at_str = article.fetched_at.astimezone(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO articles (id, source_name, title, url, published_at, summary, fetched_at, topics_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            source_name = excluded.source_name,
            title = excluded.title,
            url = excluded.url,
            published_at = excluded.published_at,
            summary = excluded.summary,
            fetched_at = excluded.fetched_at,
            topics_json = excluded.topics_json
        """,
        (
            article.id,
            article.source_name,
            article.title,
            article.url,
            published_at_str,
            article.summary,
            fetched_at_str,
            topics_json,
        ),
    )


def upsert_article(conn: sqlite3.Connection, article: Article) -> None:
    """Insert or update an article by id."""
    with conn:
        _upsert_article_stmt(conn, article)


def save_research_result(conn: sqlite3.Connection, result: ResearchResult) -> None:
    """Upsert articles, write run_articles rows, and save full result to contents in a single transaction."""
    now_str = datetime.now(timezone.utc).isoformat()
    body_json = result.model_dump_json()

    with conn:
        # Upsert all constituent articles
        for item in result.items:
            _upsert_article_stmt(conn, item.article)

        # Replace existing run_articles for this run
        conn.execute("DELETE FROM run_articles WHERE run_id = ?", (result.run_id,))
        for item in result.items:
            conn.execute(
                """
                INSERT INTO run_articles (run_id, article_id, rank, relevance_reason)
                VALUES (?, ?, ?, ?)
                """,
                (result.run_id, item.article.id, item.rank, item.relevance_reason),
            )

        # Upsert full JSON in contents
        conn.execute(
            """
            INSERT INTO contents (run_id, kind, body_json, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(run_id, kind) DO UPDATE SET
                body_json = excluded.body_json,
                created_at = excluded.created_at
            """,
            (result.run_id, "research_result", body_json, now_str),
        )


def load_research_result(conn: sqlite3.Connection, run_id: str) -> ResearchResult | None:
    """Load research result from contents table by run_id."""
    cursor = conn.execute(
        """
        SELECT body_json
        FROM contents
        WHERE run_id = ? AND kind = ?
        """,
        (run_id, "research_result"),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return ResearchResult.model_validate_json(row["body_json"])


def save_editorial_package(conn: sqlite3.Connection, pkg: EditorialPackage) -> None:
    """Save full editorial package into contents table."""
    now_str = datetime.now(timezone.utc).isoformat()
    body_json = pkg.model_dump_json()
    with conn:
        conn.execute(
            """
            INSERT INTO contents (run_id, kind, body_json, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(run_id, kind) DO UPDATE SET
                body_json = excluded.body_json,
                created_at = excluded.created_at
            """,
            (pkg.run_id, "editorial_package", body_json, now_str),
        )


def load_editorial_package(conn: sqlite3.Connection, run_id: str) -> EditorialPackage | None:
    """Load and deserialize editorial package from contents table."""
    cursor = conn.execute(
        """
        SELECT body_json
        FROM contents
        WHERE run_id = ? AND kind = ?
        """,
        (run_id, "editorial_package"),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return EditorialPackage.model_validate_json(row["body_json"])


def add_step_log(conn: sqlite3.Connection, log: StepLog) -> None:
    """Append a step log entry."""
    created_at_str = log.created_at.astimezone(timezone.utc).isoformat()
    with conn:
        conn.execute(
            """
            INSERT INTO step_logs (run_id, step, status, attempt, message, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (log.run_id, log.step, log.status, log.attempt, log.message, created_at_str),
        )


def list_step_logs(conn: sqlite3.Connection, run_id: str) -> list[StepLog]:
    """List step logs for a run ordered by id ASC."""
    cursor = conn.execute(
        """
        SELECT run_id, step, status, attempt, message, created_at
        FROM step_logs
        WHERE run_id = ?
        ORDER BY id ASC
        """,
        (run_id,),
    )
    rows = cursor.fetchall()
    return [
        StepLog(
            run_id=row["run_id"],
            step=row["step"],
            status=row["status"],
            attempt=row["attempt"],
            message=row["message"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
        for row in rows
    ]


def list_recent_articles(
    conn: sqlite3.Connection,
    since: datetime,
    limit: int,
) -> list[Article]:
    """Retrieve articles published on or after `since`, ordered by published_at DESC, up to `limit`."""
    since_utc = since if since.tzinfo is not None else since.replace(tzinfo=timezone.utc)
    since_str = since_utc.astimezone(timezone.utc).isoformat()
    cursor = conn.execute(
        """
        SELECT id, source_name, title, url, published_at, summary, fetched_at, topics_json
        FROM articles
        WHERE published_at >= ?
        ORDER BY published_at DESC
        LIMIT ?
        """,
        (since_str, limit),
    )
    rows = cursor.fetchall()
    return [
        Article(
            id=row["id"],
            source_name=row["source_name"],
            title=row["title"],
            url=row["url"],
            published_at=datetime.fromisoformat(row["published_at"]),
            summary=row["summary"],
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
            topics_matched=json.loads(row["topics_json"]),
        )
        for row in rows
    ]

