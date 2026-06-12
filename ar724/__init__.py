"""ztrade-ares 7×24 autonomous research controller.

A controller daemon that owns the iterative strategy-and-factor research loop,
launches bounded worker agent sessions through a tmux-based operator console,
evaluates candidates with a deterministic Python evaluator, and mechanically
promotes only evaluator-backed winners.

See PRD: docs/superpowers/specs/2026-06-12-autoresearch-7x24-tmux-controller-design-v1.md
"""

__version__ = "0.1.0"
