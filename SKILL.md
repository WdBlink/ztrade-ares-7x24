---
name: ztrade-ares-7x24
description: Operate the ztrade-ares 7x24 autonomous research controller via natural language. Parses user prompts into 8 researcher verbs (set-goal, add-metric, modify-strategy, add-agent, set-baseline, explain, tune-budget, switch-policy) plus 13 lifecycle verbs (init, up, down, pause, resume, halt, iter dry-run, iter cancel, eval run-all, status, board, events, costs, trace, metrics), confirms intent for high-impact actions, and dispatches to the `ar724` CLI. Routes incident prompts to the right runbook (circuit-breaker tripped → references/runbooks/00; pause → 01; rollback → 02; MCP disable → 03; secrets → 04; evaluator replay → 05; cost spike → 06; orphaned jobs → 07; SQLite restore → 08; lease extension → 09; vacation handoff → 10). Use when the user says "/ztrade-ares-7x24 ...", "set the goal to ...", "pause the run", "rollback the last promotion", "why did the circuit breaker trip", "what does the board look like", or describes a controller incident.
license: MIT
metadata:
  author: WdBlink
  version: "1.0"
  requires-cli: ar724
  install-source: "AR724_INSTALL_SOURCE env var (default: git+https://github.com/WdBlink/ztrade-ares-7x24.git@main — same repo as this Skill)"
---

# ztrade-ares-7x24 — Skill entrypoint

You are the entrypoint for the **ztrade-ares 7×24 autonomous research controller**.
The user is a **Researcher** (per the controller PRD §22.1). They will invoke you
with one of **8 verbs** or describe a controller incident. Your job is to parse
intent, confirm when the action is high-impact, and dispatch to the `ar724` CLI
or to the matching runbook.

## Execution Procedure

```
def entrypoint(prompt, workdir=".") -> result:
    if not preflight():
        return blocked("ar724 CLI not found. Run: bash scripts/setup.sh")

    verb, args = parse_prompt(prompt)                 # NL -> verb + args
    if verb is None:                                  # ambiguous / no match
        ask_user("Which verb? Set-goal / add-metric / ... / incident dispatch?")

    if verb is INCIDENT:                              # user described a controller incident
        runbook = match_runbook(prompt)               # route via incident keyword table
        read(f"references/runbooks/{runbook}")
        return dispatch_runbook_steps(runbook, args, workdir)

    if is_high_impact(verb, args):
        confirm_high_impact(action=verb, args=args)

    return invoke_cli(verb, args)                     # call ar724 via Bash

def dispatch_runbook_steps(runbook, args, workdir) -> result:
    for step in runbook.execution_procedure:
        if step.requires_operator_edit:
            present_manual_edit(step)                 # never edit secrets/source/config directly
            wait_for_user("done")
        elif is_high_impact(step.command, step.args):
            confirm_high_impact(action=step.command, args=step.args)
            invoke_cli(step.command, step.args)
        else:
            invoke_or_report(step)

def confirm_high_impact(action, args) -> confirmed:
    echo("About to: <action>. Effect: <effect>. Continue? (yes/no)")
    if user != "yes":
        abort("user declined")
```

### Step 0 — Pre-flight (first invocation only)

```
def preflight() -> bool:
    if which ar724 is empty:
        echo("ar724 CLI not found. Run: bash scripts/setup.sh")
        return False
    return True
```

## Verbs

| Verb | User prompt pattern | CLI call | Confirmation? |
|---|---|---|---|
| `set-goal` | `/ztrade-ares-7x24 set-goal "<goal text>"` | `ar724 goal set "<goal text>"` | No (idempotent) |
| `add-metric` | `/ztrade-ares-7x24 add-metric <name> <value>` | `ar724 config add-stop-condition <name> <value>` | No |
| `modify-strategy` | `/ztrade-ares-7x24 modify-strategy <param> from <a> to <b>` | `ar724 params set <param> <b> --check-bounds` | No (bounds-checked) |
| `add-agent` | `/ztrade-ares-7x24 add-agent <name> --based-on <role> --acceptance "<criteria>"` | `ar724 roles add <name> --based-on <role> --acceptance "<criteria>"` | **Yes (writes new role YAML; show diff first)** |
| `set-baseline` | `/ztrade-ares-7x24 set-baseline <iter-tag>` | `ar724 promotion set-baseline <iter-tag>` | **Yes (overwrites best/; show what is being replaced)** |
| `explain` | `/ztrade-ares-7x24 explain <candidate_hash>` | `ar724 explain <candidate_hash>` | No (read-only) |
| `tune-budget` | `/ztrade-ares-7x24 tune-budget <field> <value>` | `ar724 budget set --<field> <value>` | No (idempotent) |
| `switch-policy` | `/ztrade-ares-7x24 switch-policy <name> <value>` | `ar724 oscillation set-policy <value>` | No (idempotent) |

## Lifecycle verbs

These wrap the `ar724` CLI directly. **All control verbs that change daemon or
iteration state (`up`, `down`, `pause`, `resume`, `halt`, `iter cancel`, `iter next`,
promotion rollback, backup restore, and safety policy reload) require explicit
user confirmation** before invocation; the CLI's own prompt does not count.

| Verb | CLI call |
|---|---|
| `init` | `ar724 init` |
| `up` | `ar724 up` |
| `down` | `ar724 down` |
| `pause` | `ar724 pause` |
| `resume` | `ar724 resume` |
| `halt` | `ar724 halt "<reason>"` |
| `iter dry-run` | `ar724 iter dry-run` |
| `iter cancel` | `ar724 iter cancel` |
| `eval run-all` | `ar724 eval run-all` |
| `status` | `ar724 status` |
| `board` | `ar724 board` |
| `events` | `ar724 events [--since 1h] [--severity ...]` |
| `costs` | `ar724 costs [--today]` |
| `trace` | `ar724 trace <trace_id>` |
| `metrics` | `ar724 metrics` |

## Incident dispatch (runbook routing)

When the user describes a controller incident, route to the matching runbook.
The runbook is the source of truth for the procedure; the skill just routes
to it and invokes the relevant safe `ar724` commands at each step. For
destructive commands or manual edits, the runbook must call the confirmation
gate or ask the operator to make the edit and reply `done`.

| Incident keywords | Runbook |
|---|---|
| `circuit breaker tripped`, `circuit_breaker_tripped`, `.circuit-breaker` file | `references/runbooks/00-circuit-breaker-tripped.md` |
| `pause the run`, `over-producing`, `spending too fast` | `references/runbooks/01-pause-and-resume.md` |
| `rollback`, `wrong promotion`, `data leak`, `overfit candidate` | `references/runbooks/02-rollback-last-promotion.md` |
| `MCP server`, `prompt injection`, `safety_violation_blocked` | `references/runbooks/03-disable-mcp-server.md` |
| `rotate secret`, `leaked credential`, `401 from provider` | `references/runbooks/04-rotate-secrets.md` |
| `evaluator disagrees`, `KEEP/DISCARD mismatch`, `replay evaluator` | `references/runbooks/05-replay-evaluator.md` |
| `cost spike`, `cost_anomaly`, `budget exceeded` | `references/runbooks/06-inspect-cost-spike.md` |
| `orphaned job`, `phase_orphaned`, `stale-reaper` | `references/runbooks/07-recover-orphaned-jobs.md` |
| `SQLite corrupt`, `database disk image is malformed`, `state.db` | `references/runbooks/08-restore-sqlite-snapshot.md` |
| `lease expired`, `premature reap`, `heartbeat timer missing` | `references/runbooks/09-extend-conductor-leases.md` |
| `vacation`, `going away`, `handoff to colleague` | `references/runbooks/10-vacation-handoff.md` |

## High-impact actions

- `add-agent`, `set-baseline`, `up`, `down`, `pause`, `resume`, `halt`,
  `iter cancel`, `iter next`, `promotion rollback`, `backup restore`, and
  `safety policy reload`.
- Any action that clears `.ares/.circuit-breaker`, overwrites `.ares/state.db`,
  changes `.ares/env.sh`, changes controller config, or asks the operator to
  patch files inside the controller source tree.
- Incident runbooks inherit this list. A runbook step cannot bypass the
  confirmation gate just because the incident itself matched automatically.

## Natural-language parsing rules

- "I want to add a 30-day reversal factor" → `modify-strategy`, param = `<name>`, `from = <current>`, `to = <new>`. If the param name is ambiguous, ask the user.
- "what does the board look like" → `board` (or `explain` with `last_candidate_hash` from `ar724 status --format last_candidate_hash`).
- "stop the run" → `pause` first; `halt --force` only if the user confirms they want a circuit-breaker trip.
- If the verb is ambiguous, ask the user to clarify. Do not guess.

## Error surfacing

- If `ar724 <command>` exits non-zero, surface the stderr in the chat. Do **not** retry without explicit "retry".
- For destructive actions (`up`, `down`, `pause`, `resume`, `halt`, `iter cancel`, `iter next`, `promotion rollback`, `backup restore`, `safety policy reload`, and manual edit steps), the Skill must echo the effect and wait for explicit "yes" or `done` before continuing. The CLI's own confirmation prompts do not count.
- If the system is halted (`.circuit-breaker` set), the Skill surfaces this prominently and refuses to invoke verbs that re-run the main loop, until the user runs `ar724 resume` via the CLI.

## What this skill does NOT do

- The skill does not edit files inside the `ar724/` Python package.
- The skill does not edit `safety_policy.yaml` directly. It can tell the operator exactly what to change, wait for `done`, then run `ar724 safety policy reload` after explicit confirmation.
- The skill does not ask the user to paste secret values into chat. For secret rotation it instructs the operator to edit `.ares/env.sh` locally.
- The skill does not run 24/7; the controller daemon (`ar724 up`) runs 24/7.
- The skill does not bypass the circuit-breaker.

## References

- `references/runbooks/` — 10 incident-response runbooks (00 first-response, 01-10 by incident class)
