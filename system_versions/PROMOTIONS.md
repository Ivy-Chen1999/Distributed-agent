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

## 2026-10-04 candidates: data scopes (not promoted)

Both are candidates only. Neither replaces `v0.3-candidate.yaml`, and neither is promoted until
the comparison below has api-backend evidence.

| File | Version id | Purpose |
|---|---|---|
| `v1.0-scoped.yaml` | `sv_735b080cf78a` | Side experiment: per-expert data scopes split by data form (full text versus obligation records); only the Planner sees the delta. |
| `v1.0-unscoped.yaml` | `sv_73c6a3fdd013` | Control arm, and the self-evolution base candidate (R27/R28): v1.0-scoped with every scope removed. Same prompts, explore prompt and retrieval cap. |
| `v1.0-scoped-api.yaml` | `sv_21cba366298d` | api twin of `v1.0-scoped.yaml`: every role on the api backend with `v0.3-api`'s model id. |
| `v1.0-unscoped-api.yaml` | `sv_761d872bb18e` | api twin of `v1.0-unscoped.yaml`: every role on the api backend with `v0.3-api`'s model id. |

The pre-registered comparison runs on the api twins: `--scoped sv_21cba366298d --unscoped
sv_761d872bb18e` (`v1.0-scoped-api.yaml` vs `v1.0-unscoped-api.yaml`). The claude_code files
serve development runs only, whose verdicts are dev-only. The twins' model ids are unverified
until the first live run with an API key; if a model id has to change, the twins get new ids
and this table is updated before any comparison run.

Comparison: `scripts/compare_versions.py`, scoped vs unscoped only, AI Act golden cases only,
api backend, router in shadow (or off) mode, at least 6 runs per case. Pre-registered rule: if
scoped coverage is more than one noise band below unscoped, revise the default scopes before
v1.0-scoped is used further; otherwise keep the scopes. Golden-case results measure preset-mode
scoping only. The single-agent arm (`v1.0-single`) is deferred until 15–30 golden cases exist.
