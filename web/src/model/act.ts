// Act screen: whole-act comparison view models. Pure; every number comes from the API's
// ActComparison (rule-based obligation records) or from a run's dossier findings.
import type { ArticleComparison, ComparisonRef, ImpactFinding, ObligationSummary, ParagraphPair, RunDetail, RunSummary } from '../types';
import { diffWords, sideOf, type Segment } from './compare';

export type ActView = 'inline' | 'sbs' | 'map';

export type Measure = 'changed' | 'findings' | 'roleChanged' | 'dateMoved' | 'duties';

/** The role whose duties the screen counts (deployers: the role municipalities hold). */
export const ROLE = 'deployer';

export const MEASURES: { v: Measure; label: string }[] = [
  { v: 'changed', label: 'Paragraphs added, changed or removed' },
  { v: 'findings', label: 'Findings of the latest run' },
  { v: 'roleChanged', label: 'Deployer duties in changed paragraphs' },
  { v: 'dateMoved', label: 'Duties whose application date moved' },
  { v: 'duties', label: 'All duties and prohibitions' },
];

/** A withheld-date reason that means the date moved (not that the text changed). */
export const isMovedReason = (reason: string): boolean => reason.startsWith('date_moved');

export interface ArticleStats {
  key: string;
  paragraphs: number;
  changedParagraphs: number;
  /** Share of after-side (before-side if removed) characters in changed paragraphs, 0–100. */
  share: number;
  duties: number;
  roleDuties: number;
  roleChanged: number;
  dateMoved: number;
  findings: number;
}

const role = (s: ObligationSummary | null): number => s?.by_actor[ROLE] ?? 0;

/**
 * The obligation side to count (duties and withheld dates). An amended unit of a consolidated version has no
 * after-side records (corpus rule), so its duties are taken from the before side, where they
 * still describe what the text required.
 */
function counted(p: ParagraphPair): ObligationSummary | null {
  const a = p.after_obligations;
  return a && a.duties > 0 ? a : (p.before_obligations ?? a);
}

export function articleStats(a: ArticleComparison, findings = 0): ArticleStats {
  let chars = 0;
  let changedChars = 0;
  let changed = 0;
  let duties = 0;
  let roleDuties = 0;
  let roleChanged = 0;
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
    roleDuties += role(s);
    if (isChanged) roleChanged += role(s);
    for (const [reason, n] of Object.entries(s?.date_withheld ?? {})) if (isMovedReason(reason)) dateMoved += n;
  }
  return {
    key: a.provision_key,
    paragraphs: a.paragraphs.length,
    changedParagraphs: changed,
    share: chars ? Math.round((100 * changedChars) / chars) : 0,
    duties,
    roleDuties,
    roleChanged,
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
    case 'roleChanged':
      return s.roleChanged;
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
  roleDuties: number;
  dateMoved: number;
  withheld: string[];
}

export function paragraphVM(p: ParagraphPair): ParagraphVM {
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
    roleDuties: role(s),
    dateMoved: Object.entries(s?.date_withheld ?? {}).reduce((n, [r, k]) => n + (isMovedReason(r) ? k : 0), 0),
    withheld,
  };
}

/** Readable withheld-date reason: `date_moved_2026` -> "date moved (2026)". */
export function reasonText(reason: string): string {
  const m = reason.match(/^(.*)_(\d{4})$/);
  return m ? `${m[1].replace(/_/g, ' ')} (${m[2]})` : reason.replace(/_/g, ' ');
}
