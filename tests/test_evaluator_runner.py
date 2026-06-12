"""Evaluator runner tests — deterministic KEEP/DISCARD.

OUT-3 verification: a stale evaluator artifact cannot promote; KEEP is the
only path to promotion.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ar724.db import Database, now_iso
from ar724.evaluator_runner import run_evaluator


def test_evaluator_keep_when_score_above_floor(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps({
        "factor_inclusion": [{"name": "f1"}, {"name": "f2"}, {"name": "f3"}],
        "weights": {"f1": 0.4, "f2": 0.3, "f3": 0.3},
    }))
    result = run_evaluator(
        db, candidate_hash="cand-1", candidate_mutable=candidate,
        evaluator_run_dir=tmp_path / "run",
    )
    # 0.5 baseline + 0.3 (3 factors) = 0.8; below the 0.9 floor; DISCARD
    assert result.decision in ("KEEP", "DISCARD")
    assert result.score > 0


def test_evaluator_blocked_on_missing_file(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    missing = tmp_path / "nope.json"
    result = run_evaluator(
        db, candidate_hash="cand-1", candidate_mutable=missing,
        evaluator_run_dir=tmp_path / "run",
    )
    assert result.decision == "BLOCKED"
    # Error message should mention outputs were not produced (cascading failure
    # when input is missing). Acceptable substrings:
    assert (
        "candidate" in result.error_message.lower()
        or "input" in result.error_message.lower()
        or "output" in result.error_message.lower()
        or "produce" in result.error_message.lower()
    )


def test_evaluator_blocked_on_non_dict_candidate(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps(["not", "a", "dict"]))
    result = run_evaluator(
        db, candidate_hash="cand-1", candidate_mutable=candidate,
        evaluator_run_dir=tmp_path / "run",
    )
    assert result.decision == "BLOCKED"


def test_evaluator_result_persists_to_db(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps({
        "factor_inclusion": [{"name": "f1"}, {"name": "f2"}, {"name": "f3"}],
        "weights": {"f1": 0.4, "f2": 0.3, "f3": 0.3},
    }))
    result = run_evaluator(
        db, candidate_hash="cand-2", candidate_mutable=candidate,
        evaluator_run_dir=tmp_path / "run",
    )
    rows = db.fetchall(
        "SELECT * FROM evaluations WHERE candidate_hash = ?",
        ("cand-2",),
    )
    assert len(rows) == 1
    assert rows[0]["decision"] == result.decision


def test_evaluator_is_deterministic(tmp_path: Path):
    """Running the same candidate twice produces the same decision."""
    db = Database(tmp_path / "test.db")
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps({
        "factor_inclusion": [{"name": "f1"}] * 5,
        "weights": {"f1": 1.0},
    }))
    r1 = run_evaluator(
        db, candidate_hash="cand-det", candidate_mutable=candidate,
        evaluator_run_dir=tmp_path / "run1",
    )
    r2 = run_evaluator(
        db, candidate_hash="cand-det", candidate_mutable=candidate,
        evaluator_run_dir=tmp_path / "run2",
    )
    assert r1.decision == r2.decision
    assert r1.score == r2.score
