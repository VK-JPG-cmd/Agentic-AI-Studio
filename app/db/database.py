"""SQLite database persistence layer.

Provides connection management and schema initialization for persistent agent state.
"""

from contextlib import contextmanager
import json
import os
import sqlite3
from typing import Any, Dict, Generator, List, Optional

from app.config import get_settings


def _get_target_db_path(db_path: Optional[str] = None) -> str:
    """Resolve database path from argument or application settings."""
    if db_path is not None:
        return db_path
    return get_settings().database_path


@contextmanager
def get_db_connection(db_path: Optional[str] = None) -> Generator[sqlite3.Connection, None, None]:
    """Provide a transactional SQLite database connection with row factory."""
    resolved_path = _get_target_db_path(db_path)
    
    # Ensure directory exists if path contains directory structure
    dir_name = os.path.dirname(resolved_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    conn = sqlite3.connect(resolved_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Optional[str] = None) -> None:
    """Initialize SQLite database tables if they do not exist."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()

        # Sessions table to store user goals and lifecycle state
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                user_goal TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'initialized',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        # Agent logs table for audit trail of steps, tool calls, and observations
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES sessions (session_id) ON DELETE CASCADE
            );
            """
        )

        # Create indices for fast lookup
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_agent_logs_session_id
            ON agent_logs (session_id);
            """
        )


def save_session(
    session_id: str,
    user_goal: str,
    status: str = "initialized",
    db_path: Optional[str] = None,
) -> None:
    """Save or update an agent session."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO sessions (session_id, user_goal, status, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(session_id) DO UPDATE SET
                status = excluded.status,
                updated_at = CURRENT_TIMESTAMP;
            """,
            (session_id, user_goal, status),
        )


def get_session(session_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieve an existing session by ID."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None


def save_log(
    session_id: str,
    step_index: int,
    role: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
    db_path: Optional[str] = None,
) -> int:
    """Append a log entry for a session step and return its row ID."""
    metadata_json = json.dumps(metadata) if metadata is not None else None
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO agent_logs (session_id, step_index, role, content, metadata)
            VALUES (?, ?, ?, ?, ?);
            """,
            (session_id, step_index, role, content, metadata_json),
        )
        return cursor.lastrowid or 0


def get_logs_for_session(
    session_id: str, db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Retrieve all execution logs for a session ordered by creation time."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, session_id, step_index, role, content, metadata, created_at
            FROM agent_logs
            WHERE session_id = ?
            ORDER BY id ASC;
            """,
            (session_id,),
        )
        rows = cursor.fetchall()
        results = []
        for r in rows:
            entry = dict(r)
            if entry.get("metadata"):
                try:
                    entry["metadata"] = json.loads(entry["metadata"])
                except Exception:
                    pass
            results.append(entry)
        return results
