# Runbook: 05 — Replay the Evaluator

## Trigger
A user reports that the evaluator's KEEP/DISCARD decision disagrees with a
manual analysis. The operator wants to re-run the evaluator on a stored
candidate to verify the decision is reproducible.

## Detection
- `ar724 explain <candidate_hash>` shows `decision: KEEP` but a manual
  walkthrough suggests `DISCARD`.
- An overnight report shows the evaluator's score trending against
  intuition.

## Immediate action
1. Identify the candidate's stored mutable file:
   `ar724 explain <candidate_hash>` → `evaluation.evaluator_run_dir`.
2. Locate the candidate JSON: that directory contains `candidate.json` plus
   `metrics.json` and `run_status.json`.
3. Re-run the evaluator manually:
   ```bash
   cd <evaluator_run_dir>
   python3 evaluator.py
   ```
4. Compare the new `metrics.json` and `run_status.json` to the originals.

## Diagnosis
- If outputs disagree: the evaluator is non-deterministic. Check whether
  the evaluator uses a non-deterministic source (current time, hash seed,
  multiprocessing). The evaluator SHOULD be deterministic.
- If outputs agree: the manual analysis is wrong. Document the disagreement
  to improve future manual reviews.

## Recovery
1. If the evaluator is non-deterministic, fix it. Do not allow a
   non-deterministic evaluator to gate promotion.
2. If the manual analysis was wrong, update the manual procedure or training.

## Postmortem checklist
- [ ] File a task to add a determinism fixture to
       `evals/evaluator_correctness/` (the same candidate must produce the
       same decision on every run).
- [ ] If the candidate is a legitimate DISCARD, mark the original
       promotion's `promotions` row as `failed` in the audit log.
