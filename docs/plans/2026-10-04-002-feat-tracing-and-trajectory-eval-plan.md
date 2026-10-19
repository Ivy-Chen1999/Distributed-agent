---
title: "feat: LangSmith tracing conventions and deterministic trajectory metrics"
type: feat
status: active
date: 2026-10-04
origin: docs/plans/2026-10-02-002-feat-scoped-provision-retrieval-plan.md (R12, U4, U6)
---

# feat: LangSmith tracing conventions and deterministic trajectory metrics

## Problem

Scoped retrieval (U4) made the pipeline's intermediate steps matter: which keys the Planner chose,
what each expert was granted or refused, and whether experts cited only what they were given.
Today none of that is visible or scored:

- Retrieval happens inside each expert node and appears in LangSmith only as part of the node's
  state output. The trace UI cannot render it as a retrieval.
- Metadata differs between graph runs (`run_id`, `scenario_id`, `system_version`, `git_*`) and eval
  runs (`case_id`, `repetition`, `system_version`). Filtering traces by backend, router mode, data
  scope or run mode is impossible.
- The Planner's focus is not saved on `RunResult`, so Planner key recall (plan R17 check) cannot be
  computed from `runs/` after the fact.
- Eval experiments re-serve precomputed scores. Nothing ties a score to the graph trace that
  produced it.
- Eval reports score only outcomes (coverage, omissions, grounding). Process failures (a needed key
  refused to every expert, a citation outside an expert's scope) are invisible unless coverage
  drops.
- The web pipeline log says a `no_data_in_scope` skip means "run continues as degraded". That is
  wrong: the skip is data, not an error, and does not degrade the run on its own.

## Constraints

- Existing SystemVersion ids and pinned prompt hashes stay unchanged. Nothing here touches
  `SystemVersionSpec` or prompts.
- `RunResult` gains optional fields only. Results saved before this change still load.
- LangSmith calls are no-ops when tracing is off (`LANGSMITH_TRACING` unset or no API key). Tests
  inject a mock client through `langsmith.tracing_context` and never need the network.
- Holdout material never reaches LangSmith. Every tracing and feedback helper checks one predicate,
  `womm.tracing.is_sealed(split)`.
- `src/womm/eval/run_eval.py` and `evaluators.py` are being edited on `feat/golden-import`. New logic
  goes into new modules (`src/womm/tracing.py`, `src/womm/eval/trajectory.py`). The eval files get
  small hooks only.

## External APIs adopted (checked against current docs)

- **Retriever runs:** `@traceable(run_type="retriever")`. Outputs are documents with
  `page_content`, `type: "Document"` and `metadata`. LangSmith renders each document inline. We use
  `process_outputs` to emit `{"documents": [...]}`, the shape LangChain retrievers log.
- **LLM runs:** `run_type="llm"` with `usage_metadata` (`input_tokens`, `output_tokens`,
  `total_tokens`, optional `total_cost`) renders token counts and latency. The `claude_code` backend
  already sets this via `RunTree.set(usage_metadata=...)`. On the `api` backend, LangChain's chat
  model run carries `AIMessage.usage_metadata` automatically. Latency is the span's own start and end
  time on both.
- **Feedback:** `Client.create_feedback(run_id, key=..., score=..., trace_id=..., comment=...)`
  attaches a score to any run, including a child run, and runs in the background when `trace_id` is
  given.
- **Experiments:** `evaluate()`/`aevaluate()` evaluators may return `{"results": [{"key", "score"}]}`.
  `summary_evaluators` get the lists of outputs. The existing recorder keeps that shape.
- **Root run id:** LangGraph accepts `config["run_id"]` for the root run, so `run_scenario` can know
  its graph trace id before the run starts.
- **Metadata and tags:** `traceable(metadata=..., tags=...)` and LangGraph `config["metadata"]`
  propagate to child runs. One helper builds them.
- **agentevals graph trajectory** (`graph_trajectory_strict_match`) is not adopted. It needs
  a checkpointer thread (`extract_langgraph_trajectory_from_thread`). Our graph is a fixed DAG whose
  node sequence varies only in active router mode, and that is already scored as `router_recall`.
  Its only signal would be "the topology did not change", which `test_end_to_end_fake` already
  covers. It would add a dependency and a checkpointer for no new information.

## Units

### U1. `src/womm/tracing.py`

- `is_sealed(split)`: True for `holdout`. The single predicate.
- `tracing_enabled()`: false when a `tracing_context(enabled=False)` is active. True when a context
  enables tracing with a client. Otherwise it needs `langsmith.utils.tracing_is_enabled()` and
  an API key.
- `run_metadata(sv, *, scenario_id, mode, code, case_id, split)` and `run_tags(meta)`. They hold
  `system_version`, `scenario_id`, `case_id`, `split`, `mode` (`eval` | `explore` | `demo`),
  `backends`, `models`, `router_mode`, `router_decider`, `git_sha`, `git_dirty`,
  `claude_cli_version` and `data_scope` (`scoped` when any expert has a scope, else `unscoped`).
  Graph runs and eval runs use the same helper.
- `traced(fn, *, split, **traceable_kwargs)`: `traceable(fn)` unless sealed.
- `retrieval_documents(records, sources)` and `trace_retrieval(fn, *, agent, split)`. Each
  granted source becomes one document (metadata: `source_id`, `kind`, `version`, `key`, `status`,
  `granted: true`). Each refused key becomes one empty document (`granted: false`,
  `refusal_reason`: `out_of_scope` | `unknown_key`). The memorandum is listed as given.
- `send_feedback(run_id, scores, *, split, trace_id=None, client=None)`: one `create_feedback` per
  non-None score. A no-op when sealed, when tracing is off, or without a run id.

### U2. Graph hooks (`build.py`, `experts.py`, `planner.py`, `models/run.py`)

- `run_scenario` takes `run_mode`, `case_id` and `split`. It builds metadata and tags through
  `run_metadata`, and passes a fresh root `run_id` to LangGraph. It returns it as
  `RunResult.trace_run_id` when tracing is on. A sealed split runs under
  `tracing_context(enabled=False)`.
- The expert node wraps its `expert_view` call in `trace_retrieval`. The Planner's sizing loop
  calls `expert_view` directly and stays untraced, so the retriever spans are one per expert.
- `RunResult.planner: PlannerTrace | None` holds the focus keys in Planner order, the keys of each
  focus area, the cap notes and the experts the router dispatched. Router decisions and their
  probabilities are already on `RunResult.decisions`.

### U3. `src/womm/eval/trajectory.py`

These are deterministic metrics from `RunResult`, the golden case and the labelled router
decisions:

| Metric | Definition | Skipped (None) when |
|---|---|---|
| `planner_key_recall` | share of the case's anchor keys (union of `expected_impacts[].provision_keys`) the Planner chose | no `planner` on the run, or no anchor keys |
| `planner_key_precision` | share of Planner keys that are anchor keys | same, or the Planner chose no keys |
| `router_recall` | share of judge-relevant experts the router called relevant | no labelled decisions with a relevant expert |
| `router_brier` | mean (p − relevant)² over labelled decisions with a probability | no probabilities |
| `scope_violations` | evidence citing a source the run holds that is not citable for that expert (another expert's grant, or a memorandum its scope withholds). Expected 0. | no retrieval records |
| `citation_out_of_retrieval` | evidence citing a source the run never retrieved for anyone | no retrieval records |
| `needed_key_refused` | anchor keys that some expert requested and no expert was granted | no retrieval records or no anchor keys |

The eval report gets a `trajectory` block with per-metric means over scored cases. Each `CaseScore`
carries its `trajectory` dict and `graph_run_id`. Metrics go to LangSmith as feedback keys
`traj.<name>` on the graph run, and as evaluator results in the recorded experiment.

### U4. Eval hooks (`run_eval.py`, `evaluators.py`)

- `CaseScore` gains `graph_run_id` and `trajectory`. Both are optional additions.
- `one_case` computes the trajectory, attaches it, and calls `send_feedback` for `coverage`,
  `omissions_addressed`, `grounding` and `traj.*` on the graph run.
- The eval-case span uses `run_metadata(mode="eval")` through `traced(...)`, so it is sealed-aware.
- The experiment recorder's `metrics` evaluator also returns the `traj.*` scores.

### U5. Web fix

The pipeline log shows a `no_data_in_scope` expert as `INFO · skipped · no data within its scope (no
call)`, without "run continues as degraded". The Detail page's failed-experts list labels it
"Skipped: no data in scope". Unit and e2e assertions are updated.

## Test scenarios

- `run_metadata` holds every key. `data_scope` is `scoped` for `v1.0-scoped` and `unscoped` for
  the default. The tags include version, mode and scope.
- `is_sealed("holdout")` is True. `send_feedback` with `split="holdout"` never touches the
  client. `traced(fn, split="holdout")` returns `fn` itself. A holdout `run_scenario` under a
  mock-enabled tracing context posts no runs.
- With tracing off (no env), `send_feedback` creates no client and `trace_run_id` is None.
- With a mock client: a fake run posts one `retriever` run per expert. Its outputs hold
  `documents` with `type: "Document"`, and refused keys appear with `granted: false` and
  `refusal_reason`. The root graph run carries the shared metadata, and its id equals
  `RunResult.trace_run_id`.
- `RunResult.planner` holds the focus keys and dispatched experts. Old JSON without it still loads.
- Trajectory: recall and precision on a known plan. None without annotation. A citation to another
  expert's source counts as a scope violation. An unknown source counts as out-of-retrieval. A
  refused anchor key is counted. Router recall and Brier come from labelled decisions.
- Eval: a fake eval report has `trajectory` means. With a mock client, feedback `traj.*` and
  `coverage` go to the graph run id. The experiment `metrics` include `traj.*`.
- `claude_code` llm span gets `usage_metadata` from the CLI payload.
- Web: the log line for a `no_data_in_scope` skip is INFO and says "skipped". The timeout case still
  says "run continues as degraded".

## Later (documented, not implemented)

- **`expert_coverage` per domain:** coverage split by the expert whose findings matched each
  expected impact (needs a domain label on expected impacts, which golden-import adds as
  `category`).
- **`synthesis_faithfulness` LLM judge:** whether each dossier impact is entailed by its findings.
  This needs a human-calibrated judge (agreement on a labelled sample) before its scores are used,
  following the coverage-judge calibration in the golden-case plan.
