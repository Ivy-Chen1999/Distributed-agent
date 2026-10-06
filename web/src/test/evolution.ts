// Evolution page fixtures: a seed, a prompt-stage child, a topology child (promoted, weak mode,
// via its api twin) and a rejected dev candidate with an R37 regression.
import type { CandidateDetail, CandidateDiff, Lineage, LineageNode, PromotionDecision } from '../types';

const node = (over: Partial<LineageNode> & Pick<LineageNode, 'version_id'>): LineageNode => ({
  name: 'v1.0-unscoped',
  parent_id: null,
  cycle_id: null,
  origin: 'seed',
  created_at: '2026-11-02T10:00:00+00:00',
  badges: ['seed'],
  twins: [],
  decision: null,
  label: null,
  new_expert: null,
  r37_regression: null,
  ...over,
});

export const SEED = node({ version_id: 'sv_seed00000001', twins: ['sv_seedapi00001'] });
export const PROMPT = node({ version_id: 'sv_prompt000001', name: 'v1.0-unscoped+e1', parent_id: SEED.version_id, origin: 'gepa', badges: ['gepa'], cycle_id: 'cycle_a' });
export const TOPOLOGY = node({
  version_id: 'sv_topology0001',
  name: 'v1.0-unscoped+e1+e2',
  parent_id: PROMPT.version_id,
  origin: 'topology',
  badges: ['topology', 'promoted'],
  twins: ['sv_topologyapi1'],
  decision: 'promoted',
  label: 'promoted (weak threshold: directional)',
  new_expert: 'workforce',
  r37_regression: false,
  cycle_id: 'cycle_a',
});
export const REJECTED = node({
  version_id: 'sv_rejected0001',
  name: 'v1.0-unscoped+e3',
  parent_id: SEED.version_id,
  origin: 'gepa',
  badges: ['gepa', 'promoted', 'dev-only'],
  decision: 'promoted',
  label: 'promoted (dev-only, not deployable; weak threshold: directional)',
  r37_regression: true,
  cycle_id: 'cycle_b',
});

export const lineage = (): Lineage => ({ nodes: [SEED, PROMPT, TOPOLOGY, REJECTED], publish_summary: false });

const delta = (mean: number, sd = 0.05) => ({ mean_delta: mean, ci95_low: null, ci95_high: null, n_cases: 8, noise_sd: sd });

export const weakDecision: PromotionDecision = {
  gate_id: 'gate_1',
  created_at: '2026-11-03T10:00:00+00:00',
  candidate_version: 'sv_topologyapi1',
  incumbent_version: 'sv_seedapi00001',
  mode: 'weak',
  deployable: true,
  decision: 'promoted',
  label: 'promoted (weak threshold: directional)',
  reasons: [],
  notes: ['weak mode: no minimum-detectable-delta report for this judge'],
  deltas: { coverage: delta(0.06), grounding: delta(-0.01), omissions_addressed: delta(0) },
  n_proposals: 3,
  flags: ['insufficient_proposals'],
};

const base = (n: LineageNode): CandidateDetail => ({
  node: n,
  experts: [
    { id: 'legal', domain: 'legal', router_gloss: null },
    { id: 'fiscal', domain: 'fiscal', router_gloss: null },
  ],
  summary: { experts_added: [], added_experts: {}, prompts_changed: ['expert:fiscal'], router_gloss_changed: {}, retrieval: null },
  rationale: null,
  proposer: null,
  new_expert: null,
  metrics: [{ split: 'val', judge_version: 'jv_1', metrics: { coverage: { mean: 0.62, sd: null, n: 1, noise_sd: 0.02 } } }],
  holdout: { status: 'sealed', message: 'No published holdout decision: not submitted to holdout, or the decision is recorded in the sealed audit only.', decisions: [] },
  r37: { status: 'not_run', score: null, reference_version: SEED.version_id, reference_score: null, regression: null, message: 'R37 diff check not run for this version' },
});

export const details: Record<string, CandidateDetail> = {
  [SEED.version_id]: { ...base(SEED), summary: null },
  [PROMPT.version_id]: base(PROMPT),
  [TOPOLOGY.version_id]: {
    ...base(TOPOLOGY),
    summary: { experts_added: ['workforce'], added_experts: { workforce: { domain: 'workforce', router_gloss: 'effects on workers' } }, prompts_changed: [], router_gloss_changed: {}, retrieval: null },
    new_expert: {
      id: 'workforce',
      domain: 'workforce',
      router_gloss: 'effects on workers, skills, staffing and employment',
      prompt_text: 'You are the Workforce expert.',
      target_pattern: { kind: 'missed_impact', category: 'social_environmental', owner: 'none' },
      rationale: 'Workforce impacts are missed in 2 proposals and no expert owns them.',
    },
    holdout: { status: 'published', message: '', decisions: [weakDecision] },
    r37: { status: 'available', score: { mean: 0.61, sd: 0.02, n: 3 }, reference_version: SEED.version_id, reference_score: { mean: 0.6, sd: 0.02, n: 3 }, regression: false, message: 'monitoring only: never gates a promotion' },
  },
  [REJECTED.version_id]: {
    ...base(REJECTED),
    holdout: {
      status: 'published',
      message: '',
      decisions: [{ ...weakDecision, gate_id: 'gate_2', candidate_version: REJECTED.version_id, mode: 'dev', deployable: false, label: REJECTED.label! }],
    },
    r37: { status: 'available', score: { mean: 0.31, sd: 0.01, n: 3 }, reference_version: SEED.version_id, reference_score: { mean: 0.6, sd: 0.02, n: 3 }, regression: true, message: 'regression on the R37 diff check (monitoring only: the decision is unchanged)' },
  },
};

export const diffs: Record<string, CandidateDiff> = {
  [PROMPT.version_id]: {
    version_id: PROMPT.version_id,
    parent_id: SEED.version_id,
    summary: details[PROMPT.version_id].summary,
    prompts: { 'expert:fiscal': '--- prompts/fiscal.md\n+++ prompts/evolved/sv_seed/expert-fiscal.md\n@@ -1,2 +1,3 @@\n You are the Fiscal expert.\n-Old line.\n+Quantify every cost you name.' },
  },
};
