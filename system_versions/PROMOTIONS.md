# System version promotions

Promotion rule (v0): better on the golden cases overall, no per-case regression beyond run-to-run
noise, grounding stays ~100%. No sealed holdout exists yet, so promotions are provisional (v1 R23).

| Date | Promoted | Replaces | Evidence |
|---|---|---|---|
| 2026-09-29 | `v0.3-candidate.yaml` (`sv_c28e04120c37`) | `v0-baseline.yaml` (`sv_3d8f3a48f955`) | 3 runs per case, claude_code backend, same planner/judge prompts. Mean coverage 0.64 → 0.72, mean omissions 0.67 → 0.78, grounding 1.00 → 0.99. case_02 variance down (coverage sd 0.20 → 0.08). case_01 omissions 0.83 → 0.67, within noise (sd 0.14; one omission item = 0.25). LangSmith: `womm-v0-baseline-*`, `womm-v0.3-candidate-f0aab5f9`. Caveat: prompts were tuned on the same two golden cases. |

`v0.3-api.yaml` is the deployment twin (same prompts, api backend).

## 2026-09-29 follow-up: pooled evidence for v0.3

Three more runs of the same pipeline (router decider Jev in shadow mode, which does not change
execution; LangSmith `womm-v0.3-candidate+overrides-e346a2ab`) show the run-to-run noise:
case_01 coverage 0.77 in the first three runs, 0.63 (0.7 / 0.5 / 0.7) in the next three.

Pooled over 6 runs per case, v0.3 against the baseline:

| | baseline | v0.3 (6 runs) |
|---|---|---|
| case_01 coverage | 0.70 | ≈0.70 (no gain) |
| case_01 omissions | 0.83 | ≈0.71 |
| case_02 coverage | 0.57 | ≈0.69 |
| case_02 omissions | 0.50 | ≈0.83 |

The promotion stands (net better, case_02 clearly), but the gain comes from case_02. Single-case
coverage moves by ~0.1 between identical runs, so 3 repetitions only resolve differences above
~0.1; the v1 gate (R28) must be sized for that.
