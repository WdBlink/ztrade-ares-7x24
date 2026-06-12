# ztrade-ares-7x24

A 7×24 autonomous research controller for the ztrade autoresearch protocol.
A Python daemon owns the iterative strategy-and-factor research loop, launches
bounded worker agent sessions through a tmux-based operator console, evaluates
candidates with a deterministic Python evaluator, and mechanically promotes
only evaluator-backed winners.

## Architecture (one-pager)

```
┌─────────────────────────────────────────────────────────────────┐
│ L0: OS-level supervisor (macOS launchd KeepAlive + 1-min cron)  │
└─────────────────────────────────────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│ L1: Controller (Python daemon) — owns state.db, scheduler,     │
│     leases, budget, 10-gate promotion. Single authority.         │
└─────────────────────────────────────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│ L2: Spawner — ClawTeam-style tmux new-window + env-file         │
│     injection; DAG layer dispatch; CLI adapter registry.        │
└─────────────────────────────────────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│ L3: Worker sessions (1 tmux session `ar7x24-{run_id}`)         │
│     4 fixed roles: factor_combiner, backtester, factor_validator│
│     backtest_reviewer. Plus monitor + operator windows.         │
└─────────────────────────────────────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│ L4: Persistence — state.db (SQLite WAL, source of truth),      │
│     events.jsonl (live callback side-channel), git (best/+report│
└─────────────────────────────────────────────────────────────────┘
```

## Quickstart

```bash
# Install (in a fresh venv)
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .

# Initialize
ar724 init

# Validate your role YAMLs
ar724 validate-roles

# Preview a single iteration (dry-run, no worker spawn)
ar724 iter dry-run

# Start the system (installs launchd plist + creates tmux session)
./bin/ares-up.sh "$(pwd)"

# Watch progress
ar724 status
ar724 board

# Attach to the tmux console
tmux attach -t ar7x24-$(ar724 status --format run_id 2>/dev/null || echo unknown)
```

## Roles

Four fixed roles (PRD §7.3) — defined in `autoresearch/v2/roles/*.yaml`:

| Role | Writes | Default model |
|---|---|---|
| `factor_combiner` | `autoresearch/candidates/<hash>/proposal.json` | sonnet_4_5 |
| `backtester` | `autoresearch/mutable/v47_params.json` (only role) | sonnet_4_5 |
| `factor_validator` | `autoresearch/candidates/<hash>/validation.json` | opus_4_7 |
| `backtest_reviewer` | `autoresearch/candidates/<hash>/review.json` | opus_4_7 |

Plus a deterministic Python `evaluator_runner` (no LLM) that returns
`KEEP | DISCARD | BLOCKED`.

## 10 mechanical promotion gates (PRD §8.1)

A candidate is promoted to `autoresearch/best/` only if **all 10** pass:

1. Schema validation (worker output matches role schema)
2. Scope check (worker wrote only within its `write_scope`)
3. Candidate hash matches evaluation input
4. No stale artifact (eval references the same candidate hash + iteration)
5. Deterministic evaluator decided `KEEP`
6. Metric gate: `candidate_score >= best_score * 0.9` (regression guard)
7. Reviewer independence: separate phase job from builder/evaluator
8. Budget gate: not over `runs.budget_cents` or daily cap
9. Loop gate: `consecutive_discards < 5` AND `consecutive_blockeds < 3` AND no oscillation halt
10. Promotion lock: controller holds `service_locks.promotion`

## Observability (PRD §13)

Four pillars, all implemented:

- **Traces** — `trace_id` (UUID) flows through every phase of an iteration.
- **Metrics** — 11 counters/gauges (e.g. `iterations.completed.last_24h`).
- **Logs** — `events` table with controlled `event_type` vocabulary (§13.3).
- **Evals** — separate `eval_results` table; `ar724 eval run-all` for the suite.

## Safety (PRD §15)

- Path validators: rejects traversal outside project root.
- SSRF protection: private IP ranges (10/8, 172.16/12, 192.168/16, 127/8) blocked in Bash.
- Shell classifier: `network_or_destructive` commands denied; `git commit` is controller-only.
- MCP allowlist: only approved servers/tools; mutations require `approvals` table.
- Untrusted input: MCP outputs, market data, prior reports, evaluator outputs are
  treated as untrusted; embedded instructions are ignored.

## Runbooks (PRD §15.4)

10 incident-response runbooks in `ar724/runbooks/`:

1. `01-pause-and-resume.md`
2. `02-rollback-last-promotion.md`
3. `03-disable-mcp-server.md`
4. `04-rotate-secrets.md`
5. `05-replay-evaluator.md`
6. `06-inspect-cost-spike.md`
7. `07-recover-orphaned-jobs.md`
8. `08-restore-sqlite-snapshot.md`
9. `09-extend-conductor-leases.md`
10. `10-vacation-handoff.md`

## License and attribution

This project is MIT-licensed. It imports source code (with attribution) from:

- **HKUDS/Vibe-Trading** (MIT) — `ar724/dag.py`, `ar724/db.py`,
  `ar724/heartbeat.py`, `ar724/role_loader.py` (scoped subset).
- **HKUDS/ClawTeam** — design pattern inspiration for tmux runtime
  (state.json + keepalive shell + exit-journal architecture). No code copied.
- **iamtouchskyer/OPC** — design pattern inspiration for the L3
  oscillation detector and per-role acceptance criteria. No code copied.

See PRD §20 for the full attribution log.

## Development

```bash
# Run tests
pytest tests/ -v

# Lint
ruff check ar724/

# Validate role YAMLs
ar724 validate-roles
```

## Non-goals (PRD §2.2, §2.3)

- Live trading execution
- Multi-tenant SaaS
- Cross-machine transport / horizontal scaling
- 11-role OPC-style committee
- Web UI (tmux IS the monitor)
- 1M-context caching layer
- Autonomous Skill self-modification
