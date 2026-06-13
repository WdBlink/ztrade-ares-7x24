# ztrade-ares-7x24

A 7×24 autonomous research controller for the [ztrade](https://github.com/WdBlink)
autoresearch protocol. A Python daemon owns the iterative strategy-and-factor
research loop, launches bounded worker agent sessions through a tmux-based
operator console, evaluates candidates with a deterministic Python evaluator,
and mechanically promotes only evaluator-backed winners.

This **single repo** contains both the controller daemon and the Skill
entrypoint — install once, use the CLI directly or invoke the Skill from
Claude Code / Codex / OpenCode.

---

## Quickstart

### Install the controller

```bash
git clone https://github.com/WdBlink/ztrade-ares-7x24
cd ztrade-ares-7x24
python3.11 -m venv .venv
.venv/bin/pip install -e .
ar724 --version
```

### Install the Skill (optional — for Claude Code / Codex / OpenCode)

```bash
# Symlink this repo's root into your agent's skills dir
ln -sfn "$(pwd)" ~/.claude/skills/ztrade-ares-7x24

# Verify Claude Code sees it
ls ~/.claude/skills/ztrade-ares-7x24/SKILL.md
```

After this, inside Claude Code, type:

```
/ztrade-ares-7x24 status
/ztrade-ares-7x24 set-goal "Tighten the v47 momentum weights to push past 8%."
```

### First run

```bash
ar724 init                      # creates .ares/state.db, loop_config.json, env.sh
ar724 validate-roles            # 4 roles OK
ar724 goal set "<your goal>"   # writes to loop_config.json + audit row
ar724 iter dry-run             # preview DAG without spawning workers
./bin/ares-up.sh "$(pwd)"      # install launchd plist + start tmux + cron
ar724 status                   # one-line summary (active, iter, last event)
ar724 board                    # 11-metric snapshot
tmux attach -t ar7x24-...      # raw log streams
```

---

## Architecture (5 layers, PRD §4)

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
│     + results.tsv), autoresearch/{best,mutable,candidates,repo- │
│     rts} (filesystem as the worker ↔ controller surface).       │
└─────────────────────────────────────────────────────────────────┘
```

The Skill (this repo's `SKILL.md`) sits above L0 as the researcher-facing
NL→CLI translator. It does not own state, does not bypass gates, and does
not run 24/7 — it only translates intent into `ar724` invocations.

---

## Project layout (PRD §16.2)

```
ztrade-ares-7x24/
├── SKILL.md                          # Skill entrypoint (loaded by Claude Code / Codex / OpenCode)
├── bin/
│   ├── ares                          # Python wrapper for the ar724 CLI
│   ├── ares-up.sh                    # install launchd plist + cron + start conductor
│   ├── ares-down.sh                  # graceful shutdown
│   ├── ares-status.sh                # one-shot status
│   ├── ares-halt.sh                  # write .circuit-breaker
│   ├── ares-cron-tick.sh            # 1-min tick + daily snapshot
│   └── com.wdblink.ztrade-ares-7x24.plist  # launchd template
├── ar724/                            # the Python daemon (pip-installable package)
│   ├── cli.py                        # 30+ Click commands (PRD §22.4)
│   ├── conductor.py                  # main tick loop, 14 steps (PRD §11.1)
│   ├── db.py                         # SQLite schema + atomic_write (Vibe-Trading port)
│   ├── dag.py                        # topological_layers, validate_dag (Vibe-Trading port)
│   ├── promotion_gate.py            # the 10 mechanical gates (PRD §8.1)
│   ├── evaluator_runner.py          # deterministic KEEP/DISCARD subprocess
│   ├── safety.py                     # path validators, SSRF, shell classifier
│   ├── budget.py                     # pre-call estimator + AnomalyDetector
│   ├── role_loader.py               # validate_roles + check_acceptance (Vibe-Trading port)
│   ├── heartbeat.py                  # HeartbeatTimer (Vibe-Trading port)
│   ├── oscillation.py               # OscillationDetector (per-param + bucketed)
│   ├── observability.py             # traces, metrics, logs, evals
│   ├── event_types.py               # controlled event vocabulary
│   ├── spawner.py / tmux_manager.py # tmux + env-file + keepalive
│   ├── adapters/                     # Claude Code, Codex, OpenCode CLI adapters
│   ├── live_callbacks/              # Feishu + tmux status callbacks
│   ├── runbooks/                     # 11 incident-response runbooks (PRD §15.4)
│   └── migrations/                   # SQLite schema migrations
├── autoresearch/v2/
│   ├── program.md                    # human-readable protocol
│   ├── protocol/                     # runtime artifacts (v47_params.json, results.tsv)
│   └── roles/                        # 4 role YAMLs (PRD §7.3)
├── schemas/                          # 4 JSON Schemas for role outputs
├── config/                           # safety_policy, model_profiles, role_routing
├── references/
│   └── runbooks → ../ar724/runbooks  # symlink (Skill references them by this path)
├── scripts/
│   └── setup.sh                      # legacy installer (use `pip install -e .` instead)
├── tests/                            # 97 unit + integration tests
├── evals/                            # eval fixtures (prompt-injection, oscillation, etc.)
└── README.md (this file)
```

---

## The 4 fixed roles (PRD §7.3)

| Role | Writes | Default model |
|---|---|---|
| `factor_combiner` | `autoresearch/candidates/<hash>/proposal.json` | sonnet_4_5 |
| `backtester` | `autoresearch/mutable/v47_params.json` (only role allowed) | sonnet_4_5 |
| `factor_validator` | `autoresearch/candidates/<hash>/validation.json` | opus_4_7 |
| `backtest_reviewer` | `autoresearch/candidates/<hash>/review.json` | opus_4_7 |

Plus a deterministic Python `evaluator_runner` (no LLM) that returns
`KEEP | DISCARD | BLOCKED` and is the **sole authority** for gate 5.

Per-role acceptance criteria (PRD §9.2) live in each YAML's `acceptance:`
block and are checked by `ar724/role_loader.py:check_acceptance`. Unknown
criteria are warnings, not failures — the registry is open for extension.

---

## The 10 mechanical promotion gates (PRD §8.1)

A candidate is promoted to `autoresearch/best/` only if **all 10** pass:

1. **Schema** — worker output validates against the role's JSON Schema.
2. **Scope** — worker wrote only within its declared `write_scope`.
3. **Candidate hash** — evaluator input hash matches the candidate hash in SQLite.
4. **Stale artifact** — evaluator output references the same candidate hash + iteration.
5. **Deterministic evaluation** — `evaluator_runner.py` exits 0 and decides `KEEP`.
6. **Metric** — `candidate_score >= best_score * 0.9` (regression guard).
7. **Reviewer independence** — reviewer is a separate phase job from builder/evaluator and returns KEEP.
8. **Budget** — not over `runs.budget_cents` or daily cap.
9. **Loop** — `consecutive_discards < 5` AND `consecutive_blockeds < 3` AND no oscillation halt.
10. **Promotion lock** — controller holds `service_locks.promotion`.

Gates 1-9 are computed in `ar724/promotion_gate.py:run_all_gates`; gate 10
is the CAS claim on the SQLite `service_locks` row.

---

## The 8-verb Skill (PRD §16.1, §22.1.6)

| Verb | Routes to |
|---|---|
| `set-goal` | `ar724 goal set` |
| `add-metric` | `ar724 budget set` (loop_config.stop_conditions) |
| `modify-strategy` | `ar724 params set --check-bounds` (V1.1) |
| `add-agent` | `ar724 roles add` (V1.1) |
| `set-baseline` | `ar724 promotion set-baseline` (writes to approvals) |
| `explain` | `ar724 explain <hash>` |
| `tune-budget` | `ar724 budget set` |
| `switch-policy` | `ar724 oscillation set-policy` |

Plus 13 lifecycle verbs (init / up / down / pause / resume / halt / iter
dry-run / iter cancel / eval run-all / status / board / events / costs /
trace / metrics). Destructive verbs (halt, iter cancel, promotion
rollback, safety policy reload) require explicit user confirmation in
chat — the CLI's own prompt does not count.

Incident keywords auto-route to the matching runbook (see SKILL.md §
"Incident dispatch").

---

## Observability (PRD §13)

Four pillars, all implemented:

- **Traces** — `trace_id` (UUID) flows through every phase of an iteration.
- **Metrics** — 11 counters/gauges (e.g. `iterations.completed.last_24h`).
- **Logs** — `events` table with controlled `event_type` vocabulary (§13.3).
- **Evals** — separate `eval_results` table; `ar724 eval run-all` for the suite.

---

## Safety (PRD §15)

- **Path validators** — `ar724/safety.py` rejects traversal outside the
  project root; `autoresearch/best/` is controller-write-only; only the
  `backtester` role may write `autoresearch/mutable/`.
- **SSRF** — private IP ranges (10/8, 172.16/12, 192.168/16, 127/8, 100.64/10)
  blocked in any `Bash` URL.
- **Shell classifier** — `network_or_destructive` commands denied; `git commit`
  is controller-only.
- **MCP allowlist** — only approved servers/tools; mutations require the
  `approvals` table (`ar724 mcp approvals list`).
- **Untrusted input** — MCP outputs, market data, prior reports, evaluator
  outputs are treated as untrusted; embedded instructions are ignored.

The validator library is defined in `ar724/safety.py`; the V1.0 production
enforcement is the 10-gate promotion pipeline in `ar724/promotion_gate.py`.
The V1.1 dispatch shim will wire the validators into the worker
invocation path explicitly.

---

## 11 runbooks (PRD §15.4)

In `ar724/runbooks/` (symlinked as `references/runbooks/` for the Skill):

| # | File | Trigger |
|---|---|---|
| 00 | `00-circuit-breaker-tripped.md` | **Operator's FIRST RESPONSE** — read this first |
| 01 | `01-pause-and-resume.md` | pause / resume the run |
| 02 | `02-rollback-last-promotion.md` | git revert a bad promotion |
| 03 | `03-disable-mcp-server.md` | remove a server from the allowlist |
| 04 | `04-rotate-secrets.md` | rotate `.ares/env.sh` keys |
| 05 | `05-replay-evaluator.md` | re-run the deterministic evaluator |
| 06 | `06-inspect-cost-spike.md` | investigate a `cost_anomaly` event |
| 07 | `07-recover-orphaned-jobs.md` | a phase job was killed mid-flight |
| 08 | `08-restore-sqlite-snapshot.md` | SQLite WAL corruption |
| 09 | `09-extend-conductor-leases.md` | lease timer vs backtest runtime mismatch |
| 10 | `10-vacation-handoff.md` | pre-departure checklist for the operator |

Each runbook is self-contained: Trigger → Detection → Immediate action →
Diagnosis → Recovery → Postmortem checklist.

---

## License and attribution

This project is MIT-licensed (`LICENSE`).

It ports source code (with attribution headers on every ported file) from:

- **HKUDS/Vibe-Trading** (MIT) — `ar724/dag.py:validate_dag`,
  `ar724/dag.py:topological_layers`, `ar724/dag.py:resolve_dependencies`,
  `ar724/db.py:atomic_write`, `ar724/db.py:replace_with_retry`,
  `ar724/heartbeat.py:HeartbeatTimer`, `ar724/role_loader.py:validate_roles`.
  See file headers for original paths and license URL.
- **HKUDS/ClawTeam** — design pattern inspiration for the tmux runtime
  (state.json + keepalive shell + exit-journal architecture). The keepalive
  in `ar724/spawner.py` is an **independent re-implementation** of the
  pattern described in `ClawTeam/spawn/keepalive.py:53-91`. No code copied.
- **iamtouchskyer/OPC** — design pattern inspiration for the L3 oscillation
  detector and per-role acceptance criteria. **Independent re-implementation**;
  no code copied.

See PRD §20 for the full attribution log and license URLs.

---

## Development

```bash
# Run tests (97, all hermetic — no network or LLM access)
python3.11 -m pytest tests/ -v

# Lint
ruff check ar724/

# Validate role YAMLs after editing
ar724 validate-roles

# Run a full eval suite (no impact on the main loop)
ar724 eval run-all
```

---

## Non-goals (PRD §2.2, §2.3)

- Live trading execution (deliberate; would need human-in-the-loop design).
- Multi-tenant SaaS.
- Cross-machine transport / horizontal scaling.
- 11-role OPC-style committee.
- Web UI (tmux IS the monitor).
- 1M-context caching layer.
- Autonomous Skill self-modification.
- Customer-facing distribution (single-user / internal research tool).

---

## V1.1 follow-ups (deferred, not blocking)

- `ar724 roles add` CLI (implementer workflow).
- Filesystem-level persona enforcement (researcher cannot edit `safety_policy.yaml`).
- Validator wiring into the dispatch shim (so `validate_path_write` and
  `validate_bash_command` are called at the worker invocation boundary, not
  just in tests).
- MCP allowlist / safety policy YAML unification.
- `ar724 approvals decide` transition command.
- `ar724 routing set` for `role_routing.yaml` (currently hand-edited).
- Empirical tuning of the 5s/3s/60s timing constants after 100+ iters.
- Oscillation policy re-evaluation after the first real signal.
