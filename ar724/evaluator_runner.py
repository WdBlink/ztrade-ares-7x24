"""Deterministic evaluator subprocess.

PRD §4 L2. The evaluator is the SOLE authority for KEEP/DISCARD
decisions (PRD §8.1 gate 5). It runs as a local Python subprocess; no LLM.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import Database, atomic_write, now_iso


@dataclass
class EvaluatorResult:
    candidate_hash: str
    decision: str  # 'KEEP' | 'DISCARD' | 'BLOCKED'
    score: float
    metrics: dict[str, Any]
    evaluator_run_dir: Path
    error_message: str = ""


def _write_inputs(
    candidate_mutable: Path,
    evaluator_run_dir: Path,
) -> dict[str, Path]:
    """Stage the candidate mutable file into the evaluator run dir."""
    evaluator_run_dir.mkdir(parents=True, exist_ok=True)
    staged = evaluator_run_dir / "candidate.json"
    if candidate_mutable.exists():
        staged.write_text(candidate_mutable.read_text())
    return {"candidate": staged}


def _build_default_evaluator_script(
    evaluator_run_dir: Path,
    candidate_path: Path,
    metrics_out: Path,
    status_out: Path,
    score_floor: float = 0.9,
) -> str:
    """Return a Python script that loads the candidate, scores it, writes
    run_status.json and metrics.json. This is a stub evaluator used for
    V1.0; in production, the operator replaces this with a real
    `evaluator_runner.py` from the ztrade autoresearch repo.

    The stub:
      - Loads candidate JSON.
      - Computes a synthetic score from candidate factor count.
      - Marks KEEP if score >= score_floor * 1.0, DISCARD otherwise.
    """
    return f"""#!/usr/bin/env python3
import json, sys, hashlib
from pathlib import Path

candidate_path = Path({str(candidate_path)!r})
metrics_out = Path({str(metrics_out)!r})
status_out = Path({str(status_out)!r})
score_floor = {float(score_floor)}

try:
    data = json.loads(candidate_path.read_text())
except Exception as e:
    status_out.write_text(json.dumps({{"status": "BLOCKED", "error": str(e)}}))
    sys.exit(2)

if not isinstance(data, dict):
    status_out.write_text(json.dumps({{"status": "BLOCKED", "error": "candidate not a dict"}}))
    sys.exit(2)

factors = data.get("factor_inclusion", data.get("factors", []))
if not isinstance(factors, list):
    factors = []
score = min(1.0, 0.1 * len(factors) + 0.5)  # synthetic: 0.5 baseline, +0.1 per factor
metrics = {{
    "score": score,
    "factor_count": len(factors),
    "weights_sum": sum(float(v) for v in (data.get("weights", {{}}) or {{}}).values()),
    "candidate_hash": hashlib.sha256(candidate_path.read_bytes()).hexdigest()[:16],
}}
metrics_out.write_text(json.dumps(metrics, indent=2))
decision = "KEEP" if score >= score_floor else "DISCARD"
status_out.write_text(json.dumps({{"status": "DECIDED", "decision": decision, "score": score}}))
sys.exit(0)
"""


def run_evaluator(
    db: Database,
    candidate_hash: str,
    candidate_mutable: Path,
    *,
    evaluator_script: Path | None = None,
    evaluator_run_dir: Path | None = None,
    score_floor: float = 0.9,
    wall_clock_limit_seconds: int = 90 * 60,
    trace_id: str = "",
) -> EvaluatorResult:
    """Run the deterministic evaluator and record the result.

    `evaluator_script` defaults to the built-in stub. In production, the
    operator points this at the real ztrade autoresearch evaluator.
    """
    run_dir = evaluator_run_dir or (
        Path(".ares/evaluator_runs") / candidate_hash
    )
    paths = _write_inputs(candidate_mutable, run_dir)
    metrics_out = run_dir / "metrics.json"
    status_out = run_dir / "run_status.json"

    if evaluator_script is None:
        script_path = run_dir / "evaluator.py"
        script_path.write_text(_build_default_evaluator_script(
            run_dir, paths["candidate"], metrics_out, status_out, score_floor,
        ))
    else:
        script_path = evaluator_script

    started = time.monotonic()
    try:
        proc = subprocess.run(
            ["python3", str(script_path)],
            capture_output=True, text=True,
            timeout=wall_clock_limit_seconds,
        )
    except subprocess.TimeoutExpired:
        return _record_evaluator(
            db, candidate_hash, run_dir, decision="BLOCKED", score=0.0,
            metrics={}, error_message=f"evaluator timeout after {wall_clock_limit_seconds}s",
        )
    elapsed = time.monotonic() - started

    if not metrics_out.exists() or not status_out.exists():
        return _record_evaluator(
            db, candidate_hash, run_dir, decision="BLOCKED", score=0.0,
            metrics={}, error_message=f"evaluator did not produce outputs (rc={proc.returncode})",
        )

    status = json.loads(status_out.read_text())
    metrics = json.loads(metrics_out.read_text()) if metrics_out.exists() else {}
    decision = status.get("decision", "DISCARD")
    if status.get("status") == "BLOCKED":
        decision = "BLOCKED"
    return _record_evaluator(
        db, candidate_hash, run_dir,
        decision=decision,
        score=float(metrics.get("score", 0.0)),
        metrics=metrics,
        error_message=(
            f"elapsed={elapsed:.1f}s rc={proc.returncode} "
            f"stderr={proc.stderr[:200]!r}"
            if proc.returncode != 0
            else ""
        ),
        trace_id=trace_id,
    )


def _record_evaluator(
    db: Database,
    candidate_hash: str,
    run_dir: Path,
    *,
    decision: str,
    score: float,
    metrics: dict[str, Any],
    error_message: str = "",
    trace_id: str = "",
) -> EvaluatorResult:
    import uuid
    eval_id = f"eval-{uuid.uuid4()}"
    db.execute(
        "INSERT INTO evaluations "
        "(id, candidate_hash, evaluator_run_dir, run_status_path, "
        " metrics_json_path, decision, score, error_message, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            eval_id, candidate_hash, str(run_dir),
            str(run_dir / "run_status.json"),
            str(run_dir / "metrics.json"),
            decision, float(score), error_message, now_iso(),
        ),
    )
    return EvaluatorResult(
        candidate_hash=candidate_hash,
        decision=decision,
        score=float(score),
        metrics=metrics,
        evaluator_run_dir=run_dir,
        error_message=error_message,
    )
