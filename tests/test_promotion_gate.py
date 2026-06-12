"""Unit tests for ar724.promotion_gate — the 10 mechanical gates.

OUT-3 verification: promotion requires passing all 10 mechanical gates.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from ar724.db import Database, now_iso
from ar724.promotion_gate import (
    GateReport, gate_budget, gate_candidate_hash, gate_deterministic_evaluation,
    gate_loop, gate_metric, gate_promotion_lock, gate_reviewer_independence,
    gate_schema, gate_scope, gate_stale_artifact, run_all_gates,
)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "test.db")


def _seed_run(db: Database, budget_cents: int = 10000) -> str:
    run_id = f"run-{uuid.uuid4()}"
    db.execute(
        "INSERT INTO runs (id, goal, status, created_at, budget_cents, daily_budget_cents) "
        "VALUES (?, 'test', 'running', ?, ?, ?)",
        (run_id, now_iso(), budget_cents, budget_cents),
    )
    return run_id


def test_gate_metric_blocks_regression(db: Database):
    """Gate 6: candidate_score < best * 0.9 is rejected."""
    failure = gate_metric(candidate_score=0.5, best_score=1.0, ratio=0.9)
    assert failure is not None
    assert failure.gate == "metric"


def test_gate_metric_passes_on_improvement():
    """Gate 6: candidate_score > best * 0.9 passes."""
    failure = gate_metric(candidate_score=1.0, best_score=1.0, ratio=0.9)
    assert failure is None
    failure = gate_metric(candidate_score=0.95, best_score=1.0, ratio=0.9)
    assert failure is None


def test_gate_deterministic_evaluation_only_keep_passes():
    """Gate 5: only KEEP decisions pass."""
    assert gate_deterministic_evaluation("KEEP") is None
    assert gate_deterministic_evaluation("DISCARD") is not None
    assert gate_deterministic_evaluation("BLOCKED") is not None
    assert gate_deterministic_evaluation(None) is not None


def test_gate_reviewer_independence_requires_separate_reviewer():
    """Gate 7: reviewer must be a different phase job from builder/evaluator."""
    f1 = gate_reviewer_independence("job-A", "job-A", "job-B")
    assert f1 is not None  # reviewer == builder
    f2 = gate_reviewer_independence("job-A", "job-B", "job-A")
    assert f2 is not None  # reviewer == evaluator
    f3 = gate_reviewer_independence("job-A", "job-B", "job-C")
    assert f3 is None
    f4 = gate_reviewer_independence(None, "job-B", "job-C")
    assert f4 is not None  # no reviewer


def test_gate_loop_blocks_circuit_breaker():
    """Gate 9: consecutive_discards >= 5 blocks promotion."""
    f = gate_loop(consecutive_discards=5, consecutive_blockeds=0, oscillation_fired=False)
    assert f is not None
    assert "consecutive_discards" in f.reason
    f2 = gate_loop(consecutive_discards=2, consecutive_blockeds=3, oscillation_fired=False)
    assert f2 is not None
    assert "consecutive_blockeds" in f2.reason
    f3 = gate_loop(consecutive_discards=2, consecutive_blockeds=1, oscillation_fired=True)
    assert f3 is not None
    assert "oscillation" in f3.reason
    f4 = gate_loop(consecutive_discards=2, consecutive_blockeds=1, oscillation_fired=False)
    assert f4 is None


def test_gate_candidate_hash_mismatch_blocks():
    """Gate 3: hash mismatch between candidate and evaluation is rejected."""
    f = gate_candidate_hash("abc123", "xyz789")
    assert f is not None
    assert "mismatch" in f.reason


def test_gate_stale_artifact_with_no_evaluation(db: Database):
    """Gate 4: no evaluation row is rejected."""
    f = gate_stale_artifact("nonexistent-hash", "iter-1", db)
    assert f is not None


def test_gate_scope_rejects_out_of_scope_write():
    """Gate 2: write outside write_scope is rejected."""
    f = gate_scope(["autoresearch/best/foo.json"], ["autoresearch/candidates/"])
    assert f is not None
    assert "best" in f.reason
    f2 = gate_scope(["autoresearch/candidates/x/proposal.json"], ["autoresearch/candidates/"])
    assert f2 is None


def test_gate_budget_blocks_over_budget(db: Database):
    """Gate 8: spending over budget blocks promotion."""
    run_id = _seed_run(db, budget_cents=100)
    # Insert cost events totaling 200 cents
    for i in range(2):
        db.execute(
            "INSERT INTO cost_events (id, run_id, provider, model, input_tokens, "
            "output_tokens, cache_read_tokens, cache_write_tokens, cost_cents, created_at) "
            "VALUES (?, ?, 'a', 'b', 1, 1, 0, 0, 100, ?)",
            (f"c-{i}", run_id, now_iso()),
        )
    f = gate_budget(db, run_id)
    assert f is not None
    assert "exceeded" in f.reason


def test_gate_promotion_lock_requires_lock_held(db: Database):
    """Gate 10: missing promotion lock is rejected."""
    f = gate_promotion_lock(db, "run-1")
    assert f is not None
    # Acquire the lock
    from ar724.promotion_gate import claim_promotion_lock
    assert claim_promotion_lock(db, "run-1")
    f2 = gate_promotion_lock(db, "run-1")
    assert f2 is None


def test_run_all_gates_returns_report(db: Database):
    """run_all_gates returns a GateReport with details for each gate."""
    run_id = _seed_run(db, budget_cents=10000)
    iter_id = f"iter-{uuid.uuid4()}"
    db.execute(
        'INSERT INTO iterations (id, run_id, "index", status, started_at, trace_id) '
        "VALUES (?, ?, 1, 'completed', ?, 'trace-1')",
        (iter_id, run_id, now_iso()),
    )
    cand_hash = "cand-hash-1"
    db.execute(
        "INSERT INTO candidates (hash, run_id, iteration_id, created_at) "
        "VALUES (?, ?, ?, ?)",
        (cand_hash, run_id, iter_id, now_iso()),
    )
    db.execute(
        "INSERT INTO evaluations (id, candidate_hash, decision, score, created_at) "
        "VALUES (?, ?, 'KEEP', 1.0, ?)",
        (f"eval-{uuid.uuid4()}", cand_hash, now_iso()),
    )
    db.execute(
        "INSERT INTO phase_jobs (id, run_id, iteration_id, phase, role, status, slot_name, attempt) "
        "VALUES (?, ?, ?, 'reviewing', 'backtest_reviewer', 'completed', 'slot_50', 0)",
        (f"pj-rev-{uuid.uuid4()}", run_id, iter_id),
    )
    db.execute(
        "INSERT INTO phase_jobs (id, run_id, iteration_id, phase, role, status, slot_name, attempt) "
        "VALUES (?, ?, ?, 'building', 'backtester', 'completed', 'slot_20', 0)",
        (f"pj-bld-{uuid.uuid4()}", run_id, iter_id),
    )
    db.execute(
        "INSERT INTO phase_jobs (id, run_id, iteration_id, phase, role, status, slot_name, attempt) "
        "VALUES (?, ?, ?, 'evaluating', 'evaluator_runner', 'completed', 'slot_40', 0)",
        (f"pj-evl-{uuid.uuid4()}", run_id, iter_id),
    )
    from ar724.promotion_gate import claim_promotion_lock
    claim_promotion_lock(db, run_id)
    report = run_all_gates(
        db, run_id=run_id,
        candidate_hash=cand_hash, iteration_id=iter_id,
        role_yaml={"id": "backtest_reviewer"},
        worker_output={}, write_paths=[],
        eval_decision="KEEP", candidate_score=1.0, best_score=1.0,
        reviewer_phase_job_id="reviewer-1",
        builder_phase_job_id="builder-1",
        evaluator_phase_job_id="evaluator-1",
        consecutive_discards=0, consecutive_blockeds=0,
        oscillation_fired=False,
        allowed_paths=["autoresearch/best/", "autoresearch/mutable/"],
    )
    assert isinstance(report, GateReport)
    assert report.passed, f"gates failed: {report.failures}"
    assert set(report.details.keys()) == {
        f"{i}_{g}" for i, g in enumerate([
            "schema", "scope", "candidate_hash", "stale_artifact",
            "deterministic_evaluation", "metric", "reviewer_independence",
            "budget", "loop", "promotion_lock",
        ], start=1)
    }
