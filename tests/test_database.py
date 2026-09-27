"""Tests for SQLite database persistence."""

import os
from app.db.database import (
    get_db_connection,
    get_logs_for_session,
    get_session,
    init_db,
    save_log,
    save_session,
)


def test_db_initialization(tmp_path: os.PathLike) -> None:
    """Ensure init_db creates necessary tables."""
    db_file = str(tmp_path / "test_init.db")
    init_db(db_file)

    with get_db_connection(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
        )
        tables = [row["name"] for row in cursor.fetchall()]
        assert "sessions" in tables
        assert "agent_logs" in tables


def test_session_lifecycle(tmp_path: os.PathLike) -> None:
    """Test saving, updating, and fetching a session."""
    db_file = str(tmp_path / "test_session.db")
    init_db(db_file)

    session_id = "test_sess_001"
    save_session(session_id, "Test user goal", status="initialized", db_path=db_file)

    session = get_session(session_id, db_path=db_file)
    assert session is not None
    assert session["session_id"] == session_id
    assert session["user_goal"] == "Test user goal"
    assert session["status"] == "initialized"

    # Update session status
    save_session(session_id, "Test user goal", status="completed", db_path=db_file)
    updated = get_session(session_id, db_path=db_file)
    assert updated is not None
    assert updated["status"] == "completed"


def test_agent_logs_persistence(tmp_path: os.PathLike) -> None:
    """Test logging actions and retrieving session history."""
    db_file = str(tmp_path / "test_logs.db")
    init_db(db_file)

    session_id = "test_sess_002"
    save_session(session_id, "Goal for logging", db_path=db_file)

    log_id1 = save_log(
        session_id=session_id,
        step_index=0,
        role="user",
        content="Calculate 2 + 2",
        metadata={"user_id": "u123"},
        db_path=db_file,
    )
    assert log_id1 > 0

    log_id2 = save_log(
        session_id=session_id,
        step_index=1,
        role="tool",
        content="4",
        metadata={"tool": "calculator"},
        db_path=db_file,
    )
    assert log_id2 > log_id1

    logs = get_logs_for_session(session_id, db_path=db_file)
    assert len(logs) == 2
    assert logs[0]["role"] == "user"
    assert logs[0]["metadata"]["user_id"] == "u123"
    assert logs[1]["role"] == "tool"
    assert logs[1]["content"] == "4"
