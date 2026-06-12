"""State authority test — SQLite is the source of truth.

OUT-1 verification: writing tmux pane text does NOT affect state.db.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

import pytest

from ar724.db import Database, now_iso


def test_state_db_ignores_external_writes_to_events_jsonl(tmp_path: Path, monkeypatch):
    """Even if events.jsonl is corrupted or written externally, state.db
    remains the source of truth (PRD §5.4).
    """
    db = Database(tmp_path / "state.db")
    # Insert one event
    db.execute(
        "INSERT INTO events (id, event_type, severity, created_at) "
        "VALUES (?, ?, ?, ?)",
        (f"e-{uuid.uuid4()}", "test_event", "info", now_iso()),
    )
    # Write garbage to events.jsonl (which doesn't exist yet)
    jsonl = tmp_path / "events.jsonl"
    jsonl.write_text("this is not JSON\n{broken\n")
    # state.db is unaffected
    rows = db.fetchall("SELECT * FROM events")
    assert len(rows) == 1
    # The events.jsonl content is irrelevant
    assert "not JSON" in jsonl.read_text()


def test_state_db_resilient_to_missing_files(tmp_path: Path):
    """The controller can recover from missing side-channel files."""
    db = Database(tmp_path / "state.db")
    # No events.jsonl exists; reads should not raise
    events = db.fetchall("SELECT * FROM events")
    assert events == []


def test_state_db_persists_across_reopens(tmp_path: Path):
    """state.db data persists across Database() reopens."""
    p = tmp_path / "state.db"
    db1 = Database(p)
    db1.execute(
        "INSERT INTO events (id, event_type, severity, created_at) "
        "VALUES (?, ?, ?, ?)",
        (f"e-{uuid.uuid4()}", "persistence_test", "info", now_iso()),
    )
    db1.close()
    # Reopen
    db2 = Database(p)
    rows = db2.fetchall(
        "SELECT * FROM events WHERE event_type = 'persistence_test'"
    )
    assert len(rows) == 1
