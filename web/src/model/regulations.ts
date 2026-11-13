// Regulations screen: find an act by name, number or a pasted EUR-Lex link, and decide where it
// opens. Pure. Real entries come from GET /regulations; EXAMPLE_REGULATIONS are illustrative
// entries for acts WOMM has not imported, shown only when asked for and always labelled.
import type { ComparisonRef, RegulationEntry, RunStatus, RunSummary, ScenarioRef } from '../types';
import { comparisonKey } from './act';

/** Depth of what a reader can open for an act. */
export type Depth = 'comparison' | 'proposal' | 'none';

export interface CatalogueEntry {
  regulation_id: string;
  title: string;
  /** Short names and numbers people search for (AI Act, 2024/1689, COM(2021) 206 ...). */
  aliases: string[];
  celex: string[];
  versions: { label: string; status: string; date: string }[];
  depth: Depth;
  comparisons: ComparisonRef[];
  /** Scenarios whose finished runs are this act's analyses. */
  scenarios: ScenarioRef[];
  /** version_id -> label, to say what an analysis covered. */
  versionLabels: Record<string, string>;
  example: boolean;
}

export interface ActTarget {
  comparison: string | null;
  article: string | null;
  /** Whether `article` is numbered as in the before version (a pasted proposal link) or after. */
  side: 'before' | 'after';
}

const pad4 = (n: string | number) => String(n).padStart(4, '0');

/** Search names and numbers derived from an act's CELEX numbers and title. */
function aliasesFor(title: string, celex: string[]): string[] {
  const out = new Set<string>([title]);
  for (const c of celex) {
    let m = c.match(/^3(\d{4})([RLD])(\d{4})$/);
    if (m) out.add(`${m[1]}/${+m[3]}`);
    m = c.match(/^5(\d{4})PC(\d{4})$/);
    if (m) out.add(`COM(${m[1]}) ${+m[2]}`);
  }
  if (/artificial intelligence/i.test(title)) ['AI Act', 'AI-verordening'].forEach((a) => out.add(a));
  return [...out];
}

export function fromApi(e: RegulationEntry): CatalogueEntry {
  const celex = e.versions.map((v) => v.celex);
  return {
    regulation_id: e.regulation_id,
    title: e.title,
    aliases: aliasesFor(e.title, celex),
    celex,
    versions: e.versions.map((v) => ({ label: v.label, status: v.status, date: v.date })),
    depth: e.comparisons.length ? 'comparison' : e.versions.length ? 'proposal' : 'none',
    comparisons: e.comparisons,
    scenarios: e.scenarios ?? [],
    versionLabels: Object.fromEntries(e.versions.map((v) => [v.version_id, v.label])),
    example: false,
  };
}

const example = (regulation_id: string, title: string, celex: string, label: string, date: string, extra: string[] = []): CatalogueEntry => ({
  regulation_id,
  title,
  aliases: [...aliasesFor(title, [celex]), ...extra],
  celex: [celex],
  versions: [{ label, status: 'adopted', date }],
  depth: 'none',
  comparisons: [],
  scenarios: [],
  versionLabels: {},
  example: true,
});

/**
 * Illustrative entries (mock data) for acts WOMM has not imported. They show how the list reads
 * with more than one act, and never name an evaluation fixture.
 */
export const EXAMPLE_REGULATIONS: CatalogueEntry[] = [
  example('gdpr', 'General Data Protection Regulation', '32016R0679', 'Regulation (EU) 2016/679', '2016-04-27', ['GDPR', 'AVG']),
  example('nis2', 'NIS 2 Directive', '32022L2555', 'Directive (EU) 2022/2555', '2022-12-14', ['NIS2', 'Cyberbeveiligingswet']),
  example('dora', 'Digital Operational Resilience Act', '32022R2554', 'Regulation (EU) 2022/2554', '2022-12-14', ['DORA']),
  example('accessibility_act', 'European Accessibility Act', '32019L0882', 'Directive (EU) 2019/882', '2019-04-17', ['EAA']),
  example('open_data', 'Open Data Directive', '32019L1024', 'Directive (EU) 2019/1024', '2019-06-20', ['PSI']),
];

export type Query =
  | { kind: 'empty' }
  | { kind: 'text'; text: string }
  | { kind: 'id'; celex: string; article: string | null; consolidated: boolean };

/** Read what the user typed or pasted: a EUR-Lex/ELI link, a CELEX or COM number, or free text. */
export function parseQuery(raw: string): Query {
  let s = raw.trim();
  if (!s) return { kind: 'empty' };
  try {
    s = decodeURIComponent(s);
  } catch {
    /* keep as typed */
  }
  const hash = s.split('#')[1] ?? '';
  const article = (hash.match(/^art_(\d+[a-z]?)/) ?? hash.match(/^0*(\d+)\.\d{3}$/))?.[1] ?? s.match(/\b(?:art(?:icle|ikel)?\.?)\s*(\d+[a-z]?)/i)?.[1] ?? null;
  let m = s.match(/CELEX[:=]\s*([0-9][0-9A-Z()-]+)/i) ?? s.match(/\b([0-9]\d{4}[A-Z]{1,2}\d{4}(?:-\d{8})?)\b/);
  if (m) {
    const c = m[1].toUpperCase();
    const cons = c.match(/^0(\d{4}[A-Z]\d{4})-\d{8}$/);
    return { kind: 'id', celex: cons ? '3' + cons[1] : c, article, consolidated: !!cons };
  }
  m = s.match(/eli\/(reg|dir|dec)\/(\d{4})\/(\d+)/i);
  if (m) return { kind: 'id', celex: `3${m[2]}${{ reg: 'R', dir: 'L', dec: 'D' }[m[1].toLowerCase() as 'reg']}${pad4(m[3])}`, article, consolidated: false };
  m = s.match(/\bCOM\s*[(/]?\s*(\d{4})\s*[)/]?\s*(\d+)/i);
  if (m) return { kind: 'id', celex: `5${m[1]}PC${pad4(m[2])}`, article, consolidated: false };
  m = s.match(/\b(?:regulation|directive|verordening|richtlijn)?\s*\(?(?:eu|eg|ec)?\)?\s*(?:no\.?\s*)?(\d{4})\/(\d{1,4})\b/i);
  if (m) {
    const kind = /dir|richt/i.test(s) ? 'L' : 'R';
    return { kind: 'id', celex: `3${m[1]}${kind}${pad4(m[2])}`, article, consolidated: false };
  }
  return { kind: 'text', text: s };
}

const fold = (s: string) => s.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');

/** Entries matching the query, best first: exact number matches, then name matches. */
export function search(entries: CatalogueEntry[], q: Query): CatalogueEntry[] {
  if (q.kind === 'empty') return entries;
  if (q.kind === 'id') return entries.filter((e) => e.celex.includes(q.celex));
  const words = fold(q.text).split(/\s+/).filter(Boolean);
  const hay = (e: CatalogueEntry) => fold([e.title, e.regulation_id, ...e.aliases, ...e.celex, ...e.versions.map((v) => v.label)].join(' '));
  return entries.filter((e) => words.every((w) => hay(e).includes(w)));
}

/**
 * Where an entry opens. The default is the newest comparison (the law in force against its latest
 * change); a pasted proposal number opens proposal -> adopted text.
 */
export function targetFor(e: CatalogueEntry, q: Query): ActTarget | null {
  if (e.depth !== 'comparison') return null;
  const last = e.comparisons[e.comparisons.length - 1];
  let cmp = last;
  if (q.kind === 'id' && !q.consolidated) {
    const fromProposal = e.comparisons.find((c) => c.before.status === 'proposal' && c.before.celex === q.celex);
    if (fromProposal) cmp = fromProposal;
  }
  // Only a proposal numbers its articles differently from the adopted text.
  const side = q.kind === 'id' && cmp.before.status === 'proposal' && cmp.before.celex === q.celex ? 'before' : 'after';
  return { comparison: comparisonKey(cmp), article: q.kind === 'id' ? q.article : null, side };
}

const WITH_DOSSIER: RunStatus[] = ['succeeded', 'degraded'];

export interface AnalysisRow {
  runId: string;
  /** ISO time the run finished (or was created). */
  date: string | null;
  /** What was analysed, e.g. "COM(2021) 206 → Regulation (EU) 2024/1689". */
  what: string;
  description: string;
  impacts: number | null;
  /** Degraded runs are incomplete: an expert or the synthesis failed. */
  complete: boolean;
}

/** Scenario ids whose runs count as this act's analyses (comparisons and single versions). */
function analysisScenarios(e: CatalogueEntry): Map<string, ScenarioRef | null> {
  const m = new Map<string, ScenarioRef | null>(e.scenarios.map((s) => [s.scenario_id, s]));
  for (const id of e.comparisons.flatMap((c) => c.scenario_ids)) if (!m.has(id)) m.set(id, null);
  return m;
}

/** Finished analyses (runs with a dossier) of the act, newest first. */
export function analysesFor(e: CatalogueEntry, runs: RunSummary[] | undefined): AnalysisRow[] {
  const scen = analysisScenarios(e);
  const label = (v: string) => e.versionLabels[v] ?? v;
  const time = (r: RunSummary) => r.finished_at ?? r.created_at ?? null;
  return (runs ?? [])
    .filter((r) => scen.has(r.scenario_id) && WITH_DOSSIER.includes(r.status))
    .sort((a, b) => (Date.parse(time(b) ?? '') || 0) - (Date.parse(time(a) ?? '') || 0))
    .map((r) => {
      const s = scen.get(r.scenario_id) ?? null;
      const what = s ? (s.before_version ? `${label(s.before_version)} → ${label(s.after_version)}` : label(s.after_version)) : r.scenario_id;
      return { runId: r.run_id, date: time(r), what, description: s?.description ?? '', impacts: r.impacts ?? null, complete: r.status === 'succeeded' };
    });
}

export const analysesOf = (e: CatalogueEntry, runs: RunSummary[] | undefined): number => analysesFor(e, runs).length;

export const DEPTH_TEXT: Record<Depth, string> = {
  comparison: 'Versions compared',
  proposal: 'Proposal text only',
  none: 'Not analysed yet',
};
