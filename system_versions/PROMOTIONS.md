# System version promotions

Promotion rule (v0): better on the golden cases overall, no per-case regression beyond run-to-run
noise, grounding stays ~100%. No sealed holdout exists yet, so promotions are provisional (v1 R23).

| Date | Promoted | Replaces | Evidence |
|---|---|---|---|
| 2026-09-29 | `v0.3-candidate.yaml` (`sv_c28e04120c37`) | `v0-baseline.yaml` (`sv_3d8f3a48f955`) | 3 runs per case, claude_code backend, same planner/judge prompts. Mean coverage 0.64 → 0.72, mean omissions 0.67 → 0.78, grounding 1.00 → 0.99. case_02 variance down (coverage sd 0.20 → 0.08). case_01 omissions 0.83 → 0.67, within noise (sd 0.14; one omission item = 0.25). LangSmith: `womm-v0-baseline-*`, `womm-v0.3-candidate-f0aab5f9`. Caveat: prompts were tuned on the same two golden cases. |

`v0.3-api.yaml` is the deployment twin (same prompts, api backend), `sv_40304d89c9e5`.

## 2026-10-14: api models pinned

The api twins (`v0.3-api` and the three `v1.0-*-api` files) run on OpenAI snapshot ids, chosen on
OpenAI's price list of 2026-10-14 for price per quality. GPT-6 has no dated ids; the model pages
list each id as its own snapshot, so the id is the pin. Reasoning effort is pinned to `medium` on
every role and is part of the version id and, for the judge, the judge id.

| Roles | Model | Price per 1M tokens (input / cached / output) |
|---|---|---|
| Planner | `openai:gpt-6-luna` | $0.10 / $0.01 / $0.50 |
| Experts, synthesis, judge, cost step | `openai:gpt-6.1-sol` | $2.00 / $0.10 / $10.00 |

The Planner only splits the question and assigns experts, so it runs on the small model; every
role that reasons over the text runs on the larger one. Evolved candidates get the same models
role for role when their api twin is archived (`womm evolve twin`). The judge's model and effort
must not change after the calibration: a change means a new calibration sitting.

Live check on 2026-10-14: `case_02_sme_impacts` on `v0.3-api`, one run, coverage 0.67, grounding
1.00, about 70k tokens and $0.35 for the run (judge call not included), 7 minutes.

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
| `v1.0-scoped-api.yaml` | `sv_19c5a3b406a8` | api twin of `v1.0-scoped.yaml`: every role on the api backend with `v0.3-api`'s model and reasoning effort for that role. |
| `v1.0-unscoped-api.yaml` | `sv_e1c252be328b` | api twin of `v1.0-unscoped.yaml`: every role on the api backend with `v0.3-api`'s model and reasoning effort for that role. |

The pre-registered comparison runs on the api twins: `--scoped sv_19c5a3b406a8 --unscoped
sv_e1c252be328b` (`v1.0-scoped-api.yaml` vs `v1.0-unscoped-api.yaml`). The claude_code files
serve development runs only, whose verdicts are dev-only. The twins' models were pinned on
2026-10-14 (see above), which gave them the ids in this table.

Comparison: `scripts/compare_versions.py`, scoped vs unscoped only, AI Act golden cases only,
api backend, router in shadow (or off) mode, at least 6 runs per case. Pre-registered rule: if
scoped coverage is more than one noise band below unscoped, revise the default scopes before
v1.0-scoped is used further; otherwise keep the scopes. Golden-case results measure preset-mode
scoping only. The single-agent arm (`v1.0-single`) is deferred until 15–30 golden cases exist.

## 2026-10-12 candidates: cost analysis (not promoted)

Cost-analysis candidates of the EU cost plan. Neither replaces the incumbent or takes part in the
self-evolution promotion gate; they are scored by `womm cost check` against the verified IA cost
reference (`evals/cost_reference/ai_act_swd2021_84.yaml`, checked 11 of 11 on 2026-10-12, EU-level
FTE basis §6.2).

| File | Version id | Purpose |
|---|---|---|
| `v1.0-cost.yaml` | `sv_1f12d8afd1e7` | `v1.0-unscoped` plus the cost step (cost records per duty in a separate dossier section); development runs on claude_code, dev-only. |
| `v1.0-cost-api.yaml` | `sv_311aa5a33d5f` | api twin of `v1.0-cost.yaml`, for the formal cost check once an API key exists. |
