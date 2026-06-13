"""Tests for lifecycle audit events on pause/resume/halt."""

from __future__ import annotations

from click.testing import CliRunner

import pytest

from ar724.cli import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def db_setup(tmp_path, monkeypatch):
    """Set up a fresh .ares/ in tmp_path and chdir there."""
    monkeypatch.chdir(tmp_path)
    # Initialize state.db and a run
    from ar724.cli import _db
    from ar724.conductor import create_run, start_run
    from ar724.db import Database
    db = Database(tmp_path / ".ares" / "state.db")
    (tmp_path / ".ares").mkdir(exist_ok=True)
    run_id = create_run(db, goal="test")
    start_run(db, run_id)
    return db, run_id


def test_pause_emits_run_paused_event(db_setup, runner):
    """ar724 pause writes a run_paused audit event."""
    db, run_id = db_setup
    result = runner.invoke(main, ["pause"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "paused" in result.output
    rows = db.fetchall(
        "SELECT event_type, severity FROM events WHERE event_type = 'run_paused'"
    )
    assert len(rows) == 1
    assert rows[0]["severity"] == "info"


def test_resume_emits_run_resumed_event(db_setup, runner):
    """ar724 resume writes a run_resumed audit event."""
    db, run_id = db_setup
    # First pause so resume has something to do
    db.execute("UPDATE runs SET status = 'paused' WHERE id = ?", (run_id,))
    result = runner.invoke(main, ["resume"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "resumed" in result.output
    rows = db.fetchall(
        "SELECT event_type FROM events WHERE event_type = 'run_resumed'"
    )
    assert len(rows) == 1


def test_pause_no_running_run_emits_no_event(db_setup, runner):
    """ar724 pause with no running run writes nothing (silent no-op)."""
    db, run_id = db_setup
    # Move the run to a non-running state
    db.execute("UPDATE runs SET status = 'completed' WHERE id = ?", (run_id,))
    result = runner.invoke(main, ["pause"], catch_exceptions=False)
    assert result.exit_code == 0
    rows = db.fetchall(
        "SELECT event_type FROM events WHERE event_type = 'run_paused'"
    )
    assert len(rows) == 0


def test_halt_emits_run_halted_event(db_setup, runner):
    """ar724 halt --force writes a run_halted audit event with critical severity."""
    db, run_id = db_setup
    result = runner.invoke(main, ["halt", "test reason", "--force"],
                          catch_exceptions=False)
    assert result.exit_code == 0
    rows = db.fetchall(
        "SELECT event_type, severity FROM events WHERE event_type = 'run_halted'"
    )
    assert len(rows) == 1
    assert rows[0]["severity"] == "critical"


def test_halt_without_force_refuses_and_emits_no_event(db_setup, runner):
    """ar724 halt without --force refuses and writes nothing."""
    db, run_id = db_setup
    result = runner.invoke(main, ["halt", "test reason"], catch_exceptions=False)
    assert result.exit_code == 2
    rows = db.fetchall(
        "SELECT event_type FROM events WHERE event_type = 'run_halted'"
    )
    assert len(rows) == 0
