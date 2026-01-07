"""SQLite database for news items and digests."""
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from contextlib import contextmanager
import json

from ..config import config


SCHEMA = """
CREATE TABLE IF NOT EXISTS news_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_name TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT UNIQUE,
    summary TEXT,
    content TEXT,
    author TEXT,
    published_at TIMESTAMP,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    relevance_score REAL DEFAULT 0.0,
    used_in_digest INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS digests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    digest_date DATE UNIQUE,
    content TEXT NOT NULL,
    news_item_ids TEXT,  -- JSON array of IDs used
    model_version TEXT,
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_news_published ON news_items(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_used ON news_items(used_in_digest);
CREATE INDEX IF NOT EXISTS idx_digest_date ON digests(digest_date DESC);
"""


@contextmanager
def get_connection():
    """Context manager for database connections."""
    conn = sqlite3.connect(str(config.db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Initialize the database schema."""
    with get_connection() as conn:
        conn.executescript(SCHEMA)


def add_news_item(
    feed_name: str,
    title: str,
    url: str,
    summary: Optional[str] = None,
    content: Optional[str] = None,
    author: Optional[str] = None,
    published_at: Optional[datetime] = None,
    relevance_score: float = 0.0
) -> Optional[int]:
    """
    Add a news item to the database.

    Returns the item ID or None if it already exists.
    """
    with get_connection() as conn:
        try:
            cursor = conn.execute("""
                INSERT INTO news_items
                    (feed_name, title, url, summary, content, author, published_at, relevance_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (feed_name, title, url, summary, content, author, published_at, relevance_score))
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            # URL already exists
            return None


def get_unused_news(limit: int = 10, max_age_days: int = 7) -> list[dict]:
    """
    Get news items that haven't been used in a digest yet.

    Returns items sorted by relevance_score and recency.
    """
    cutoff = datetime.now() - timedelta(days=max_age_days)

    with get_connection() as conn:
        rows = conn.execute("""
            SELECT id, feed_name, title, url, summary, content, author,
                   published_at, relevance_score
            FROM news_items
            WHERE used_in_digest = 0
              AND (published_at IS NULL OR published_at > ?)
            ORDER BY relevance_score DESC, published_at DESC
            LIMIT ?
        """, (cutoff, limit)).fetchall()

        return [dict(row) for row in rows]


def mark_news_used(item_ids: list[int]):
    """Mark news items as used in a digest."""
    with get_connection() as conn:
        placeholders = ','.join('?' * len(item_ids))
        conn.execute(f"""
            UPDATE news_items
            SET used_in_digest = 1
            WHERE id IN ({placeholders})
        """, item_ids)


def save_digest(
    digest_date: datetime,
    content: str,
    news_item_ids: list[int],
    model_version: str
) -> int:
    """
    Save a generated digest.

    Returns the digest ID.
    """
    with get_connection() as conn:
        cursor = conn.execute("""
            INSERT OR REPLACE INTO digests
                (digest_date, content, news_item_ids, model_version)
            VALUES (?, ?, ?, ?)
        """, (
            digest_date.date(),
            content,
            json.dumps(news_item_ids),
            model_version
        ))
        return cursor.lastrowid


def get_latest_digest() -> Optional[dict]:
    """Get the most recent digest."""
    with get_connection() as conn:
        row = conn.execute("""
            SELECT id, digest_date, content, news_item_ids, model_version, generated_at
            FROM digests
            ORDER BY digest_date DESC
            LIMIT 1
        """).fetchone()

        if row:
            result = dict(row)
            result['news_item_ids'] = json.loads(result['news_item_ids'] or '[]')
            return result
        return None


def get_digest_by_date(date_str: str) -> Optional[dict]:
    """Get digest for a specific date (YYYY-MM-DD format)."""
    with get_connection() as conn:
        row = conn.execute("""
            SELECT id, digest_date, content, news_item_ids, model_version, generated_at
            FROM digests
            WHERE digest_date = ?
        """, (date_str,)).fetchone()

        if row:
            result = dict(row)
            result['news_item_ids'] = json.loads(result['news_item_ids'] or '[]')
            return result
        return None


def list_digest_dates(limit: int = 30) -> list[str]:
    """List available digest dates (most recent first)."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT digest_date
            FROM digests
            ORDER BY digest_date DESC
            LIMIT ?
        """, (limit,)).fetchall()

        return [row['digest_date'] for row in rows]


def cleanup_old_news(days: int = 30):
    """Delete news items older than specified days."""
    cutoff = datetime.now() - timedelta(days=days)

    with get_connection() as conn:
        cursor = conn.execute("""
            DELETE FROM news_items
            WHERE published_at < ?
        """, (cutoff,))
        return cursor.rowcount


def get_stats() -> dict:
    """Get database statistics."""
    with get_connection() as conn:
        news_count = conn.execute("SELECT COUNT(*) FROM news_items").fetchone()[0]
        unused_count = conn.execute(
            "SELECT COUNT(*) FROM news_items WHERE used_in_digest = 0"
        ).fetchone()[0]
        digest_count = conn.execute("SELECT COUNT(*) FROM digests").fetchone()[0]

        return {
            'total_news_items': news_count,
            'unused_news_items': unused_count,
            'total_digests': digest_count
        }
