// Act screen: whole-act comparison view models. Pure; every number comes from the API's
// ActComparison (rule-based obligation records) or from a run's dossier findings.
import type { ArticleComparison, ComparisonRef, ImpactFinding, ObligationSummary, ParagraphPair, RunDetail, RunSummary } from '../types';
import { diffWords, sideOf, type Segment } from './compare';

export type ActView = 'inline' | 'sbs' | 'map';

export type Measure = 'changed' | 'findings' | 'actorChanged' | 'dateMoved' | 'duties';

/**
 * The reader's role in the law: whose duties the screen counts. An actor is a `primary_actor` of
 * the obligation records (deployer, provider, ...), or `public` for every duty marked public
 * sector. The list comes from the data, so it follows whichever act is loaded.
 */
export const DEFAULT_ACTOR = 'deployer';
export const PUBLIC_ACTOR = 'public';
export const ACTOR_KEY = 'womm.actor';

/** Actors that are authorities or EU bodies, not parties that implement the law. */
const NOT_IMPLEMENTERS = new Set(['unspecified', 'commission', 'ai_office', 'board', 'member_state', 'market_surveillance_authority', 'national_competent_authority', 'notifying_authority', 'notified_body', 'scientific_panel', 'advisory_forum', 'edps', 'union_institutions',
  // Covered by the "Public body" option (every duty marked public sector), which is broader.
  'public_authority']);

const ACTOR_NAMES: Record<string, string> = {
  gpai_provider: 'Provider of a general-purpose AI model',
  authorised_representative: 'Authorised representative',
  product_manufacturer: 'Product manufacturer',
  [PUBLIC_ACTOR]: 'Public body',
};

export function actorLabel(actor: string): string {
  return ACTOR_NAMES[actor] ?? actor.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
}

/** Implementing actors that carry duties in the comparison, most duties first, then `public`. */
export function actorsIn(articles: ArticleComparison[]): string[] {
  const n = new Map<string, number>();
  for (const a of articles)
    for (const p of a.paragraphs)
      for (const side of [p.before_obligations, p.after_obligations])
        for (const [k, v] of Object.entries(side?.by_actor ?? {})) if (!NOT_IMPLEMENTERS.has(k)) n.set(k, (n.get(k) ?? 0) + v);
  return [...[...n].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([k]) => k), PUBLIC_ACTOR];
}

export function loadActor(): string {
  try {
    return localStorage.getItem(ACTOR_KEY) || DEFAULT_ACTOR;
  } catch {
    return DEFAULT_ACTOR;
  }
}

export function saveActor(actor: string): void {
  try {
    localStorage.setItem(ACTOR_KEY, actor);
  } catch {
    /* storage unavailable */
  }
}

export function measures(actor: string): { v: Measure; label: string }[] {
  return [
    { v: 'changed', label: 'Paragraphs added, changed or removed' },
    { v: 'findings', label: 'Findings of the latest run' },
    { v: 'actorChanged', label: `${actorLabel(actor)} duties in changed paragraphs` },
    { v: 'dateMoved', label: 'Duties whose application date moved' },
    { v: 'duties', label: 'All duties and prohibitions' },
  ];
}

/** A withheld-date reason that means the date moved (not that the text changed). */
export const isMovedReason = (reason: string): boolean => reason.startsWith('date_moved');

export interface ArticleStats {
  key: string;
  paragraphs: number;
  changedParagraphs: number;
  /** Share of after-side (before-side if removed) characters in changed paragraphs, 0–100. */
  share: number;
  duties: number;
  actorDuties: number;
  actorChanged: number;
  dateMoved: number;
  findings: number;
}

/** Duties of `actor` in one obligation side. */
export const actorCount = (s: ObligationSummary | null, actor: string): number => (actor === PUBLIC_ACTOR ? (s?.public_sector ?? 0) : (s?.by_actor[actor] ?? 0));

/**
 * The obligation side to count (duties and withheld dates). An amended unit of a consolidated version has no
 * after-side records (corpus rule), so its duties are taken from the before side, where they
 * still describe what the text required.
 */
function counted(p: ParagraphPair): ObligationSummary | null {
  const a = p.after_obligations;
  return a && a.duties > 0 ? a : (p.before_obligations ?? a);
}

export function articleStats(a: ArticleComparison, findings = 0, actor = DEFAULT_ACTOR): ArticleStats {
  let chars = 0;
  let changedChars = 0;
  let changed = 0;
  let duties = 0;
  let actorDuties = 0;
  let actorChanged = 0;
  let dateMoved = 0;
  for (const p of a.paragraphs) {
    const len = (p.after_text ?? p.before_text ?? '').length;
    chars += len;
    const isChanged = p.change !== 'unchanged';
    if (isChanged) {
      changed++;
      changedChars += len;
    }
    const s = counted(p);
    duties += s?.duties ?? 0;
    actorDuties += actorCount(s, actor);
    if (isChanged) actorChanged += actorCount(s, actor);
    for (const [reason, n] of Object.entries(s?.date_withheld ?? {})) if (isMovedReason(reason)) dateMoved += n;
  }
  return {
    key: a.provision_key,
    paragraphs: a.paragraphs.length,
    changedParagraphs: changed,
    share: chars ? Math.round((100 * changedChars) / chars) : 0,
    duties,
    actorDuties,
    actorChanged,
    dateMoved,
    findings,
  };
}

export function measureOf(s: ArticleStats, m: Measure): number {
  switch (m) {
    case 'changed':
      return s.changedParagraphs;
    case 'findings':
      return s.findings;
    case 'actorChanged':
      return s.actorChanged;
    case 'dateMoved':
      return s.dateMoved;
    case 'duties':
      return s.duties;
  }
}

/** Heat level 0–5 on a square-root scale, so one large article does not wash out the rest. */
export function heat(v: number, max: number): number {
  if (v <= 0 || max <= 0) return 0;
  return Math.min(5, Math.max(1, Math.ceil(5 * Math.sqrt(v / max))));
}

/** Tile fill per heat level, mixed from the theme accent so it works in both themes. */
export function heatFill(level: number): string {
  if (level === 0) return 'var(--soft)';
  const pct = [0, 18, 32, 48, 66, 85][level];
  return `color-mix(in srgb, var(--accInk) ${pct}%, var(--card))`;
}

/** Text on a tile stays readable: card colour on the two darkest levels. */
export const heatInk = (level: number): string => (level >= 4 ? 'var(--card)' : 'var(--ink)');

export const articleLabel = (a: Pick<ArticleComparison, 'kind' | 'number'>): string =>
  a.kind === 'annex' ? `Annex ${a.number}` : `Article ${a.number}`;

export const tileLabel = (a: Pick<ArticleComparison, 'kind' | 'number'>): string => (a.kind === 'annex' ? `Anx ${a.number}` : a.number);

export const comparisonKey = (c: Pick<ComparisonRef, 'before' | 'after'>): string => `${c.before.version_id}->${c.after.version_id}`;

export const comparisonLabel = (c: Pick<ComparisonRef, 'before' | 'after'>): string => `${c.before.label} → ${c.after.label}`;

/** One line under an article title: how it relates to the before version. */
export function relationText(a: ArticleComparison, c: Pick<ComparisonRef, 'before'>): string {
  if (a.change === 'added') return `New: not in ${c.before.label}`;
  if (a.change === 'removed') return `Removed: only in ${c.before.label}`;
  const renumbered = a.before_number && a.before_number !== a.number ? `was ${a.kind === 'annex' ? 'Annex' : 'Article'} ${a.before_number} · ` : '';
  return `${renumbered}${a.change === 'unchanged' ? 'text unchanged' : 'text changed'}`;
}

const FINISHED_WITH_DOSSIER = new Set(['succeeded', 'degraded']);

/** The newest finished run (with a dossier) of any scenario mapped onto this comparison. */
export function latestRunFor(runs: RunSummary[] | undefined, c: Pick<ComparisonRef, 'scenario_ids'> | null): RunSummary | null {
  if (!runs || !c) return null;
  const ids = new Set(c.scenario_ids);
  const ok = runs.filter((r) => ids.has(r.scenario_id) && FINISHED_WITH_DOSSIER.has(r.status));
  const time = (r: RunSummary) => Date.parse(r.finished_at ?? r.created_at ?? '') || 0;
  return ok.sort((a, b) => time(b) - time(a))[0] ?? null;
}

export interface LayerRun {
  run: RunSummary;
  /** "open": the analysis the user has open; "latest": the newest finished one. */
  source: 'open' | 'latest';
}

/**
 * The run whose findings the Act screen lays over the articles: the one the user has open, when it
 * is a finished run of this comparison, else the newest finished one.
 */
export function layerRunFor(runs: RunSummary[] | undefined, c: Pick<ComparisonRef, 'scenario_ids'> | null, openRunId: string | null): LayerRun | null {
  if (!runs || !c) return null;
  const ids = new Set(c.scenario_ids);
  const open = runs.find((r) => r.run_id === openRunId && ids.has(r.scenario_id) && FINISHED_WITH_DOSSIER.has(r.status));
  if (open) return { run: open, source: 'open' };
  const latest = latestRunFor(runs, c);
  return latest ? { run: latest, source: 'latest' } : null;
}

/** Findings of a run's dossier, grouped by the provision they cite (impact order kept). */
export function findingsByKey(run: RunDetail | null | undefined): Map<string, ImpactFinding[]> {
  const out = new Map<string, ImpactFinding[]>();
  for (const imp of run?.dossier?.impacts ?? []) {
    for (const f of imp.findings) {
      const list = out.get(f.provision_key) ?? [];
      list.push(f);
      out.set(f.provision_key, list);
    }
  }
  return out;
}

/** Findings that cite a provision the comparison does not contain (shown, not dropped). */
export function unplacedFindings(byKey: Map<string, ImpactFinding[]>, articles: ArticleComparison[]): ImpactFinding[] {
  const keys = new Set(articles.map((a) => a.provision_key));
  return [...byKey].filter(([k]) => !keys.has(k)).flatMap(([, f]) => f);
}

export interface ParagraphVM {
  label: string;
  change: ParagraphPair['change'];
  inline: Segment[];
  before: Segment[];
  after: Segment[];
  duties: number;
  actorDuties: number;
  dateMoved: number;
  withheld: string[];
}

export function paragraphVM(p: ParagraphPair, actor = DEFAULT_ACTOR): ParagraphVM {
  const b = p.before_text ?? '';
  const a = p.after_text ?? '';
  const segs = p.change === 'unchanged' ? (a ? [{ op: 'same' as const, text: a }] : []) : diffWords(b, a);
  const s = counted(p);
  const withheld = Object.keys(s?.date_withheld ?? {}).filter((r) => (s?.date_withheld[r] ?? 0) > 0);
  return {
    label: p.label,
    change: p.change,
    inline: segs,
    before: p.before_text === null ? [] : sideOf(segs, 'before'),
    after: p.after_text === null ? [] : sideOf(segs, 'after'),
    duties: s?.duties ?? 0,
    actorDuties: actorCount(s, actor),
    dateMoved: Object.entries(s?.date_withheld ?? {}).reduce((n, [r, k]) => n + (isMovedReason(r) ? k : 0), 0),
    withheld,
  };
}

/** Readable withheld-date reason: `date_moved_2026` -> "date moved (2026)". */
export function reasonText(reason: string): string {
  const m = reason.match(/^(.*)_(\d{4})$/);
  return m ? `${m[1].replace(/_/g, ' ')} (${m[2]})` : reason.replace(/_/g, ' ');
}
