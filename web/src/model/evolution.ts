// View models for the evolution page (R36 page 4). Pure presentation: decisions, labels,
// reasons and the R37 message come from the API exactly as the promotion gate wrote them.
import { AMBER, GREEN, RED } from '../design';
import type { CandidateDetail, DiffSummary, LineageNode, MetricDeltaOut } from '../types';

export interface TreeRow {
  node: LineageNode;
  depth: number;
}

/** Depth-first rows of the lineage forest: roots oldest first, children under their parent. */
export function treeRows(nodes: LineageNode[]): TreeRow[] {
  const ids = new Set(nodes.map((n) => n.version_id));
  const children = new Map<string, LineageNode[]>();
  const roots: LineageNode[] = [];
  for (const n of nodes) {
    if (n.parent_id && ids.has(n.parent_id)) children.set(n.parent_id, [...(children.get(n.parent_id) ?? []), n]);
    else roots.push(n);
  }
  const rows: TreeRow[] = [];
  const seen = new Set<string>();
  const walk = (n: LineageNode, depth: number) => {
    if (seen.has(n.version_id)) return;
    seen.add(n.version_id);
    rows.push({ node: n, depth });
    for (const c of children.get(n.version_id) ?? []) walk(c, depth + 1);
  };
  roots.forEach((r) => walk(r, 0));
  return rows;
}

/** The node opened first: the latest one with a decision, else the latest one. */
export function defaultSelection(nodes: LineageNode[]): string | null {
  const decided = nodes.filter((n) => n.decision);
  return (decided.at(-1) ?? nodes.at(-1))?.version_id ?? null;
}

const NEUTRAL = '#8A9699';
const VIOLET = '#8B5CF6';
const TEAL = '#00737A';

export const BADGES: Record<string, { t: string; c: string }> = {
  seed: { t: 'Seed', c: NEUTRAL },
  gepa: { t: 'Prompt edit', c: TEAL },
  topology: { t: 'New expert', c: VIOLET },
  manual: { t: 'Manual', c: NEUTRAL },
  twin: { t: 'api twin', c: NEUTRAL },
  promoted: { t: 'Promoted', c: GREEN },
  rejected: { t: 'Rejected', c: RED },
  'dev-only': { t: 'Dev-only', c: AMBER },
  r37: { t: 'R37 regression', c: AMBER },
};

export const badge = (b: string): { t: string; c: string } => BADGES[b] ?? { t: b, c: NEUTRAL };

export const METRIC_NAMES: Record<string, string> = {
  coverage: 'Coverage',
  omissions_addressed: 'Omissions addressed',
  grounding: 'Grounding',
};

export const metricName = (m: string): string => METRIC_NAMES[m] ?? m.replace(/_/g, ' ');

export const shortId = (id: string): string => (id.length > 15 ? id.slice(0, 15) : id);

export function fmtNum(x: number | null | undefined, digits = 3): string {
  return x === null || x === undefined || !Number.isFinite(x) ? '—' : x.toFixed(digits);
}

export function fmtDelta(x: number | null | undefined): string {
  if (x === null || x === undefined || !Number.isFinite(x)) return '—';
  return (x > 0 ? '+' : x < 0 ? '−' : '±') + Math.abs(x).toFixed(3);
}

export function ciText(d: MetricDeltaOut): string {
  if (d.ci95_low === null || d.ci95_high === null) return 'CI not available';
  return `CI95 [${fmtDelta(d.ci95_low)}, ${fmtDelta(d.ci95_high)}]`;
}

/** "0.700 ± 0.020" with the noise band, or the mean alone. */
export function withNoise(mean: number | null, noise: number | null | undefined): string {
  return noise === null || noise === undefined ? fmtNum(mean) : `${fmtNum(mean)} ± ${fmtNum(noise)}`;
}

/** One line per structural change of a candidate's config diff. */
export function summaryLines(s: DiffSummary | null): string[] {
  if (!s) return [];
  const out: string[] = [];
  for (const id of s.experts_added) {
    const e = s.added_experts[id];
    out.push(`Expert added: ${id}${e?.domain && e.domain !== id ? ` (${e.domain})` : ''}`);
  }
  if (s.prompts_changed.length) out.push(`Prompts changed: ${s.prompts_changed.join(', ')}`);
  for (const [id, g] of Object.entries(s.router_gloss_changed)) out.push(`Router gloss of ${id}: ${g.from ?? '(none)'} → ${g.to ?? '(none)'}`);
  if (s.retrieval) {
    const keys = Object.keys(s.retrieval.to).filter((k) => s.retrieval!.to[k] !== s.retrieval!.from[k]);
    out.push(`Research policy: ${keys.map((k) => `${k} ${s.retrieval!.from[k]} → ${s.retrieval!.to[k]}`).join(', ')}`);
  }
  return out;
}

/** The Failure Memory pattern a new expert targets, as a phrase. */
export function patternText(p: Record<string, string> | null): string | null {
  if (!p) return null;
  const owner = p.owner === 'none' ? 'no expert owns it' : `owner ${p.owner}`;
  return `${(p.kind ?? '').replace(/_/g, ' ')} · ${(p.category ?? 'uncategorised').replace(/_/g, ' ')} · ${owner}`;
}

/** Text of the R37 comparison, e.g. "0.310 vs 0.600 (sv_… )". */
export function r37Line(d: CandidateDetail['r37']): string {
  if (d.status !== 'available' || !d.score) return 'not run';
  const ref = d.reference_score ? ` vs ${fmtNum(d.reference_score.mean)} (${d.reference_version ? shortId(d.reference_version) : 'reference'})` : '';
  return `${withNoise(d.score.mean, d.score.sd)} over ${d.score.n} run${d.score.n === 1 ? '' : 's'}${ref}`;
}

export const MODE_TEXT: Record<string, string> = {
  statistical: 'Statistical (bootstrap CI)',
  weak: 'Weak threshold (directional)',
  dev: 'Dev (not every role on the api backend; not deployable)',
};

export const lineDiffKind = (line: string): 'add' | 'del' | 'hunk' | 'meta' | 'ctx' =>
  line.startsWith('+++') || line.startsWith('---') ? 'meta' : line.startsWith('@@') ? 'hunk' : line.startsWith('+') ? 'add' : line.startsWith('-') ? 'del' : 'ctx';

/** Lines of one prompt diff rendered before a "show more" (long prompts stay responsive). */
export const DIFF_LINE_CAP = 200;

export function capLines(text: string, cap: number): { lines: string[]; hidden: number } {
  const all = text.split('\n');
  return all.length <= cap ? { lines: all, hidden: 0 } : { lines: all.slice(0, cap), hidden: all.length - cap };
}
