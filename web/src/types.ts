// Shapes of the WOMM API (docs/ui/api-contract.md, docs/ui/schema/*.json).

export type RunStatus = 'queued' | 'running' | 'succeeded' | 'degraded' | 'failed' | 'no_changes';
export type ErrorKind = 'auth' | 'rate_limit' | 'timeout' | 'schema_invalid' | 'process_error' | 'no_data_in_scope' | string;
export type RouterMode = 'off' | 'shadow' | 'active';

export interface Provenance {
  agent: string;
  system_version: string;
  prompt_hash: string;
  backend: string;
  model: string;
  round?: number;
}

export interface Evidence {
  evidence_id: string;
  source_id: string;
  quote: string;
}

export interface ImpactFinding {
  finding_id: string;
  agent: string;
  provision_key: string;
  affected_actor: string;
  impact: string;
  mechanism: string;
  evidence: Evidence[];
  confidence: number;
  provenance: Provenance;
}

export interface ExpertFailure {
  agent: string;
  error_kind: ErrorKind;
  message: string;
  attempts?: number;
}

export interface DossierImpact {
  impact_id: string;
  summary: string;
  findings: ImpactFinding[];
  merged: boolean;
}

export interface ImpactChain {
  impact_ids: string[];
  description: string;
}

export interface Disagreement {
  finding_ids: string[];
  note: string;
}

export interface OpenQuestion {
  question: string;
  finding_id?: string | null;
  reason: 'evidence_unresolved' | 'synthesis';
}

export interface ImpactDossier {
  run_id: string;
  scenario_id: string;
  status: 'succeeded' | 'degraded' | 'failed' | 'no_changes';
  system_version: string;
  impacts: DossierImpact[];
  chains: ImpactChain[];
  disagreements: Disagreement[];
  open_questions: OpenQuestion[];
  discarded: { finding: ImpactFinding; reason: string }[];
  unprocessed: ImpactFinding[];
  failed_experts: ExpertFailure[];
  notes: string[];
}

export interface DecisionRecord {
  decision_point?: string;
  subject: string;
  input_summary?: string;
  decision: string;
  probability: number | null;
  mode: RouterMode;
  decider: 'jev' | 'stub' | string;
  system_version?: string;
  error?: string | null;
  truncated?: boolean;
  created_at?: string;
}

export interface CallUsage {
  role: string;
  agent?: string | null;
  backend: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd?: number | null;
  latency_s: number;
}

export interface Grounding {
  passed: number;
  total: number;
}

export interface CodeIdentity {
  git_sha: string | null;
  dirty: boolean;
  claude_cli_version?: string | null;
}

export interface RunDetail {
  run_id: string;
  scenario_id: string;
  status: RunStatus;
  system_version: string;
  error_kind?: string | null;
  error?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  nodes?: Record<string, string>;
  decisions?: DecisionRecord[] | null;
  grounding?: Grounding | null;
  board?: ImpactFinding[] | null;
  failures?: ExpertFailure[] | null;
  usage?: CallUsage[] | null;
  code_identity?: CodeIdentity | null;
  dossier?: ImpactDossier | null;
  /** Every source an expert of the run could cite (texts, obligation views, memorandum). Absent on older runs. */
  citable_sources?: SourceDoc[] | null;
}

export interface RunSummary {
  run_id: string;
  scenario_id: string;
  status: RunStatus;
  system_version: string;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  duration_s?: number | null;
  impacts?: number | null;
  grounding?: Grounding | null;
  error_kind?: string | null;
}

export interface EventPayload {
  findings?: Record<string, number>;
  failures?: Record<string, string>;
  decisions?: DecisionRecord[];
  dispatched?: string[];
  focus_areas?: number;
  grounding?: Grounding;
  supported?: number;
  unsupported?: number;
  error?: string;
  status?: string;
  impacts?: number;
}

export interface RunEvent {
  seq: number;
  node: string;
  event: 'started' | 'finished' | 'failed' | string;
  payload: EventPayload;
  at: string;
}

export interface EventsPage {
  run_id: string;
  events: RunEvent[];
  next_after: number;
}

export interface Scenario {
  scenario_id: string;
  kind: 'evaluation' | 'demo' | string;
  description: string;
  before_version: string | null;
  after_version: string;
  provision_keys: string[];
  ia_reference: string | null;
}

export interface ProvisionRef {
  article: string;
  source_id: string;
}

export interface ProvisionChange {
  provision_key: string;
  kind: 'added' | 'modified' | 'removed' | string;
  before: ProvisionRef | null;
  after: ProvisionRef | null;
}

export interface SourceDoc {
  source_id: string;
  title: string;
  kind: string;
  text: string;
}

export interface ScenarioSources {
  scenario_id: string;
  changes: ProvisionChange[];
  sources: SourceDoc[];
}

export interface RoleSpec {
  backend: string;
  model: string;
  prompt?: string;
  prompt_hash?: string;
}

export interface SystemInfo {
  version_id: string;
  name: string;
  /** null for versions built in code rather than loaded from a YAML file. */
  source_path: string | null;
  description?: string;
  roles: Record<string, RoleSpec>;
  experts: { id: string; domain: string; backend: string; model: string }[];
  router: { mode: RouterMode; decider: string };
  max_parallel_llm_calls?: number;
  available_backends: string[];
  backend_ready: boolean;
  backend_error?: string | null;
  code?: CodeIdentity | null;
  self_check?: { passed: boolean; checks: Record<string, boolean>; problems: string[] } | null;
}

export interface RunOverrides {
  router_mode?: 'shadow' | 'active';
  backends?: Record<string, string>;
}

export interface RunAccepted {
  run_id: string;
  status: RunStatus;
  system_version?: string;
}

export interface AskAnswer {
  answer: string;
  cites: string[];
  covered: boolean;
}

// Evolution page (R36 page 4): docs/ui/api-contract.md "Evolution page".

export type EvolutionOrigin = 'seed' | 'gepa' | 'topology' | 'twin' | 'manual';
export type GateMode = 'statistical' | 'weak' | 'dev';

export interface LineageNode {
  version_id: string;
  name: string;
  parent_id: string | null;
  cycle_id: string | null;
  origin: EvolutionOrigin;
  created_at: string;
  badges: string[];
  twins: string[];
  decision: 'promoted' | 'rejected' | null;
  label: string | null;
  new_expert: string | null;
  r37_regression: boolean | null;
}

export interface Lineage {
  nodes: LineageNode[];
  publish_summary: boolean | null;
}

export interface MetricDeltaOut {
  mean_delta: number | null;
  ci95_low: number | null;
  ci95_high: number | null;
  n_cases: number;
  noise_sd?: number | null;
}

export interface PromotionDecision {
  gate_id: string;
  created_at: string;
  candidate_version: string;
  incumbent_version: string;
  mode: GateMode;
  deployable: boolean;
  decision: 'promoted' | 'rejected';
  label: string;
  reasons: string[];
  notes: string[];
  deltas: Record<string, MetricDeltaOut>;
  n_proposals: number;
  flags: string[];
}

export interface SplitMetrics {
  split: 'train' | 'val';
  judge_version: string;
  metrics: Record<string, { mean: number | null; sd: number | null; n: number; noise_sd: number | null }>;
}

export interface DiffScore {
  mean: number | null;
  sd: number | null;
  n: number;
}

export interface R37Check {
  status: 'available' | 'not_run';
  score: DiffScore | null;
  reference_version: string | null;
  reference_score: DiffScore | null;
  regression: boolean | null;
  message: string;
}

export interface NewExpert {
  id: string;
  domain: string;
  router_gloss: string;
  prompt_text: string;
  target_pattern: Record<string, string> | null;
  rationale: string | null;
}

export interface DiffSummary {
  experts_added: string[];
  added_experts: Record<string, { domain: string; router_gloss: string | null }>;
  prompts_changed: string[];
  router_gloss_changed: Record<string, { from: string | null; to: string | null }>;
  retrieval: { from: Record<string, number>; to: Record<string, number> } | null;
}

export interface CandidateDetail {
  node: LineageNode;
  experts: { id: string; domain: string; router_gloss: string | null }[];
  summary: DiffSummary | null;
  rationale: string | null;
  proposer: Record<string, unknown> | null;
  new_expert: NewExpert | null;
  metrics: SplitMetrics[];
  holdout: { status: 'published' | 'not_submitted' | 'sealed'; message: string; decisions: PromotionDecision[] };
  r37: R37Check;
}

export interface CandidateDiff {
  version_id: string;
  parent_id: string | null;
  summary: DiffSummary | null;
  prompts: Record<string, string>;
}
