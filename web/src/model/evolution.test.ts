import { describe, expect, it } from 'vitest';
import { badge, ciText, defaultSelection, fmtDelta, lineDiffKind, patternText, r37Line, summaryLines, treeRows, withNoise } from './evolution';
import { PROMPT, REJECTED, SEED, TOPOLOGY, details, lineage } from '../test/evolution';

describe('evolution view models', () => {
  it('orders the lineage depth first with children under their parent', () => {
    const rows = treeRows(lineage().nodes);
    expect(rows.map((r) => [r.node.version_id, r.depth])).toEqual([
      [SEED.version_id, 0],
      [PROMPT.version_id, 1],
      [TOPOLOGY.version_id, 2],
      [REJECTED.version_id, 1],
    ]);
  });

  it('treats a node whose parent is not archived as a root', () => {
    const orphan = { ...PROMPT, parent_id: 'sv_missing' };
    expect(treeRows([orphan]).map((r) => r.depth)).toEqual([0]);
  });

  it('opens the latest decided candidate first', () => {
    expect(defaultSelection(lineage().nodes)).toBe(REJECTED.version_id);
    expect(defaultSelection([SEED, PROMPT])).toBe(PROMPT.version_id);
    expect(defaultSelection([])).toBeNull();
  });

  it('formats deltas, intervals and noise bands', () => {
    expect(fmtDelta(0.06)).toBe('+0.060');
    expect(fmtDelta(-0.05)).toBe('−0.050');
    expect(fmtDelta(null)).toBe('—');
    expect(ciText({ mean_delta: 0.08, ci95_low: 0.02, ci95_high: 0.14, n_cases: 8 })).toBe('CI95 [+0.020, +0.140]');
    expect(ciText({ mean_delta: 0.08, ci95_low: null, ci95_high: null, n_cases: 8 })).toBe('CI not available');
    expect(withNoise(0.7, 0.02)).toBe('0.700 ± 0.020');
    expect(withNoise(0.7, null)).toBe('0.700');
  });

  it('summarises a config diff structurally', () => {
    expect(summaryLines(details[TOPOLOGY.version_id].summary)).toEqual(['Expert added: workforce']);
    expect(summaryLines(details[PROMPT.version_id].summary)).toEqual(['Prompts changed: expert:fiscal']);
    expect(
      summaryLines({ experts_added: [], added_experts: {}, prompts_changed: [], router_gloss_changed: { legal: { from: null, to: 'law' } }, retrieval: { from: { max_provisions: 8, max_prompt_chars: 40000 }, to: { max_provisions: 12, max_prompt_chars: 40000 } } }),
    ).toEqual(['Router gloss of legal: (none) → law', 'Research policy: max_provisions 8 → 12']);
    expect(summaryLines(null)).toEqual([]);
  });

  it('names the targeted Failure Memory pattern and the R37 comparison', () => {
    expect(patternText({ kind: 'missed_impact', category: 'social_environmental', owner: 'none' })).toBe('missed impact · social environmental · no expert owns it');
    expect(r37Line(details[REJECTED.version_id].r37)).toBe('0.310 ± 0.010 over 3 runs vs 0.600 (sv_seed00000001)');
    expect(r37Line(details[PROMPT.version_id].r37)).toBe('not run');
  });

  it('labels badges and diff lines', () => {
    expect(badge('topology').t).toBe('New expert');
    expect(badge('dev-only').t).toBe('Dev-only');
    expect(badge('something').t).toBe('something');
    expect(['+++ b', '--- a', '@@ -1 +1 @@', '+x', '-y', ' z'].map(lineDiffKind)).toEqual(['meta', 'meta', 'hunk', 'add', 'del', 'ctx']);
  });
});
