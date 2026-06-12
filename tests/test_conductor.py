"""Conductor tests — tick loop, controller lock, stale reaping.

OUT-1 verification: a single controller process owns state transitions.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest

from ar724.conductor import (
    LOCK_LEASE_SECONDS, acquire_controller_lock, create_iteration, create_run,
    enqueue_phase_jobs, heartbeat_controller_lock, reap_stale_phase_jobs,
    start_run, tick,
)
from ar724.db import Database, now_iso


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "test.db")


def _bootstrap_run(db: Database) -> str:
    run_id = create_run(db, goal="test")
    start_run(db, run_id)
    iter_id = create_iteration(db, run_id=run_id, index=1)
    enqueue_phase_jobs(db, run_id=run_id, iteration_id=iter_id)
    return run_id


def test_acquire_controller_lock_first_time_succeeds(db: Database):
    """First acquisition of the controller lock succeeds."""
    run_id = f"run-{uuid.uuid4()}"
    db.execute(
        "INSERT INTO runs (id, goal, status, created_at) "
        "VALUES (?, 'x', 'running', ?)",
        (run_id, now_iso()),
    )
    assert acquire_controller_lock(db, run_id) is True


def test_acquire_controller_lock_holder_wins(db: Database):
    """The same holder can re-acquire (refresh) its own lock."""
    run_id = f"run-{uuid.uuid4()}"
    db.execute(
        "INSERT INTO runs (id, goal, status, created_at) "
        "VALUES (?, 'x', 'running', ?)",
        (run_id, now_iso()),
    )
    assert acquire_controller_lock(db, run_id) is True
    # Refresh
    assert acquire_controller_lock(db, run_id) is True
    heartbeat_controller_lock(db, run_id)


def test_acquire_controller_lock_other_holder_loses(db: Database):
    """A different run_id cannot acquire while another holds the lock."""
    run_id_1 = f"run-{uuid.uuid4()}"
    run_id_2 = f"run-{uuid.uuid4()}"
    for rid in (run_id_1, run_id_2):
        db.execute(
            "INSERT INTO runs (id, goal, status, created_at) "
            "VALUES (?, 'x', 'running', ?)",
            (rid, now_iso()),
        )
    assert acquire_controller_lock(db, run_id_1) is True
    # Lock is held by run_id_1, so run_id_2 cannot acquire.
    assert acquire_controller_lock(db, run_id_2) is False


def test_tick_schedules_phase_job(db: Database, tmp_path: Path):
    """A tick claims the next queued phase job and emits an event."""
    run_id = _bootstrap_run(db)
    result = tick(
        db, run_id,
        roles_dir=Path(__file__).resolve().parent.parent / "autoresearch" / "v2" / "roles",
        working_dir=tmp_path, events_jsonl=tmp_path / "events.jsonl",
    )
    assert result.scheduled == 1
    # The phase job is now 'running'
    running = db.fetchall(
        "SELECT * FROM phase_jobs WHERE run_id = ? AND status = 'running'",
        (run_id,),
    )
    assert len(running) == 1
    assert running[0]["claimed_by"].startswith("worker-")


def test_tick_skips_when_no_running_run(db: Database, tmp_path: Path):
    """A tick is a no-op when no run is in 'running' state."""
    run_id = create_run(db, goal="test")  # created, not started
    result = tick(
        db, run_id,
        roles_dir=tmp_path / "roles",  # doesn't matter
        working_dir=tmp_path, events_jsonl=tmp_path / "events.jsonl",
    )
    assert result.scheduled == 0


def test_reap_stale_phase_jobs_marks_orphaned(db: Database):
    """A phase job with an expired lease is marked failed and the slot is killed."""
    run_id = create_run(db, goal="test")
    start_run(db, run_id)
    iter_id = create_iteration(db, run_id=run_id, index=1)
    enqueue_phase_jobs(db, run_id=run_id, iteration_id=iter_id)
    # Manually mark one as running with an EXPIRED lease
    pj = db.fetchone(
        "SELECT id FROM phase_jobs WHERE run_id = ? LIMIT 1", (run_id,),
    )
    db.execute(
        "UPDATE phase_jobs SET status = 'running', "
        "lease_expires_at = '2020-01-01T00:00:00Z', slot_name = 'proposer' "
        "WHERE id = ?",
        (pj["id"],),
    )
    reaped = reap_stale_phase_jobs(db, run_id=run_id)
    assert pj["id"] in reaped
    row = db.fetchone("SELECT status, error_class FROM phase_jobs WHERE id = ?", (pj["id"],))
    assert row["status"] == "failed"
    assert row["error_class"] == "orphaned_process"


def test_conductor_lock_constants():
    """Lock lease is 180s per PRD §11.3 timing constants table."""
    assert LOCK_LEASE_SECONDS == 180
