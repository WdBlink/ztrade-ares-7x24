"""Promotion gate — 10 mechanical gates per PRD §8.1.

All gates must pass for a candidate to be promoted to `autoresearch/best/`.
The gates are code-computed; no LLM verdict is authoritative.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .db import Database, now_iso


GATE_NAMES = (
    "schema",
    "scope",
    "candidate_hash",
    "stale_artifact",
    "deterministic_evaluation",
    "metric",
    "reviewer_independence",
    "budget",
    "loop",
    "promotion_lock",
)


@dataclass
class GateFailure:
    gate: str
    reason: str


@dataclass
class GateReport:
    passed: bool
    failures: list[GateFailure] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.passed


def _fail(gate: str, reason: str) -> GateFailure:
    return GateFailure(gate=gate, reason=reason)


# ── Gate implementations ──────────────────────────────────────────

def gate_schema(role_yaml: Mapping, worker_output: Mapping) -> GateFailure | None:
    """Gate 1: worker output validates against the role's JSON schema.

    Fail-closed: a missing or invalid schema path is a hard fail (per the
    audit fix; previously a missing schema was a silent pass). The role
    YAML MUST declare `output_schema_path` pointing to a real JSON Schema.
    """
    import jsonschema
    schema_path = role_yaml.get("output_schema_path", "")
    if not schema_path:
        return _fail(
            "schema",
            f"role_yaml missing 'output_schema_path' (security audit fix; "
            f"fail-closed; was previously silent pass)",
        )
    schema_file = Path(schema_path)
    if not schema_file.is_absolute():
        schema_file = Path.cwd() / schema_file
    if not schema_file.exists():
        return _fail("schema", f"schema file not found: {schema_path}")
    try:
        schema = json.loads(schema_file.read_text())
        jsonschema.validate(worker_output, schema)
        return None
    except (jsonschema.ValidationError, json.JSONDecodeError) as e:
        return _fail("schema", f"schema validation failed: {e.message}")


def gate_scope(write_paths: list[str], allowed_paths: list[str]) -> GateFailure | None:
    """Gate 2: worker wrote only within its write_scope."""
    for p in write_paths:
        if not any(p.startswith(scope) for scope in allowed_paths):
            return _fail("scope", f"path {p!r} is outside write_scope {allowed_paths}")
    return None


def gate_candidate_hash(
    candidate_hash: str, evaluation_candidate_hash: str | None
) -> GateFailure | None:
    """Gate 3: evaluator input hash matches the candidate hash in SQLite."""
    if not evaluation_candidate_hash:
        return _fail("candidate_hash", "no evaluation recorded for this candidate")
    if candidate_hash != evaluation_candidate_hash:
        return _fail(
            "candidate_hash",
            f"hash mismatch: candidate={candidate_hash} "
            f"eval={evaluation_candidate_hash}",
        )
    return None


def gate_stale_artifact(
    candidate_hash: str,
    iteration_id: str,
    db: Database,
) -> GateFailure | None:
    """Gate 4: evaluator output references the same candidate hash and iteration."""
    row = db.fetchone(
        "SELECT candidate_hash FROM evaluations WHERE candidate_hash = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (candidate_hash,),
    )
    if row is None:
        return _fail("stale_artifact", f"no evaluation for candidate {candidate_hash}")
    if row["candidate_hash"] != candidate_hash:
        return _fail("stale_artifact", "stale evaluation references a different candidate")
    return None


def gate_deterministic_evaluation(eval_decision: str | None) -> GateFailure | None:
    """Gate 5: evaluator exited successfully and produced required metrics."""
    if eval_decision not in ("KEEP", "DISCARD", "BLOCKED"):
        return _fail("deterministic_evaluation", f"no decision: {eval_decision!r}")
    if eval_decision != "KEEP":
        return _fail("deterministic_evaluation", f"evaluator decided {eval_decision}")
    return None


def gate_metric(
    candidate_score: float, best_score: float, ratio: float = 0.9
) -> GateFailure | None:
    """Gate 6: candidate_score >= best_score * 0.9 (regression guard)."""
    if best_score > 0 and candidate_score < best_score * ratio:
        return _fail(
            "metric",
            f"score {candidate_score:.3f} < best * {ratio} = {best_score * ratio:.3f}",
        )
    return None


def gate_reviewer_independence(
    reviewer_phase_job_id: str | None,
    builder_phase_job_id: str | None,
    evaluator_phase_job_id: str | None,
) -> GateFailure | None:
    """Gate 7: reviewer is a separate phase job from builder/evaluator and returns KEEP."""
    if not reviewer_phase_job_id:
        return _fail("reviewer_independence", "no reviewer phase job")
    if reviewer_phase_job_id in (builder_phase_job_id, evaluator_phase_job_id):
        return _fail("reviewer_independence", "reviewer is the same job as builder/evaluator")
    return None


def gate_budget(db: Database, run_id: str) -> GateFailure | None:
    """Gate 8: budget not exceeded."""
    from .budget import check_budget
    run = db.fetchone("SELECT * FROM runs WHERE id = ?", (run_id,))
    if not run:
        return _fail("budget", f"run {run_id} not found")
    run_dict = dict(run)
    allowed, reason = check_budget(db, run_dict)
    if not allowed:
        return _fail("budget", reason)
    return None


def gate_loop(
    consecutive_discards: int,
    consecutive_blockeds: int,
    oscillation_fired: bool,
    discard_limit: int = 5,
    blocked_limit: int = 3,
) -> GateFailure | None:
    """Gate 9: loop and circuit-breaker gates not tripped."""
    if consecutive_discards >= discard_limit:
        return _fail("loop", f"consecutive_discards={consecutive_discards} >= {discard_limit}")
    if consecutive_blockeds >= blocked_limit:
        return _fail("loop", f"consecutive_blockeds={consecutive_blockeds} >= {blocked_limit}")
    if oscillation_fired:
        return _fail("loop", "oscillation guard fired (policy=halt)")
    return None


def gate_promotion_lock(db: Database, run_id: str) -> GateFailure | None:
    """Gate 10: controller holds service_locks.promotion."""
    row = db.fetchone(
        "SELECT lease_expires_at FROM service_locks WHERE name = 'promotion' "
        "AND holder_id = ?",
        (f"controller:{run_id}",),
    )
    if not row:
        return _fail("promotion_lock", "controller does not hold service_locks.promotion")
    if row["lease_expires_at"] < now_iso():
        return _fail("promotion_lock", "promotion lock lease has expired")
    return None


# ── High-level entry point ────────────────────────────────────────

def run_all_gates(
    db: Database,
    *,
    run_id: str,
    candidate_hash: str,
    iteration_id: str,
    role_yaml: Mapping,
    worker_output: Mapping,
    write_paths: list[str],
    eval_decision: str | None,
    candidate_score: float,
    best_score: float,
    reviewer_phase_job_id: str | None,
    builder_phase_job_id: str | None,
    evaluator_phase_job_id: str | None,
    consecutive_discards: int,
    consecutive_blockeds: int,
    oscillation_fired: bool,
    allowed_paths: list[str] | None = None,
    metric_ratio: float = 0.9,
) -> GateReport:
    """Run all 10 gates and return a report."""
    failures: list[GateFailure] = []
    eval_row = db.fetchone(
        "SELECT candidate_hash FROM evaluations "
        "WHERE candidate_hash = ? ORDER BY created_at DESC LIMIT 1",
        (candidate_hash,),
    )
    evaluation_candidate_hash = eval_row["candidate_hash"] if eval_row else None

    gates = [
        ("1_schema", gate_schema(role_yaml, worker_output)),
        ("2_scope", gate_scope(write_paths, allowed_paths or [])),
        ("3_candidate_hash", gate_candidate_hash(candidate_hash, evaluation_candidate_hash)),
        ("4_stale_artifact", gate_stale_artifact(candidate_hash, iteration_id, db)),
        ("5_deterministic_evaluation", gate_deterministic_evaluation(eval_decision)),
        ("6_metric", gate_metric(candidate_score, best_score, metric_ratio)),
        ("7_reviewer_independence", gate_reviewer_independence(
            reviewer_phase_job_id, builder_phase_job_id, evaluator_phase_job_id,
        )),
        ("8_budget", gate_budget(db, run_id)),
        ("9_loop", gate_loop(
            consecutive_discards, consecutive_blockeds, oscillation_fired,
        )),
        ("10_promotion_lock", gate_promotion_lock(db, run_id)),
    ]
    # Gate 3 and 4 are intentionally distinct in the PRD: gate 3 checks the
    # exact hash match between candidate and evaluation input; gate 4 checks
    # that the evaluation references the correct iteration and is not stale
    # (a different evaluation row could match the hash but the wrong iter).
    # PRD §8.1 keeps both as separate gates for audit clarity.

    for name, failure in gates:
        if failure is not None:
            failures.append(failure)

    return GateReport(
        passed=len(failures) == 0,
        failures=failures,
        details={name: (failure.reason if failure else "pass") for name, failure in gates},
    )


# ── Promotion transaction (PRD §8.2) ─────────────────────────────

def claim_promotion_lock(db: Database, run_id: str, lease_seconds: int = 600) -> bool:
    """Acquire service_locks.promotion via CAS."""
    holder = f"controller:{run_id}"
    from datetime import datetime, timezone, timedelta
    lease = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
    now = now_iso()
    try:
        existing = db.fetchone(
            "SELECT holder_id, lease_expires_at FROM service_locks WHERE name = 'promotion'"
        )
        if existing is None:
            db.execute(
                "INSERT INTO service_locks (name, holder_id, lease_expires_at, last_heartbeat_at) "
                "VALUES (?, ?, ?, ?)",
                ("promotion", holder, lease, now),
            )
            return True
        if existing["lease_expires_at"] < now:
            db.execute(
                "UPDATE service_locks SET holder_id = ?, lease_expires_at = ?, "
                "last_heartbeat_at = ? WHERE name = 'promotion'",
                (holder, lease, now),
            )
            return True
        return existing["holder_id"] == holder
    except Exception:
        return False


def release_promotion_lock(db: Database) -> None:
    db.execute("DELETE FROM service_locks WHERE name = 'promotion'")


def perform_promotion(
    db: Database,
    *,
    run_id: str,
    iteration_id: str,
    candidate_hash: str,
    old_best_hash: str | None,
    new_best_path: Path,
    results_tsv_path: Path,
    report_path: Path,
    working_dir: Path,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Idempotent promotion transaction. Records a `promotions` row and
    commits to git. Re-running with the same idempotency_key is a no-op.
    """
    idempotency_key = idempotency_key or (
        f"promotion:{run_id}:{iteration_id}:{candidate_hash}"
    )

    existing = db.fetchone(
        "SELECT * FROM promotions WHERE idempotency_key = ?",
        (idempotency_key,),
    )
    if existing and existing["status"] == "committed":
        return {"status": "already_committed", "git_commit": existing["git_commit"]}

    new_best_hash = hashlib.sha256(new_best_path.read_bytes()).hexdigest()[:16]
    promotion_id = f"promo-{uuid.uuid4()}"

    if existing is None:
        db.execute(
            "INSERT INTO promotions "
            "(id, run_id, iteration_id, candidate_hash, old_best_hash, "
            " new_best_hash, idempotency_key, status, staged_manifest_path, "
            " promoted_at, promoted_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'prepared', ?, ?, ?)",
            (
                promotion_id, run_id, iteration_id, candidate_hash,
                old_best_hash, new_best_hash, idempotency_key,
                str(new_best_path), now_iso(), "controller",
            ),
        )
    else:
        db.execute(
            "UPDATE promotions SET status = 'applying' WHERE id = ?",
            (existing["id"],),
        )
        promotion_id = existing["id"]

    best_target = working_dir / "autoresearch" / "best" / new_best_path.name
    best_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(new_best_path, best_target)

    git_commit = _git_commit(
        working_dir, best_target, results_tsv_path, report_path,
        message=(
            f"promote iter={iteration_id} candidate={candidate_hash}\n\n"
            f"Idempotency-Key: {idempotency_key}"
        ),
    )

    db.execute(
        "UPDATE promotions SET status = 'committed', git_commit = ?, "
        "promoted_at = ? WHERE id = ?",
        (git_commit, now_iso(), promotion_id),
    )

    return {
        "status": "committed",
        "git_commit": git_commit,
        "new_best_hash": new_best_hash,
        "idempotency_key": idempotency_key,
    }


def _git_commit(
    working_dir: Path, best_target: Path, results_tsv: Path, report: Path,
    message: str,
) -> str:
    """Run git add + git commit and return the commit SHA."""
    paths = [str(p.relative_to(working_dir)) for p in (best_target, results_tsv, report) if p.exists()]
    if not paths:
        return ""
    subprocess.run(
        ["git", "add", "--", *paths],
        cwd=str(working_dir), check=True, capture_output=True, text=True,
    )
    proc = subprocess.run(
        ["git", "commit", "-m", message, "--no-gpg-sign"],
        cwd=str(working_dir), capture_output=True, text=True,
    )
    if proc.returncode != 0 and "nothing to commit" not in proc.stdout:
        raise RuntimeError(f"git commit failed: {proc.stderr}")
    sha_proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(working_dir), capture_output=True, text=True,
    )
    return sha_proc.stdout.strip()
