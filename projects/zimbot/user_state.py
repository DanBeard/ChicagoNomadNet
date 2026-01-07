"""
User State Management

Handles per-user mode and preferences with SQLite persistence.
"""

import sqlite3
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Mode(Enum):
    """Available conversation modes."""
    HELP = "help"
    CHAT = "chat"
    SEARCH = "search"
    OPTIONS = "options"


@dataclass
class UserPreferences:
    """User configurable preferences."""
    show_stats: bool = True
    show_sources: bool = True
    verbosity: str = "detailed"  # "concise" or "detailed"


@dataclass
class UserState:
    """Complete user state including mode and preferences."""
    user_hash: str
    mode: Mode
    preferences: UserPreferences
    is_new_user: bool = True

    def to_db_row(self) -> tuple:
        """Convert to database row format."""
        return (
            self.user_hash,
            self.mode.value,
            1 if self.preferences.show_stats else 0,
            1 if self.preferences.show_sources else 0,
            self.preferences.verbosity,
            1 if self.is_new_user else 0
        )

    @classmethod
    def from_db_row(cls, row: tuple) -> "UserState":
        """Create from database row."""
        return cls(
            user_hash=row[0],
            mode=Mode(row[1]),
            preferences=UserPreferences(
                show_stats=bool(row[2]),
                show_sources=bool(row[3]),
                verbosity=row[4]
            ),
            is_new_user=bool(row[5])
        )


class UserStateManager:
    """Manages per-user state with SQLite persistence."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize the user_state table."""
        conn = sqlite3.connect(self.db_path)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS user_state (
                user_hash TEXT PRIMARY KEY,
                mode TEXT NOT NULL DEFAULT 'help',
                show_stats INTEGER NOT NULL DEFAULT 1,
                show_sources INTEGER NOT NULL DEFAULT 1,
                verbosity TEXT NOT NULL DEFAULT 'detailed',
                is_new_user INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()

    def get_state(self, user_hash: str) -> UserState:
        """Get user state, creating default if not exists."""
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            """SELECT user_hash, mode, show_stats, show_sources,
                      verbosity, is_new_user
               FROM user_state WHERE user_hash = ?""",
            (user_hash,)
        ).fetchone()

        if row:
            conn.close()
            return UserState.from_db_row(row)

        # Check if user exists in conversations (existing user)
        has_history = conn.execute(
            "SELECT 1 FROM conversations WHERE user_hash = ?",
            (user_hash,)
        ).fetchone() is not None

        # Create new state
        # Existing users start in CHAT mode, new users start in HELP mode
        state = UserState(
            user_hash=user_hash,
            mode=Mode.CHAT if has_history else Mode.HELP,
            preferences=UserPreferences(),
            is_new_user=not has_history
        )

        conn.execute('''
            INSERT INTO user_state
            (user_hash, mode, show_stats, show_sources, verbosity, is_new_user)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', state.to_db_row())
        conn.commit()
        conn.close()

        return state

    def set_mode(self, user_hash: str, mode: Mode):
        """Update user's current mode."""
        conn = sqlite3.connect(self.db_path)
        conn.execute('''
            UPDATE user_state
            SET mode = ?, last_updated = CURRENT_TIMESTAMP
            WHERE user_hash = ?
        ''', (mode.value, user_hash))

        if conn.total_changes == 0:
            # User doesn't exist yet, get_state will create them
            conn.close()
            state = self.get_state(user_hash)
            conn = sqlite3.connect(self.db_path)
            conn.execute('''
                UPDATE user_state SET mode = ? WHERE user_hash = ?
            ''', (mode.value, user_hash))
            conn.commit()

        conn.commit()
        conn.close()

    def update_preferences(self, user_hash: str, preferences: UserPreferences):
        """Update user preferences."""
        conn = sqlite3.connect(self.db_path)
        conn.execute('''
            UPDATE user_state
            SET show_stats = ?, show_sources = ?, verbosity = ?,
                last_updated = CURRENT_TIMESTAMP
            WHERE user_hash = ?
        ''', (
            1 if preferences.show_stats else 0,
            1 if preferences.show_sources else 0,
            preferences.verbosity,
            user_hash
        ))
        conn.commit()
        conn.close()

    def mark_not_new(self, user_hash: str):
        """Mark user as no longer new (has completed onboarding)."""
        conn = sqlite3.connect(self.db_path)
        conn.execute('''
            UPDATE user_state
            SET is_new_user = 0, last_updated = CURRENT_TIMESTAMP
            WHERE user_hash = ?
        ''', (user_hash,))
        conn.commit()
        conn.close()
