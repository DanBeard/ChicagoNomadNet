"""
Lazy Embedding Queue Management

Handles persistent queue for lazy embedding tasks with priority support.
Queue persists in SQLite (same DB as main indexer) and survives restarts.
"""

import sqlite3
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass


@dataclass
class EmbeddingTask:
    """Represents a task in the embedding queue."""
    id: int
    keyword: Optional[str]
    source_type: str  # 'conversation', 'link', 'drip'
    priority: int  # 1-10, higher = more urgent
    status: str  # 'pending', 'in_progress', 'completed', 'failed'
    archive_idx: Optional[int]
    article_path: Optional[str]
    created_at: str
    processed_at: Optional[str]
    articles_found: int
    chunks_embedded: int
    error_message: Optional[str]


class LazyEmbeddingQueue:
    """
    Manages the lazy embedding queue in SQLite.

    Priority levels:
      - 10: conversation-driven (user actively asking)
      - 5: link-following (discovered during embedding)
      - 1: drip/random (background coverage building)
    """

    PRIORITY_CONVERSATION = 10
    PRIORITY_LINK = 5
    PRIORITY_DRIP = 1

    def __init__(self, conn: sqlite3.Connection):
        """
        Initialize with existing SQLite connection.
        Tables should already exist (created by ZIMIndexer.initialize_chroma).
        """
        self.conn = conn

    def queue_keywords(self, keywords: List[str], source_type: str = 'conversation',
                       priority: int = PRIORITY_CONVERSATION,
                       archive_idx: Optional[int] = None) -> int:
        """
        Queue keywords for embedding. Returns number of new tasks added.
        Skips duplicates (same keyword + archive combo already pending).
        """
        added = 0
        for keyword in keywords:
            # Check for existing pending/in_progress task
            existing = self.conn.execute('''
                SELECT id FROM embedding_queue
                WHERE keyword = ? AND (archive_idx = ? OR (archive_idx IS NULL AND ? IS NULL))
                AND status IN ('pending', 'in_progress')
            ''', (keyword, archive_idx, archive_idx)).fetchone()

            if existing:
                continue  # Skip duplicate

            self.conn.execute('''
                INSERT INTO embedding_queue (keyword, source_type, priority, archive_idx)
                VALUES (?, ?, ?, ?)
            ''', (keyword, source_type, priority, archive_idx))
            added += 1

        if added > 0:
            self.conn.commit()

        return added

    def queue_article(self, archive_idx: int, article_path: str,
                      source_type: str = 'link',
                      priority: int = PRIORITY_LINK) -> bool:
        """
        Queue a specific article for embedding. Returns True if added.
        """
        # Check for existing task
        existing = self.conn.execute('''
            SELECT id FROM embedding_queue
            WHERE archive_idx = ? AND article_path = ?
            AND status IN ('pending', 'in_progress')
        ''', (archive_idx, article_path)).fetchone()

        if existing:
            return False

        self.conn.execute('''
            INSERT INTO embedding_queue (article_path, archive_idx, source_type, priority)
            VALUES (?, ?, ?, ?)
        ''', (article_path, archive_idx, source_type, priority))
        self.conn.commit()
        return True

    def get_next_task(self) -> Optional[EmbeddingTask]:
        """
        Get the highest priority pending task and mark it in_progress.
        Returns None if queue is empty.
        """
        # Select highest priority, oldest first
        row = self.conn.execute('''
            SELECT id, keyword, source_type, priority, status, archive_idx,
                   article_path, created_at, processed_at, articles_found,
                   chunks_embedded, error_message
            FROM embedding_queue
            WHERE status = 'pending'
            ORDER BY priority DESC, created_at ASC
            LIMIT 1
        ''').fetchone()

        if not row:
            return None

        task = EmbeddingTask(
            id=row[0], keyword=row[1], source_type=row[2], priority=row[3],
            status=row[4], archive_idx=row[5], article_path=row[6],
            created_at=row[7], processed_at=row[8], articles_found=row[9],
            chunks_embedded=row[10], error_message=row[11]
        )

        # Mark as in_progress
        self.conn.execute('''
            UPDATE embedding_queue SET status = 'in_progress'
            WHERE id = ?
        ''', (task.id,))
        self.conn.commit()

        task.status = 'in_progress'
        return task

    def complete_task(self, task_id: int, articles_found: int = 0,
                      chunks_embedded: int = 0):
        """Mark a task as completed with results."""
        self.conn.execute('''
            UPDATE embedding_queue
            SET status = 'completed',
                processed_at = CURRENT_TIMESTAMP,
                articles_found = ?,
                chunks_embedded = ?
            WHERE id = ?
        ''', (articles_found, chunks_embedded, task_id))
        self.conn.commit()

    def fail_task(self, task_id: int, error_message: str):
        """Mark a task as failed with error details."""
        self.conn.execute('''
            UPDATE embedding_queue
            SET status = 'failed',
                processed_at = CURRENT_TIMESTAMP,
                error_message = ?
            WHERE id = ?
        ''', (error_message, task_id))
        self.conn.commit()

    def reset_stale_tasks(self, timeout_minutes: int = 30):
        """
        Reset tasks stuck in 'in_progress' state for too long.
        This handles worker crashes.
        """
        self.conn.execute('''
            UPDATE embedding_queue
            SET status = 'pending'
            WHERE status = 'in_progress'
            AND datetime(created_at, '+' || ? || ' minutes') < datetime('now')
        ''', (timeout_minutes,))
        self.conn.commit()

    def get_queue_depth(self) -> int:
        """Get count of pending tasks."""
        return self.conn.execute(
            "SELECT COUNT(*) FROM embedding_queue WHERE status = 'pending'"
        ).fetchone()[0]

    def get_stats(self) -> Dict:
        """Get queue statistics."""
        stats = {}
        for status in ['pending', 'in_progress', 'completed', 'failed']:
            count = self.conn.execute(
                "SELECT COUNT(*) FROM embedding_queue WHERE status = ?",
                (status,)
            ).fetchone()[0]
            stats[status] = count

        # Get by source type
        for source_type in ['conversation', 'link', 'drip']:
            count = self.conn.execute(
                "SELECT COUNT(*) FROM embedding_queue WHERE source_type = ? AND status = 'pending'",
                (source_type,)
            ).fetchone()[0]
            stats[f'pending_{source_type}'] = count

        return stats

    def cleanup_old_completed(self, days: int = 7):
        """Remove completed tasks older than specified days."""
        self.conn.execute('''
            DELETE FROM embedding_queue
            WHERE status IN ('completed', 'failed')
            AND datetime(processed_at, '+' || ? || ' days') < datetime('now')
        ''', (days,))
        self.conn.commit()
