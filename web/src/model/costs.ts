// View-models for the dossier's Costs tab (EU cost plan R5). Rankings come from the API; this
// module only labels and filters. Bands are words, never money.
import type { CostBand, CostHotspot, CostRecord, CostSection, HotspotDimension, ImpactDossier, ImpactFinding, Recurrence } from '../types';

export const EFFORT_LABEL: Record<string, string> = {
  new_process: 'New process',
  documentation: 'Documentation',
  registration: 'Registration',
  notification: 'Notification',
  human_oversight: 'Human oversight',
  training: 'Training',
  assessment: 'Assessment',
};

export const BANDS: CostBand[] = ['negligible', 'low', 'medium', 'high'];

const words = (s: string) => {
  const t = s.replace(/_/g, ' ');
  return t ? t[0].toUpperCase() + t.slice(1) : t;
};

export const bandWord = (b: CostBand | null | undefined): string => (b ? words(b) : '—');
export const effortLabel = (e: string | null | undefined): string => (e ? EFFORT_LABEL[e] ?? words(e) : '—');

export function payerText(r: CostRecord): string {
  if (!r.payer || r.payer_basis === 'unknown') return 'Payer not identified';
  const name = words(r.payer);
  if (r.payer_basis === 'inferred') return `${name} · inferred from the text`;
  if (r.payer_basis === 'rule_table') return `${name} · by rule${r.payer_legal_basis ? ` (${r.payer_legal_basis})` : ''}`;
  return name;
}

const WITHHELD: Record<string, string> = {
  amended_2026: 'amended 2026',
  date_moved_2026: 'moved 2026',
  annex: 'applies through the articles',
  proposal: 'a proposal never applied',
};

/** The date a record applies from, only with its label; a withheld date is never shown. */
export function dateText(r: CostRecord): string {
  if (r.date_withheld) return `date withheld (${WITHHELD[r.date_withheld] ?? r.date_withheld})`;
  if (r.applies_from && r.date_label) return `${r.applies_from} (${r.date_label})`;
  return '—';
}

export interface HotspotRow extends CostHotspot {
  label: string;
}

export function hotspotRows(s: CostSection, dimension: HotspotDimension, recurrence: Recurrence): HotspotRow[] {
  return s.hotspots
    .filter((h) => h.dimension === dimension && h.recurrence === recurrence)
    .map((h) => ({ ...h, label: dimension === 'effort_type' ? effortLabel(h.value) : dimension === 'payer' ? words(h.value) : h.value }));
}

export interface CostFilter {
  payer: string;
  effort: string;
  band: CostBand | 'all';
  change: 'all' | 'changed' | 'added';
}

export const NO_FILTER: CostFilter = { payer: 'all', effort: 'all', band: 'all', change: 'all' };

export function filterRecords(records: CostRecord[], f: CostFilter): CostRecord[] {
  return records.filter(
    (r) =>
      (f.payer === 'all' || (r.payer ?? 'unknown') === f.payer) &&
      (f.effort === 'all' || r.effort_type === f.effort) &&
      (f.band === 'all' || r.one_off === f.band || r.recurring === f.band) &&
      (f.change === 'all' || (f.change === 'added' ? r.late_added : r.changed_after_proposal)),
  );
}

export function filterOptions(records: CostRecord[]): { payers: string[]; efforts: string[] } {
  const payers = [...new Set(records.map((r) => r.payer ?? 'unknown'))];
  const efforts = [...new Set(records.map((r) => r.effort_type).filter((e): e is NonNullable<typeof e> => !!e))];
  return { payers, efforts };
}

/** Fiscal findings of the dossier's impacts, by provision key (shown next to cost records). */
export function fiscalFindingsByKey(d: ImpactDossier): Record<string, ImpactFinding[]> {
  const out: Record<string, ImpactFinding[]> = {};
  for (const imp of d.impacts) for (const f of imp.findings) if (f.agent === 'fiscal') (out[f.provision_key] ??= []).push(f);
  return out;
}
