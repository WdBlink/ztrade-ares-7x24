"""DAG topology — validate_dag, topological_layers, resolve_dependencies.

Vibe-Trading port:
  - validate_dag, topological_layers adapted from
    HKUDS/Vibe-Trading (MIT) agent/src/swarm/task_store.py:113-247
  - resolve_dependencies adapted from task_store.py:113-147

License: https://github.com/HKUDS/Vibe-Trading/blob/main/LICENSE
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field


@dataclass
class Task:
    """A unit of work in the iteration DAG.

    `id` is the unique task identifier (e.g. "proposer", "builder").
    `depends_on` lists the ids that must complete before this task starts.
    """

    id: str
    depends_on: list[str] = field(default_factory=list)
    layer: int = 0  # assigned by topological_layers


def resolve_dependencies(tasks: list[Task]) -> dict[str, list[str]]:
    """Resolve a task list into a {id: [deps]} graph; raise on unknown refs.

    Vibe-Trading port (MIT). Original: agent/src/swarm/task_store.py:113-147.
    """
    all_ids = {t.id for t in tasks}
    graph: dict[str, list[str]] = {}
    for t in tasks:
        for dep in t.depends_on:
            if dep not in all_ids:
                raise ValueError(
                    f"Task '{t.id}' depends on unknown task '{dep}'"
                )
        graph[t.id] = list(t.depends_on)
    return graph


def validate_dag(tasks: list[Task]) -> None:
    """DFS cycle detection. Raises ValueError with cycle path on detection.

    Vibe-Trading port (MIT). Original: agent/src/swarm/task_store.py:150-200.
    """
    all_ids = {t.id for t in tasks}
    if not all_ids:
        return
    graph = {t.id: list(t.depends_on) for t in tasks}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {tid: WHITE for tid in all_ids}
    path: list[str] = []

    def dfs(node: str) -> None:
        color[node] = GRAY
        path.append(node)
        for neighbor in graph.get(node, []):
            if neighbor not in all_ids:
                raise ValueError(
                    f"Task '{node}' depends on unknown task '{neighbor}'"
                )
            if color[neighbor] == GRAY:
                cycle_start = path.index(neighbor)
                cycle = path[cycle_start:] + [neighbor]
                raise ValueError(
                    f"Cycle detected: {' -> '.join(cycle)}"
                )
            if color[neighbor] == WHITE:
                dfs(neighbor)
        path.pop()
        color[node] = BLACK

    for tid in all_ids:
        if color[tid] == WHITE:
            dfs(tid)


def topological_layers(tasks: list[Task]) -> list[list[str]]:
    """Kahn's algorithm. Returns layers in execution order; each layer is parallel.

    Vibe-Trading port (MIT). Original: agent/src/swarm/task_store.py:203-247.

    Each returned layer contains task ids that can run in parallel given that
    all earlier layers have completed. Within a layer, order is unspecified.
    """
    validate_dag(tasks)
    in_degree: dict[str, int] = {t.id: 0 for t in tasks}
    dependents: dict[str, list[str]] = defaultdict(list)
    for t in tasks:
        in_degree[t.id] = len(t.depends_on)
        for dep in t.depends_on:
            dependents[dep].append(t.id)

    queue: deque[str] = deque(
        tid for tid, deg in in_degree.items() if deg == 0
    )
    layers: list[list[str]] = []
    while queue:
        layer = list(queue)
        queue.clear()
        layers.append(layer)
        for tid in layer:
            for downstream in dependents[tid]:
                in_degree[downstream] -= 1
                if in_degree[downstream] == 0:
                    queue.append(downstream)

    if sum(len(l) for l in layers) != len(tasks):
        raise ValueError(
            f"DAG has cycle: processed {sum(len(l) for l in layers)}/{len(tasks)}"
        )
    return layers


def annotate_layers(tasks: list[Task]) -> list[Task]:
    """Set `task.layer` on each task in-place per topological_layers result.

    Returns the same list for chaining.
    """
    layers = topological_layers(tasks)
    for layer_idx, layer in enumerate(layers):
        for tid in layer:
            for t in tasks:
                if t.id == tid:
                    t.layer = layer_idx
                    break
    return tasks
