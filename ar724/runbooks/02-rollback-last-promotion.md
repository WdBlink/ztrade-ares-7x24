# Runbook: 02 — Rollback Last Promotion

## Trigger
A candidate was promoted to `autoresearch/best/`, but a follow-up analysis
shows the promotion was wrong: the candidate overfits, has a data leak, or
underperforms in a regime that the backtest did not cover.

## Detection
- A manual `ar724 explain <candidate_hash>` shows anomalous metrics
- The next iteration's KEEP/DISCARD diverges sharply from the promoted score
- A user reports "the strategy is no longer working" after a recent promotion

## Immediate action
1. **Stop the run** to prevent further damage:
   `ar724 halt "rollback in progress" --force`
2. Identify the offending promotion:
   `ar724 explain <candidate_hash>` — note the `git_commit` field.
3. Run `ar724 promotion rollback <iteration_id>`.

## Diagnosis
- Why did the 10-gate pipeline miss this? Walk through `gate 3-7`:
  - Hash mismatch would have been caught by gate 4.
  - Stale artifact would have been caught by gate 4.
  - Evaluator KEEP without metric pass would have been caught by gate 6.
  - Reviewer VETO would have been caught by gate 7.
- If a gate was bypassed: report to implementer. If the metric is correct but
  the strategy is wrong in production: re-tune the evaluator.

## Recovery
1. The rollback creates a `git revert` commit. Verify with:
   `git log --oneline -5 autoresearch/best/`
2. Resume the run: `ar724 resume`.
3. If the rollback is correct, the next iteration should propose a candidate
   that beats the reverted best.

## Postmortem checklist
- [ ] Update the `evaluator_contract.md` with the new metric.
- [ ] If the evaluator is buggy, file a task to fix it before re-enabling promotion.
- [ ] Add a fixture to `evals/evaluator_correctness/` so this scenario is caught next time.
