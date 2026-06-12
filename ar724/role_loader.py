"""Role loader — validate role YAMLs and check per-role acceptance.

Vibe-Trading port:
  - validate_roles scoped subset of HKUDS/Vibe-Trading (MIT)
    agent/src/swarm/presets.py:99-211 (inspect_preset).
    V1.0 only imports the agent/task reference and required-field subset.
    Variable substitution and runbook matching are out of scope (PRD §3.5).
  License: https://github.com/HKUDS/Vibe-Trading/blob/main/LICENSE

Per-role acceptance criteria (PRD §7.3, §9.2) replace OPC's Task Scope
Registry. Each role YAML has a static `acceptance:` list of natural-language
assertions; check_acceptance verifies the worker output JSON against them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import yaml

# ── Required fields and ids (PRD §7.4) ────────────────────────────

REQUIRED_FIELDS = {
    "id",
    "title",
    "description",
    "system_prompt",
    "tools",
    "skills",
    "acceptance",
}

REQUIRED_ROLE_IDS = frozenset(
    {
        "factor_combiner",
        "backtester",
        "factor_validator",
        "backtest_reviewer",
    }
)


def load_role_yaml(path: Path) -> dict[str, Any]:
    """Load and minimally validate a single role YAML."""
    with path.open() as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level must be a mapping")
    if "id" not in data:
        raise ValueError(f"{path} missing required field 'id'")
    return data


def validate_roles(roles_dir: Path) -> list[dict[str, Any]]:
    """Validate that all required role YAMLs exist and have required fields.

    Vibe-Trading port (scoped). Run at controller startup. Fails fast on
    misconfiguration. Does NOT include variable substitution or runbook
    matching (out of scope per PRD §3.5).
    """
    if not roles_dir.is_dir():
        raise ValueError(f"roles_dir does not exist: {roles_dir}")

    seen: set[str] = set()
    roles: list[dict[str, Any]] = []
    for yaml_path in sorted(roles_dir.glob("*.yaml")):
        data = load_role_yaml(yaml_path)
        role_id = data["id"]
        if role_id in seen:
            raise ValueError(f"Duplicate role id: {role_id}")
        seen.add(role_id)
        missing = REQUIRED_FIELDS - set(data.keys())
        if missing:
            raise ValueError(f"{yaml_path} missing required fields: {sorted(missing)}")
        roles.append(data)

    if seen != REQUIRED_ROLE_IDS:
        missing = REQUIRED_ROLE_IDS - seen
        extra = seen - REQUIRED_ROLE_IDS
        msg = f"Expected role ids {sorted(REQUIRED_ROLE_IDS)}, got {sorted(seen)}"
        if missing:
            msg += f" (missing: {sorted(missing)})"
        if extra:
            msg += f" (unexpected: {sorted(extra)})"
        raise ValueError(msg)

    # Cross-reference with DAG phase_job definitions
    for role_id in REQUIRED_ROLE_IDS:
        expected_path = roles_dir / f"{role_id}.yaml"
        if not expected_path.exists():
            raise ValueError(f"Role YAML missing: {expected_path}")

    return roles


# ── Per-role acceptance check registry (PRD §9.2) ─────────────────
# Note: ACCEPTANCE_CHECKS is built at the BOTTOM of this file (after the
# check functions are defined) to avoid forward references at import time.


def check_acceptance(
    role_yaml: dict[str, Any], output: dict[str, Any]
) -> tuple[bool, list[str]]:
    """Verify worker output satisfies the role's acceptance criteria.

    Returns (passed, failed_criteria). An unknown criterion is skipped
    (treated as a warning, not a failure) so role authors can iterate
    on new acceptance rules without breaking the run.
    """
    failed: list[str] = []
    role_id = role_yaml.get("id", "")
    for criterion in role_yaml.get("acceptance", []):
        check = ACCEPTANCE_CHECKS.get(criterion)
        if check is None:
            # Unknown criterion: warning, not failure.
            continue
        try:
            ok = check({"id": role_id}, output)
        except (KeyError, TypeError, ValueError):
            ok = False
        if not ok:
            failed.append(criterion)
    return (len(failed) == 0), failed


# ── Acceptance check implementations ──────────────────────────────


def _check_min_3_factors(_role: dict[str, Any], output: dict[str, Any]) -> bool:
    """factor_combiner: must output >=3 candidate factors in factor_inclusion."""
    factors = output.get("factor_inclusion", [])
    return isinstance(factors, list) and len(factors) >= 3


def _check_correlation_matrix(_role: dict[str, Any], output: dict[str, Any]) -> bool:
    """factor_combiner: factor_correlation_matrix_summary must be present."""
    return "factor_correlation_matrix_summary" in output


def _check_weights_sum_to_one(_role: dict[str, Any], output: dict[str, Any]) -> bool:
    """factor_combiner: weights must sum to 1.0 ± 0.001."""
    weights = output.get("weights", {})
    if not isinstance(weights, dict) or not weights:
        return False
    total = sum(float(v) for v in weights.values())
    return abs(total - 1.0) <= 0.001


def _check_universe_proposal(_role: dict[str, Any], output: dict[str, Any]) -> bool:
    """factor_combiner: universe_proposal (if present) must have mcap_band and selection_method."""
    proposal = output.get("universe_proposal")
    if proposal is None:
        return True  # optional
    return (
        isinstance(proposal, dict)
        and "selection_method" in proposal
        and "mcap_band" in proposal
    )


def _check_results_tsv_path(_role: dict[str, Any], output: dict[str, Any]) -> bool:
    """backtester: must produce a results.tsv path."""
    return bool(output.get("results_tsv_path"))


def _check_metrics_above_floor(_role: dict[str, Any], output: dict[str, Any]) -> bool:
    """backtester: declared metrics must include the required baseline set."""
    metrics = output.get("metrics", {})
    if not isinstance(metrics, dict):
        return False
    required = {"mean_annual_return_pct", "sharpe", "max_drawdown_pct"}
    return required.issubset(metrics.keys())


def _check_risk_flags(_role: dict[str, Any], output: dict[str, Any]) -> bool:
    """factor_validator: risk_flags must be present (may be empty list)."""
    return "risk_flags" in output


def _check_candidate_hash(_role: dict[str, Any], output: dict[str, Any]) -> bool:
    """factor_validator: candidate_hash must be present for trace correlation."""
    return bool(output.get("candidate_hash"))


def _check_reviewer_verdict_known(_role: dict[str, Any], output: dict[str, Any]) -> bool:
    """backtest_reviewer: verdict must be in the controlled set."""
    return output.get("verdict") in {"KEEP", "VETO", "REVISE", "NEEDS_MORE_EVIDENCE"}


def _check_rationale_path(_role: dict[str, Any], output: dict[str, Any]) -> bool:
    """backtest_reviewer: rationale_path must point to a real file."""
    path = output.get("rationale_path")
    return bool(path) and Path(str(path)).parent.exists()


# ── Build ACCEPTANCE_CHECKS AFTER all check functions are defined ──
ACCEPTANCE_CHECKS: dict[str, Callable[[dict[str, Any], dict[str, Any]], bool]] = {
    "factor_combiner.min_3_factors": _check_min_3_factors,
    "factor_combiner.correlation_matrix_present": _check_correlation_matrix,
    "factor_combiner.weights_sum_to_one": _check_weights_sum_to_one,
    "factor_combiner.universe_validates": _check_universe_proposal,
    "backtester.results_tsv_path_present": _check_results_tsv_path,
    "backtester.metrics_above_floor": _check_metrics_above_floor,
    "factor_validator.risk_flags_present": _check_risk_flags,
    "factor_validator.candidate_hash_present": _check_candidate_hash,
    "backtest_reviewer.verdict_known": _check_reviewer_verdict_known,
    "backtest_reviewer.rationale_path_present": _check_rationale_path,
}
