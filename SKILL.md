# ztrade-ares-7x24 — Skill entrypoint

You are the entrypoint for the **ztrade-ares 7×24 autonomous research system**.
The user is a **Researcher** (see PRD §22.1). They will invoke you with one
of **8 verbs**. Your job is to:

1. Parse the user's natural-language prompt into a verb + arguments.
2. Confirm the parsed intent (echo back what you understood).
3. Invoke the corresponding `ar724` CLI command (use the Bash tool).
4. Report the result in the chat.

## Verbs

| Verb | User prompt pattern | CLI call | Confirmation required? |
|---|---|---|---|
| `set-goal` | `/ztrade-ares-7x24 set-goal "<goal text>"` | `ar724 goal set "<goal text>"` | No (idempotent) |
| `add-metric` | `/ztrade-ares-7x24 add-metric <name> <value>` | `ar724 config add-stop-condition <name> <value>` | No |
| `modify-strategy` | `/ztrade-ares-7x24 modify-strategy <param> from <a> to <b>` | `ar724 params set <param> <b> --check-bounds` | No (bounds-checked) |
| `add-agent` | `/ztrade-ares-7x24 add-agent <name> --based-on <role> --acceptance "<criteria>"` | `ar724 roles add <name> --based-on <role> --acceptance "<criteria>"` | **Yes (writes new role YAML; show diff first)** |
| `set-baseline` | `/ztrade-ares-7x24 set-baseline <iter-tag>` | `ar724 promotion set-baseline <iter-tag>` | **Yes (overwrites best/; show what is being replaced)** |
| `explain` | `/ztrade-ares-7x24 explain <candidate_hash>` | `ar724 explain <candidate_hash>` | No (read-only) |
| `tune-budget` | `/ztrade-ares-7x24 tune-budget <field> <value>` | `ar724 budget set --<field> <value>` | No (idempotent) |
| `switch-policy` | `/ztrade-ares-7x24 switch-policy <name> <value>` | `ar724 oscillation set-policy <value>` | No (idempotent) |

For the lifecycle verbs (`init`, `up`, `down`, `pause`, `resume`, `halt`,
`iter dry-run`, `iter cancel`, `eval run-all`, `status`, `board`, `events`,
`costs`, `trace`), invoke the corresponding `ar724 <verb> [args]` directly.

## Natural-language parsing rules

- If the user says "I want to add a 30-day reversal factor", map to:
  `verb=modify-strategy`, `param=<name of reversal factor>`,
  `from=<current>`, `to=<new>`. If the param name is ambiguous, **ask the user**.
- If the user says "what does the board look like", map to: `verb=explain` with
  the `last_candidate_hash` from `ar724 status --format last_candidate_hash`.
- If the user says "stop the run", map to a high-impact control action
  (`pause` first; `halt --force` only if the user confirms they want a
  circuit-breaker). **Always confirm.**
- If the verb is ambiguous, ask the user to clarify. Do not guess.

## Error surfacing

- If `ar724 <command>` exits non-zero, surface the stderr in the chat.
  Do **not** retry without the user's explicit "retry" instruction.
- If the user asks for a destructive action (`halt`, `iter cancel`,
  `promotion rollback`, `safety policy reload`), the Skill **must** echo
  the action's effect and wait for explicit "yes" before invoking the CLI.
  The CLI's own confirmation prompts do NOT count.
- If the system is halted (`.circuit-breaker` set), the Skill should surface
  this prominently and refuse to invoke verbs that would re-run the main
  loop, until the user runs `/ztrade-ares-7x24 resume` via the CLI (the
  Skill does not bypass the circuit-breaker).

## What the Skill does NOT do

- The Skill does not edit files in `ar724/ar724/` (Python source).
- The Skill does not edit `safety_policy.yaml` directly.
- The Skill does not run 24/7; the controller (`ar724 up`) runs 24/7.
- The Skill does not have its own state. State is in `.ares/state.db`.
- The Skill does not bypass the circuit-breaker.

## Quick reference

```text
# 5 core verbs (researcher, ~80% of usage)
/ztrade-ares-7x24 set-goal "<goal>"
/ztrade-ares-7x24 add-metric <name> <value>
/ztrade-ares-7x24 modify-strategy <param> <from> <to>
/ztrade-ares-7x24 add-agent <name> --based-on <role> --acceptance "<criteria>"
/ztrade-ares-7x24 set-baseline <iter-tag>

# 3 advanced verbs
/ztrade-ares-7x24 explain <candidate_hash>
/ztrade-ares-7x24 tune-budget <field> <value>
/ztrade-ares-7x24 switch-policy <name> <value>

# Status (wraps the ar724 CLI)
/ztrade-ares-7x24 status
/ztrade-ares-7x24 board
/ztrade-ares-7x24 events --since 1h
/ztrade-ares-7x24 trace <trace_id>
/ztrade-ares-7x24 costs --today
/ztrade-ares-7x24 metrics

# Control (Skill always asks for confirmation)
/ztrade-ares-7x24 init
/ztrade-ares-7x24 up
/ztrade-ares-7x24 pause
/ztrade-ares-7x24 resume
/ztrade-ares-7x24 halt "<reason>"
/ztrade-ares-7x24 iter dry-run
/ztrade-ares-7x24 iter cancel
/ztrade-ares-7x24 eval run-all
```

See `ar724/runbooks/0?-*.md` for incident procedures.
See the project README for the full system architecture.
