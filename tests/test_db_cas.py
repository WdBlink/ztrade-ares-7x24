"""Unit tests for ar724.db — CAS on phase_jobs (PRD §6.4).

OUT-1 verification: SQLite WAL is the source of truth; CAS claims are atomic.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from pathlib import Path

import pytest

from ar724.db import Database, now_iso


@pytest.fixture
def db(tmp_path: Path) -> Database:
    p = tmp_path / "test.db"
    return Database(p)


def _seed_phase_job(db: Database, status: str = "queued") -> str:
    job_id = f"pj-{uuid.uuid4()}"
    run_id = f"run-{uuid.uuid4()}"
    iter_id = f"iter-{uuid.uuid4()}"
    db.execute(
        "INSERT INTO runs (id, goal, status, created_at, budget_cents, daily_budget_cents) "
        "VALUES (?, ?, 'created', ?, 100, 50)",
        (run_id, "test", now_iso()),
    )
    db.execute(
        'INSERT INTO iterations (id, run_id, "index", status, started_at, trace_id) '
        "VALUES (?, ?, 1, 'queued', ?, ?)",
        (iter_id, run_id, now_iso(), "trace-1"),
    )
    db.execute(
        "INSERT INTO phase_jobs "
        "(id, run_id, iteration_id, phase, role, status, slot_name, attempt) "
        "VALUES (?, ?, ?, 'proposing', 'factor_combiner', ?, 'slot_10', 0)",
        (job_id, run_id, iter_id, status),
    )
    return job_id


def test_cas_update_succeeds_on_matching_status(db: Database):
    """CAS UPDATE on queued phase job succeeds when status matches."""
    job_id = _seed_phase_job(db, status="queued")
    rowcount = db.cas_update(
        "phase_jobs", job_id, "queued",
        {"status": "running", "claimed_by": "worker-1"},
    )
    assert rowcount == 1
    row = db.fetchone("SELECT status, claimed_by FROM phase_jobs WHERE id = ?", (job_id,))
    assert row["status"] == "running"
    assert row["claimed_by"] == "worker-1"


def test_cas_update_fails_on_mismatched_status(db: Database):
    """CAS UPDATE fails when the current status is not 'queued'."""
    job_id = _seed_phase_job(db, status="running")
    rowcount = db.cas_update(
        "phase_jobs", job_id, "queued",  # expected 'queued', but row is 'running'
        {"status": "running", "claimed_by": "worker-1"},
    )
    assert rowcount == 0
    # Original row is unchanged
    row = db.fetchone("SELECT status, claimed_by FROM phase_jobs WHERE id = ?", (job_id,))
    assert row["status"] == "running"
    assert row["claimed_by"] is None


def test_cas_update_exactly_one_winner_under_concurrent_claims(db: Database):
    """Concurrent CAS claims on the same phase job result in exactly one winner."""
    job_id = _seed_phase_job(db, status="queued")
    results: list[int] = []
    lock = threading.Lock()

    def claim(worker_id: str) -> None:
        rc = db.cas_update(
            "phase_jobs", job_id, "queued",
            {"status": "running", "claimed_by": worker_id},
        )
        with lock:
            results.append(rc)

    threads = [threading.Thread(target=claim, args=(f"w{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    winners = sum(results)
    assert winners == 1, f"expected exactly 1 winner, got {winners}: {results}"


def test_atomic_write_creates_file(tmp_path: Path):
    """atomic_write creates the file and survives a clean call."""
    from ar724.db import atomic_write
    target = tmp_path / "out.json"
    atomic_write(target, '{"hello": "world"}')
    assert target.exists()
    assert target.read_text() == '{"hello": "world"}'


def test_atomic_write_replaces_existing(tmp_path: Path):
    """atomic_write overwrites the target atomically."""
    from ar724.db import atomic_write
    target = tmp_path / "out.json"
    atomic_write(target, '{"a": 1}')
    atomic_write(target, '{"a": 2}')
    assert target.read_text() == '{"a": 2}'


def test_replace_with_retry_is_atomic_on_posix(tmp_path: Path):
    """replace_with_retry succeeds on first try on POSIX."""
    from ar724.db import replace_with_retry
    target = tmp_path / "out.txt"
    tmp = tmp_path / "out.txt.tmp"
    tmp.write_text("hello")
    replace_with_retry(tmp, target)
    assert target.read_text() == "hello"
    assert not tmp.exists()
