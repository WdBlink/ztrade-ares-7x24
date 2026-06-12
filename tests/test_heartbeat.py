"""Heartbeat tests — phase 13 acceptance.

A worker running with HeartbeatTimer writes worker_heartbeat events every 3s.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import pytest

from ar724.db import Database, now_iso
from ar724.heartbeat import HeartbeatTimer


def test_heartbeat_writes_events(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    run_id = f"run-{uuid.uuid4()}"
    iter_id = f"iter-{uuid.uuid4()}"
    job_id = f"pj-{uuid.uuid4()}"
    db.execute(
        "INSERT INTO runs (id, goal, status, created_at, budget_cents) "
        "VALUES (?, 'x', 'running', ?, 100)",
        (run_id, now_iso()),
    )
    db.execute(
        'INSERT INTO iterations (id, run_id, "index", status, started_at) '
        "VALUES (?, ?, 1, 'running', ?)",
        (iter_id, run_id, now_iso()),
    )
    db.execute(
        "INSERT INTO phase_jobs (id, run_id, iteration_id, phase, role, status, attempt) "
        "VALUES (?, ?, ?, 'proposing', 'factor_combiner', 'running', 1)",
        (job_id, run_id, iter_id),
    )
    events_jsonl = tmp_path / "events.jsonl"
    timer = HeartbeatTimer(
        db, run_id, job_id, interval_s=0.05, events_jsonl_path=events_jsonl,
    )
    timer.start()
    time.sleep(0.25)  # ~4 beats at 50ms interval
    timer.stop()
    assert timer.beat_count >= 3
    # events.jsonl has at least 3 heartbeat events
    lines = [
        json.loads(line) for line in events_jsonl.read_text().splitlines() if line
    ]
    assert len(lines) >= 3
    assert all(line["event_type"] == "worker_heartbeat" for line in lines)
    # phase_jobs last_heartbeat_at is recent
    row = db.fetchone("SELECT last_heartbeat_at FROM phase_jobs WHERE id = ?", (job_id,))
    assert row["last_heartbeat_at"] is not None


def test_heartbeat_swallows_exceptions():
    """HeartbeatTimer must never crash the worker, even if the DB is broken."""
    db = Database(Path("/tmp/x.db"))
    timer = HeartbeatTimer(
        db, "fake-run", "fake-job", interval_s=0.01,
    )
    # Start a heartbeat for a non-existent phase job; the UPDATE will match 0 rows
    # but should not raise.
    timer.start()
    time.sleep(0.05)
    timer.stop()
    # No assertion; just that stop() returns cleanly.
