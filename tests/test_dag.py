"""Unit tests for ar724.dag (DAG topology, Vibe-Trading port).

PRD §6.3. Phase 11 acceptance: a 6-task DAG with one parallel layer is
correctly layered; a DAG with a cycle raises ValueError.
"""

from __future__ import annotations

import pytest

from ar724.dag import Task, topological_layers, validate_dag


def test_topological_layers_4_role_dag():
    """The 4-role + evaluator + reviewer + promoter DAG is layered correctly."""
    tasks = [
        Task(id="proposer", depends_on=[]),
        Task(id="builder", depends_on=["proposer"]),
        Task(id="validator", depends_on=["builder"]),
        Task(id="evaluator", depends_on=["validator"]),
        Task(id="reviewer", depends_on=["evaluator"]),
        Task(id="promoter", depends_on=["reviewer"]),
    ]
    layers = topological_layers(tasks)
    # Each layer is exactly one task in V1.0's serialized 4-role layout.
    assert layers == [
        ["proposer"],
        ["builder"],
        ["validator"],
        ["evaluator"],
        ["reviewer"],
        ["promoter"],
    ]


def test_topological_layers_with_parallel_layer():
    """A DAG with one parallel layer is correctly handled."""
    tasks = [
        Task(id="A", depends_on=[]),
        Task(id="B1", depends_on=["A"]),
        Task(id="B2", depends_on=["A"]),
        Task(id="C", depends_on=["B1", "B2"]),
    ]
    layers = topological_layers(tasks)
    assert layers[0] == ["A"]
    # B1 and B2 are in the same layer; order within is unspecified
    assert sorted(layers[1]) == ["B1", "B2"]
    assert layers[2] == ["C"]


def test_validate_dag_detects_cycle():
    """A DAG with a cycle raises ValueError with the cycle path."""
    tasks = [
        Task(id="A", depends_on=["B"]),
        Task(id="B", depends_on=["A"]),
    ]
    with pytest.raises(ValueError, match="[Cc]ycle"):
        validate_dag(tasks)


def test_validate_dag_detects_unknown_dependency():
    """A task depending on an unknown id raises ValueError."""
    tasks = [Task(id="A", depends_on=["nonexistent"])]
    with pytest.raises(ValueError, match="unknown task"):
        validate_dag(tasks)


def test_validate_dag_accepts_acyclic():
    """A linear acyclic DAG passes validation."""
    tasks = [
        Task(id="A", depends_on=[]),
        Task(id="B", depends_on=["A"]),
        Task(id="C", depends_on=["B"]),
    ]
    validate_dag(tasks)  # should not raise


def test_topological_layers_empty():
    """Empty task list returns empty layers."""
    assert topological_layers([]) == []


def test_topological_layers_single_node():
    """A single-node DAG returns [[node]]."""
    assert topological_layers([Task(id="solo")]) == [["solo"]]
