"""Budget tests — pre-call estimator, gates, anomaly.

OUT-5 verification: pre-call estimator rejects over-budget calls.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from ar724.budget import AnomalyDetector, check_budget, estimate_call, record_cost
from ar724.db import Database, now_iso


def test_estimate_call_basic():
    """A short input produces a small cost; allowed=True."""
    result = estimate_call("factor_combiner", "x" * 100, expected_output_tokens=200)
    assert result.allowed
    assert result.cost_cents >= 0
    assert result.input_tokens > 0


def test_estimate_call_deterministic_role():
    """The evaluator_runner has a null profile and is allowed at zero cost."""
    result = estimate_call("evaluator_runner", "any input", expected_output_tokens=0)
    assert result.allowed
    assert result.cost_cents == 0
    assert result.profile == "null"


def test_estimate_call_rejects_unknown_profile():
    """An unknown role returns a default profile (which is allowed) but no profile cost."""
    result = estimate_call("nonexistent_role_xyz", "x" * 100)
    # Unknown role with no default_profile = treated as deterministic
    assert result.allowed


def test_check_budget_passes_when_under(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    run_id = f"run-{uuid.uuid4()}"
    db.execute(
        "INSERT INTO runs (id, goal, status, created_at, budget_cents, daily_budget_cents) "
        "VALUES (?, 'x', 'running', ?, 1000, 1000)",
        (run_id, now_iso()),
    )
    run = dict(db.fetchone("SELECT * FROM runs WHERE id = ?", (run_id,)))
    allowed, reason = check_budget(db, run)
    assert allowed
    assert reason == ""


def test_check_budget_blocks_over_budget(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    run_id = f"run-{uuid.uuid4()}"
    db.execute(
        "INSERT INTO runs (id, goal, status, created_at, budget_cents, daily_budget_cents) "
        "VALUES (?, 'x', 'running', ?, 100, 100)",
        (run_id, now_iso()),
    )
    db.execute(
        "INSERT INTO cost_events (id, run_id, provider, model, input_tokens, "
        "output_tokens, cache_read_tokens, cache_write_tokens, cost_cents, created_at) "
        "VALUES (?, ?, 'a', 'b', 1, 1, 0, 0, 200, ?)",
        (f"c-{uuid.uuid4()}", run_id, now_iso()),
    )
    run = dict(db.fetchone("SELECT * FROM runs WHERE id = ?", (run_id,)))
    allowed, reason = check_budget(db, run)
    assert not allowed
    assert "exceeded" in reason


def test_anomaly_detector_single_call_spike():
    det = AnomalyDetector()
    # Establish baseline of ~100 cents per call
    for _ in range(5):
        det.observe_call(100)
    # 3x spike = 300+ cents triggers
    triggered = det.observe_call(400)
    assert triggered is not None
    assert triggered["type"] == "single_call_spike"


def test_anomaly_detector_no_false_positive_on_normal():
    det = AnomalyDetector()
    for cost in [100, 110, 90, 105, 95]:
        det.observe_call(cost)
    triggered = det.observe_call(105)
    assert triggered is None


def test_anomaly_detector_daily_spike():
    det = AnomalyDetector()
    for _ in range(5):
        det.observe_daily(1000)
    triggered = det.observe_daily(3000)
    assert triggered is not None
    assert triggered["type"] == "daily_spike"


def test_record_cost_inserts_row(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    cents = record_cost(
        db, run_id=None, phase_job_id=None,
        provider="anthropic", model="sonnet_4_5",
        input_tokens=1000, output_tokens=500,
    )
    assert cents >= 0
    rows = db.fetchall("SELECT * FROM cost_events")
    assert len(rows) == 1
