import { describe, expect, it } from 'vitest';
import type { CostRecord, CostSection, ImpactDossier } from '../types';
import { bandWord, dateText, filterOptions, filterRecords, fiscalFindingsByKey, hotspotRows, payerText, NO_FILTER } from './costs';
import { buildTrace, nodeId } from './trace';
import { colIds, edges, hasCost, roleOf } from './pipeline';

const rec = (over: Partial<CostRecord>): CostRecord => ({
  obligation_id: 'o1',
  unit_id: 'u1',
  provision_key: 'ai_act/art/26',
  source_id: 'reg2024_1689/obligations/art_26',
  records_version: 'reg2024_1689',
  status: 'estimated',
  payer: 'deployer',
  payer_basis: 'rule_field',
  secondary_types: [],
  effort_type: 'human_oversight',
  one_off: null,
  recurring: 'medium',
  changed_after_proposal: false,
  late_added: false,
  ...over,
});

const section = (records: CostRecord[]): CostSection => ({
  records,
  hotspots: [
    { dimension: 'provision', value: 'ai_act/art/26', recurrence: 'recurring', medium_or_high: 2, low: 1, records: 3, provision_keys: ['ai_act/art/26'] },
    { dimension: 'provision', value: 'ai_act/art/50', recurrence: 'recurring', medium_or_high: 0, low: 1, records: 1, provision_keys: ['ai_act/art/50'] },
    { dimension: 'effort_type', value: 'human_oversight', recurrence: 'one_off', medium_or_high: 1, low: 0, records: 1, provision_keys: ['ai_act/art/26'] },
  ],
  coverage: { relevant: records.length, estimated: records.length, not_costed: 0, not_estimated: 0, invalid_dropped: 0, not_covered_keys: [], payers_by_basis: {} },
  late_added: [],
  delta_basis: null,
  notes: [],
});

describe('cost column in the pipeline', () => {
  const ev = (seq: number, node: string, event: string, at: string) => ({ seq, node, event, payload: {}, at });
  const base = [ev(1, 'planner', 'started', '2026-10-07T10:00:00Z'), ev(2, 'planner', 'finished', '2026-10-07T10:00:01Z')];
  const withCost = [...base, ev(3, 'cost', 'started', '2026-10-07T10:00:02Z'), ev(4, 'cost', 'finished', '2026-10-07T10:00:03Z')];

  it('maps the cost node and shows its column only when the trace has a cost step', () => {
    expect(nodeId('cost')).toBe('cost');
    const plain = buildTrace({ events: base, experts: ['legal'] });
    const costed = buildTrace({ events: withCost, experts: ['legal'] });
    expect(hasCost(plain)).toBe(false);
    expect(hasCost(costed)).toBe(true);
    expect(colIds(['legal']).flat()).not.toContain('cost');
    const cols = colIds(['legal'], true).map((c) => c.join());
    expect(cols.slice(cols.indexOf('citation'), cols.indexOf('citation') + 3)).toEqual(['citation', 'cost', 'synthesis']);
    expect(edges(['legal'], true)).toContainEqual(['citation', 'cost']);
    expect(edges(['legal'], true)).toContainEqual(['cost', 'synthesis']);
    expect(edges(['legal'], true)).not.toContainEqual(['citation', 'synthesis']);
    expect(roleOf('cost')).toBe('cost');
  });
});

describe('cost section view-models', () => {
  it('keeps the API ranking order and labels effort types', () => {
    const s = section([]);
    expect(hotspotRows(s, 'provision', 'recurring').map((r) => r.value)).toEqual(['ai_act/art/26', 'ai_act/art/50']);
    expect(hotspotRows(s, 'provision', 'one_off')).toEqual([]);
    expect(hotspotRows(s, 'effort_type', 'one_off')[0].label).toBe('Human oversight');
  });

  it('shows bands as words, never euros', () => {
    expect(bandWord('high')).toBe('High');
    expect(bandWord(null)).toBe('—');
    expect(bandWord('negligible')).not.toMatch(/EUR|€/);
  });

  it('marks inferred and unknown payers visibly', () => {
    expect(payerText(rec({}))).toBe('Deployer');
    expect(payerText(rec({ payer: 'provider', payer_basis: 'rule_table', payer_legal_basis: 'Art 16(a)' }))).toBe('Provider · by rule (Art 16(a))');
    expect(payerText(rec({ payer: 'member_state', payer_basis: 'inferred' }))).toBe('Member state · inferred from the text');
    expect(payerText(rec({ payer: null, payer_basis: 'unknown' }))).toBe('Payer not identified');
  });

  it('filters by payer, effort type, band and change after the proposal', () => {
    const records = [
      rec({ obligation_id: 'a', late_added: true, changed_after_proposal: true }),
      rec({ obligation_id: 'b', changed_after_proposal: true, payer: 'provider', effort_type: 'documentation', recurring: null, one_off: 'low' }),
      rec({ obligation_id: 'c' }),
    ];
    const ids = (f: Partial<typeof NO_FILTER>) => filterRecords(records, { ...NO_FILTER, ...f }).map((r) => r.obligation_id);
    expect(ids({})).toEqual(['a', 'b', 'c']);
    expect(ids({ change: 'added' })).toEqual(['a']);
    expect(ids({ change: 'changed' })).toEqual(['a', 'b']);
    expect(ids({ payer: 'provider' })).toEqual(['b']);
    expect(ids({ effort: 'human_oversight' })).toEqual(['a', 'c']);
    expect(ids({ band: 'low' })).toEqual(['b']);
    expect(ids({ band: 'medium' })).toEqual(['a', 'c']);
    const opts = filterOptions(records);
    expect(opts.payers).toEqual(['deployer', 'provider']);
    expect(opts.efforts).toEqual(['human_oversight', 'documentation']);
  });

  it('never shows a withheld date', () => {
    expect(dateText(rec({ applies_from: '2026-08-02', date_label: 'as adopted (2024)' }))).toBe('2026-08-02 (as adopted (2024))');
    expect(dateText(rec({ applies_from: null, date_withheld: 'amended_2026' }))).toBe('date withheld (amended 2026)');
    expect(dateText(rec({ applies_from: null, date_withheld: 'date_moved_2026' }))).toBe('date withheld (moved 2026)');
    expect(dateText(rec({ applies_from: '2026-08-02', date_label: null }))).toBe('—');
  });

  it('joins Fiscal findings on the same provision key', () => {
    const finding = (agent: string, key: string, impact: string) => ({ finding_id: `${agent}-${key}`, agent, provision_key: key, impact }) as never;
    const d = { impacts: [{ impact_id: 'I1', summary: 's', merged: true, findings: [finding('fiscal', 'k1', 'cost'), finding('legal', 'k1', 'duty')] }] } as unknown as ImpactDossier;
    const by = fiscalFindingsByKey(d);
    expect(Object.keys(by)).toEqual(['k1']);
    expect(by.k1.map((f) => f.impact)).toEqual(['cost']);
  });
});
