// Impact Dossier view-models: grouping, provenance chains, chains, disagreements, questions.
import type { DossierImpact, ImpactDossier, ImpactFinding, ProvisionChange } from '../types';
import { ag } from '../design';
import { areaLabel, articleLabel, changeFor } from './scenario';

export type GroupMode = 'area' | 'actor';

const ACTOR_RULES: [RegExp, string][] = [
  [/small|\bsmes?\b|start-?ups?|micro/i, 'Small providers'],
  [/authorit|member state|commission|\bboard\b|supervis|regulator|public bod/i, 'Authorities'],
  [/participant|sandbox/i, 'Participants'],
  [/provider|operator|deployer|user|importer|distributor|manufacturer/i, 'Providers and operators'],
  [/data subject|citizen|individual|person|consumer|worker|employee/i, 'Individuals'],
];

/** Coarse actor bucket, so "Affected actor" grouping stays readable with free-text actors. */
export function actorGroup(actor: string): string {
  for (const [re, label] of ACTOR_RULES) if (re.test(actor)) return label;
  const a = actor.trim();
  return a ? a[0].toUpperCase() + a.slice(1) : 'Unspecified actor';
}

export const capitalize = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);

/** Short chip label from an impact summary: the first few words at a word boundary. */
export function shortLabel(summary: string, max = 24): string {
  const clean = summary.replace(/\s+/g, ' ').trim().replace(/[.;:,]+$/, '');
  if (clean.length <= max) return clean;
  const cut = clean.slice(0, max + 1);
  const sp = cut.lastIndexOf(' ');
  return (sp > 8 ? cut.slice(0, sp) : clean.slice(0, max)).replace(/[,;:]$/, '') + '…';
}

export interface StepVM {
  k: string;
  v: string;
  mono?: boolean;
  quote?: { sourceId: string; quote: string };
}

export function findingSteps(f: ImpactFinding, changes: ProvisionChange[] | null | undefined): StepVM[] {
  const c = changeFor(f.provision_key, changes);
  const art = articleLabel(f.provision_key, changes);
  const change = [f.provision_key, art !== areaLabel(f.provision_key) ? art : null, c?.kind ?? null].filter(Boolean).join(' · ');
  const steps: StepVM[] = [
    { k: 'Regulatory change', v: change, mono: true },
    { k: 'Affected actor', v: capitalize(f.affected_actor) },
    { k: 'Mechanism', v: f.mechanism },
  ];
  for (const e of f.evidence) steps.push({ k: 'Evidence quote', v: e.quote, quote: { sourceId: e.source_id, quote: e.quote } });
  const sources = [...new Set(f.evidence.map((e) => e.source_id))];
  steps.push({ k: sources.length > 1 ? 'Sources' : 'Source', v: sources.join(', ') || '—', mono: true });
  return steps;
}

export interface ImpactVM {
  id: string;
  summary: string;
  key: string;
  conf: string;
  merged: string;
  agents: { n: string; c: string }[];
  findings: ImpactFinding[];
  primary: ImpactFinding | undefined;
}

export function impactVM(im: DossierImpact, changes: ProvisionChange[] | null | undefined): ImpactVM {
  const fs = im.findings;
  const agents = [...new Set(fs.map((f) => f.agent))].map((a) => ({ n: ag(a).n, c: ag(a).c }));
  const conf = fs.length ? fs.reduce((a, f) => a + f.confidence, 0) / fs.length : 0;
  const merged = !im.merged ? 'not merged · synthesis unavailable' : fs.length > 1 ? `merged from ${fs.length} findings` : 'single finding';
  const primary = fs[0];
  return {
    id: im.impact_id,
    summary: im.summary,
    key: primary ? articleLabel(primary.provision_key, changes) : '—',
    conf: conf.toFixed(2),
    merged,
    agents,
    findings: fs,
    primary,
  };
}

export interface GroupVM {
  label: string;
  meta: string;
  items: ImpactVM[];
}

export function groupImpacts(d: ImpactDossier, mode: GroupMode, changes: ProvisionChange[] | null | undefined): GroupVM[] {
  const vms = d.impacts.map((im) => impactVM(im, changes));
  const keyOf = (vm: ImpactVM) =>
    mode === 'area' ? (vm.primary?.provision_key ?? 'unknown') : actorGroup(vm.primary?.affected_actor ?? '');
  const order: string[] = [];
  const by = new Map<string, ImpactVM[]>();
  for (const vm of vms) {
    const k = keyOf(vm);
    if (!by.has(k)) {
      by.set(k, []);
      order.push(k);
    }
    by.get(k)!.push(vm);
  }
  return order.map((k) => {
    const items = by.get(k)!;
    return mode === 'area'
      ? { label: k === 'unknown' ? 'Other' : areaLabel(k), meta: k, items }
      : { label: k, meta: `${items.length} impact${items.length === 1 ? '' : 's'}`, items };
  });
}

/** finding_id → impact_id, for citations and disagreement links. */
export function findingIndex(d: ImpactDossier | null | undefined): Map<string, string> {
  const m = new Map<string, string>();
  for (const im of d?.impacts ?? []) for (const f of im.findings) m.set(f.finding_id, im.impact_id);
  return m;
}

export function allFindings(d: ImpactDossier | null | undefined, board?: ImpactFinding[] | null): Map<string, ImpactFinding> {
  const m = new Map<string, ImpactFinding>();
  for (const f of board ?? []) m.set(f.finding_id, f);
  for (const im of d?.impacts ?? []) for (const f of im.findings) m.set(f.finding_id, f);
  for (const f of d?.unprocessed ?? []) m.set(f.finding_id, f);
  for (const x of d?.discarded ?? []) m.set(x.finding.finding_id, x.finding);
  return m;
}

export interface ChainVM {
  desc: string;
  steps: { id: string; short: string; title: string; known: boolean }[];
}

export function chainVMs(d: ImpactDossier): ChainVM[] {
  const byId = new Map(d.impacts.map((i) => [i.impact_id, i]));
  return d.chains.map((c) => ({
    desc: c.description,
    steps: c.impact_ids.map((id) => {
      const im = byId.get(id);
      return { id, short: im ? shortLabel(im.summary) : '', title: im?.summary ?? 'Not in this dossier', known: !!im };
    }),
  }));
}

export interface DisagreementVM {
  heading: string;
  note: string;
  sides: { agentName: string; c: string; impact: string; id: string; conf: string; stance: string; impactId?: string }[];
}

export function disagreementVMs(d: ImpactDossier, board: ImpactFinding[] | null | undefined, changes: ProvisionChange[] | null | undefined): DisagreementVM[] {
  const fx = allFindings(d, board);
  const idx = findingIndex(d);
  return d.disagreements.map((dg) => {
    const fs = dg.finding_ids.map((id) => fx.get(id)).filter((f): f is ImpactFinding => !!f);
    const keys = [...new Set(fs.map((f) => f.provision_key))];
    const heading = keys.length
      ? keys.map((k) => `${articleLabel(k, changes)} · ${areaLabel(k).replace(/^\w/, (c) => c.toLowerCase()).replace(/^sme\b/i, 'SME')}`).join(' / ')
      : 'Disagreement';
    return {
      heading,
      note: dg.note,
      sides: fs.map((f, i) => ({
        agentName: ag(f.agent).n,
        c: ag(f.agent).c,
        impact: f.impact,
        id: f.finding_id,
        conf: f.confidence.toFixed(2),
        stance: `Reading ${String.fromCharCode(65 + i)}`,
        impactId: idx.get(f.finding_id),
      })),
    };
  });
}

export interface QuestionVM {
  n: number;
  q: string;
  tag: string;
  unresolved: boolean;
  findingId?: string | null;
}

export function questionVMs(d: ImpactDossier): QuestionVM[] {
  return d.open_questions.map((q, i) => ({
    n: i + 1,
    q: q.question,
    tag: q.reason === 'evidence_unresolved' ? 'Evidence unresolved' : 'Raised by synthesis',
    unresolved: q.reason === 'evidence_unresolved',
    findingId: q.finding_id,
  }));
}

/** Findings whose evidence the citation check could not find in the source. */
export function unresolvedIds(d: ImpactDossier | null | undefined): Set<string> {
  return new Set(
    (d?.open_questions ?? []).filter((q) => q.reason === 'evidence_unresolved' && q.finding_id).map((q) => q.finding_id as string),
  );
}

export function evidenceCount(d: ImpactDossier | null | undefined): number {
  return (d?.impacts ?? []).reduce((a, im) => a + im.findings.reduce((b, f) => b + f.evidence.length, 0), 0);
}
