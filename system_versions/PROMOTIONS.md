# System version promotions

Promotion rule (v0): better on the golden cases overall, no per-case regression beyond run-to-run
noise, grounding stays ~100%. No sealed holdout exists yet, so promotions are provisional (v1 R23).

| Date | Promoted | Replaces | Evidence |
|---|---|---|---|
| 2026-09-29 | `v0.3-candidate.yaml` (`sv_c28e04120c37`) | `v0-baseline.yaml` (`sv_3d8f3a48f955`) | 3 runs per case, claude_code backend, same planner/judge prompts. Mean coverage 0.64 → 0.72, mean omissions 0.67 → 0.78, grounding 1.00 → 0.99. case_02 variance down (coverage sd 0.20 → 0.08). case_01 omissions 0.83 → 0.67, within noise (sd 0.14; one omission item = 0.25). LangSmith: `womm-v0-baseline-*`, `womm-v0.3-candidate-f0aab5f9`. Caveat: prompts were tuned on the same two golden cases. |

`v0.3-api.yaml` is the deployment twin (same prompts, api backend).
