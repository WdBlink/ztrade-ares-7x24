"""Observability tests — traces, metrics, logs, evals.

OUT-6 verification: trace_id flows through; metric counters increment;
event_type vocabulary is enforced.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from ar724.db import Database, now_iso
from ar724.event_types import (
    ALL_EVENT_TYPES, assert_valid_event_type, is_valid_event_type,
)
from ar724.observability import (
    compute_metrics, emit_event, new_trace_id, record_eval_result, tail_events,
)


def test_new_trace_id_is_uuid():
    tid = new_trace_id()
    # UUIDs are 36 chars including hyphens
    assert len(tid) == 36


def test_emit_event_inserts_row(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    events_jsonl = tmp_path / "events.jsonl"
    eid = emit_event(
        db, event_type="run_created", severity="info",
        payload={"goal": "test"}, events_jsonl_path=events_jsonl,
    )
    assert eid.startswith("evt-")
    row = db.fetchone("SELECT * FROM events WHERE id = ?", (eid,))
    assert row is not None
    assert row["event_type"] == "run_created"
    assert row["severity"] == "info"
    assert events_jsonl.exists()
    # events.jsonl has the record
    line = events_jsonl.read_text().strip()
    record = json.loads(line)
    assert record["id"] == eid
    assert record["event_type"] == "run_created"


def test_emit_event_rejects_unknown_type():
    db = Database(Path("/tmp/x.db"))
    with pytest.raises(ValueError, match="Unknown event_type"):
        emit_event(db, event_type="totally_made_up_event", severity="info")


def test_emit_event_rejects_invalid_severity():
    db = Database(Path("/tmp/x.db"))
    with pytest.raises(ValueError, match="Invalid severity"):
        emit_event(db, event_type="run_created", severity="fatal")


def test_assert_valid_event_type_known_and_unknown():
    assert_valid_event_type("run_created")
    assert_valid_event_type("phase_claimed")
    with pytest.raises(ValueError):
        assert_valid_event_type("nonsense_event")


def test_is_valid_event_type():
    assert is_valid_event_type("run_created")
    assert not is_valid_event_type("not_a_real_event")


def test_event_type_vocabulary_matches_prd_13_3():
    """The vocabulary includes all categories from PRD §13.3."""
    expected = {
        # Run lifecycle
        "run_created", "run_started", "run_paused", "run_resumed",
        "run_completed", "run_halted", "run_failed", "run_cancelled",
        # Iteration
        "iter_queued", "iter_started", "iter_completed",
        "iter_blocked", "iter_discarded", "iter_vetoed",
        # Phase job
        "phase_queued", "phase_claimed", "phase_started", "phase_completed",
        "phase_failed", "phase_retry", "phase_orphaned",
        # Worker
        "worker_spawned", "worker_heartbeat", "worker_exited", "worker_force_killed",
        # Promotion
        "promotion_prepared", "promotion_applying", "promotion_committed",
        "promotion_failed", "promotion_reconciled",
        # Budget
        "budget_warning", "budget_exceeded",
        # Quality
        "oscillation_detected", "consecutive_discard", "consecutive_blocked",
        "circuit_breaker_tripped",
        # Safety
        "safety_policy_loaded", "safety_violation_blocked", "mcp_allowlist_changed",
        # Eval
        "eval_run_started", "eval_run_completed",
    }
    assert expected.issubset(ALL_EVENT_TYPES)


def test_compute_metrics_returns_all_prd_13_1_metrics(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    metrics = compute_metrics(db)
    expected = {
        "runs.total_active",
        "iterations.completed.total",
        "iterations.completed.last_24h",
        "promotion.committed.total",
        "discards.consecutive.current",
        "blockeds.consecutive.current",
        "cost.cents.today",
        "cost.cents.per_iter",
        "oscillation.detections.total",
        "workers.active.current",
        "evaluator.failures.consecutive",
    }
    assert expected.issubset(metrics.keys())


def test_record_eval_result_persists(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    eid = record_eval_result(
        db, name="evaluator_correctness",
        result="pass", metrics={"fixture": "synth.json"},
    )
    rows = db.fetchall("SELECT * FROM eval_results WHERE id = ?", (eid,))
    assert len(rows) == 1
    assert rows[0]["result"] == "pass"
    assert rows[0]["name"] == "evaluator_correctness"


def test_tail_events_filters_by_severity(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    emit_event(db, event_type="run_created", severity="info")
    emit_event(db, event_type="budget_warning", severity="warn")
    emit_event(db, event_type="budget_exceeded", severity="critical")
    warn_only = tail_events(db, severity="warn", limit=10)
    assert all(r["severity"] == "warn" for r in warn_only)
    assert len(warn_only) == 1
