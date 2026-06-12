"""Unit tests for ar724.role_loader.

PRD §7.3, §7.4, §9.2. Phase 9 acceptance:
  - 4 role YAMLs load at startup.
  - validate_roles rejects a YAML missing a required field.
  - check_acceptance rejects an output that fails a criterion.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ar724.role_loader import (
    REQUIRED_FIELDS, REQUIRED_ROLE_IDS, check_acceptance, validate_roles,
)


def test_validate_roles_accepts_shipped_yaml(tmp_path: Path):
    """The 4 shipped role YAMLs in autoresearch/v2/roles/ validate cleanly."""
    roles_dir = Path(__file__).resolve().parent.parent / "autoresearch" / "v2" / "roles"
    roles = validate_roles(roles_dir)
    assert len(roles) == 4
    assert {r["id"] for r in roles} == REQUIRED_ROLE_IDS


def test_validate_roles_rejects_missing_field(tmp_path: Path):
    """A YAML missing a required field is rejected."""
    (tmp_path / "factor_combiner.yaml").write_text(
        "id: factor_combiner\ntitle: x\n"
    )
    (tmp_path / "backtester.yaml").write_text(
        "id: backtester\ntitle: x\n"
    )
    (tmp_path / "factor_validator.yaml").write_text(
        "id: factor_validator\ntitle: x\n"
    )
    (tmp_path / "backtest_reviewer.yaml").write_text(
        "id: backtest_reviewer\ntitle: x\n"
    )
    with pytest.raises(ValueError, match="missing required fields"):
        validate_roles(tmp_path)


def test_validate_roles_rejects_missing_yaml(tmp_path: Path):
    """A roles dir missing one of the 4 required YAMLs is rejected."""
    (tmp_path / "factor_combiner.yaml").write_text(
        "id: factor_combiner\ntitle: x\ndescription: x\n"
        "system_prompt: x\ntools: []\nskills: []\nacceptance: []\n"
    )
    with pytest.raises(ValueError, match="Expected role ids"):
        validate_roles(tmp_path)


def test_validate_roles_rejects_duplicate_id(tmp_path: Path):
    """Two YAMLs with the same id is rejected."""
    (tmp_path / "factor_combiner.yaml").write_text(
        "id: factor_combiner\ntitle: x\ndescription: x\n"
        "system_prompt: x\ntools: []\nskills: []\nacceptance: []\n"
    )
    (tmp_path / "backtester.yaml").write_text(
        "id: backtester\ntitle: x\ndescription: x\n"
        "system_prompt: x\ntools: []\nskills: []\nacceptance: []\n"
    )
    (tmp_path / "factor_validator.yaml").write_text(
        "id: factor_validator\ntitle: x\ndescription: x\n"
        "system_prompt: x\ntools: []\nskills: []\nacceptance: []\n"
    )
    (tmp_path / "backtest_reviewer.yaml").write_text(
        "id: backtest_reviewer\ntitle: x\ndescription: x\n"
        "system_prompt: x\ntools: []\nskills: []\nacceptance: []\n"
    )
    (tmp_path / "duplicate.yaml").write_text(
        "id: factor_combiner\ntitle: x\ndescription: x\n"
        "system_prompt: x\ntools: []\nskills: []\nacceptance: []\n"
    )
    with pytest.raises(ValueError, match="Duplicate role id"):
        validate_roles(tmp_path)


def test_check_acceptance_passes_valid_factor_combiner_output():
    """A factor_combiner output with 3+ factors and weights summing to 1 passes."""
    role_yaml = {
        "id": "factor_combiner",
        "acceptance": [
            "factor_combiner.min_3_factors",
            "factor_combiner.correlation_matrix_present",
            "factor_combiner.weights_sum_to_one",
        ],
    }
    output = {
        "factor_inclusion": [{"name": "f1"}, {"name": "f2"}, {"name": "f3"}],
        "weights": {"f1": 0.4, "f2": 0.3, "f3": 0.3},
        "factor_correlation_matrix_summary": "low correlation",
        "rationale": "...",
    }
    passed, failed = check_acceptance(role_yaml, output)
    assert passed, f"unexpected failures: {failed}"
    assert failed == []


def test_check_acceptance_fails_on_too_few_factors():
    """An output with only 2 factors fails the min_3_factors check."""
    role_yaml = {
        "id": "factor_combiner",
        "acceptance": ["factor_combiner.min_3_factors"],
    }
    output = {
        "factor_inclusion": [{"name": "f1"}, {"name": "f2"}],
    }
    passed, failed = check_acceptance(role_yaml, output)
    assert not passed
    assert "factor_combiner.min_3_factors" in failed


def test_check_acceptance_fails_on_weights_not_summing_to_one():
    """An output with weights summing to 1.5 fails the weights_sum_to_one check."""
    role_yaml = {
        "id": "factor_combiner",
        "acceptance": ["factor_combiner.weights_sum_to_one"],
    }
    output = {"weights": {"f1": 1.0, "f2": 0.5}}
    passed, failed = check_acceptance(role_yaml, output)
    assert not passed
    assert "factor_combiner.weights_sum_to_one" in failed


def test_check_acceptance_unknown_criterion_is_warning_not_failure():
    """An unknown acceptance criterion is skipped, not failed."""
    role_yaml = {
        "id": "factor_combiner",
        "acceptance": ["unknown.criterion"],
    }
    passed, failed = check_acceptance(role_yaml, {})
    assert passed  # unknown criteria are warnings, not failures
    assert failed == []


def test_check_acceptance_reviewer_verdict_known():
    """backtest_reviewer verdict must be in the controlled set."""
    role_yaml = {
        "id": "backtest_reviewer",
        "acceptance": ["backtest_reviewer.verdict_known"],
    }
    passed_ok, _ = check_acceptance(role_yaml, {"verdict": "KEEP"})
    passed_veto, _ = check_acceptance(role_yaml, {"verdict": "VETO"})
    passed_garbage, failed = check_acceptance(role_yaml, {"verdict": "MAYBE"})
    assert passed_ok and passed_veto
    assert not passed_garbage
    assert "backtest_reviewer.verdict_known" in failed
