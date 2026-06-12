# Evaluator contract (PRD §8.1 gate 5)

The deterministic evaluator is the **sole authority** for KEEP/DISCARD
decisions. The evaluator is a local Python subprocess; it makes no LLM calls.

## Inputs

- `--candidate-path`: path to the candidate mutable JSON file
- `--score-floor`: minimum score required to KEEP (default 0.9)

## Outputs (written to the evaluator run dir)

- `metrics.json`: structured metrics
- `run_status.json`: `{"status": "DECIDED", "decision": "KEEP|DISCARD", "score": float}`
- (optional) `evaluator.py` if no script is supplied, the controller writes a
  default stub

## Decision

`decision = "KEEP"` if `score >= score_floor`, else `"DISCARD"`. A blocked
evaluator (timeout, missing files, unparseable input) emits `"BLOCKED"`.

## Authority

No LLM verdict can override the evaluator. The reviewer's role is to
provide an independent second opinion; the reviewer is one of the
10 mechanical gates, not a substitute for the evaluator.
