import { describe, expect, it } from 'vitest';
import { articleStats, findingsByKey, heat, latestRunFor, measureOf, paragraphVM, reasonText, relationText, unplacedFindings } from './act';
import { COMPARISONS, OMNIBUS, art, ob } from '../test/act';
import { sampleRun } from '../test/fixtures';
import type { RunSummary } from '../types';

const byKey = (k: string) => OMNIBUS.articles.find((a) => a.provision_key === k)!;

describe('article stats', () => {
  it('an unchanged article keeps its deployer duties and counts their moved dates', () => {
    const s = articleStats(byKey('ai_act/art/26'));
    expect(s).toMatchObject({ changedParagraphs: 0, share: 0, duties: 3, roleDuties: 3, roleChanged: 0, dateMoved: 3 });
  });

  it('an amended article counts the before-side duties, since the after side borrows none', () => {
    const s = articleStats(byKey('ai_act/art/27'));
    expect(s.changedParagraphs).toBe(1);
    expect(s.duties).toBe(4);
    expect(s.roleDuties).toBe(3);
    expect(s.roleChanged).toBe(0); // the deployer duties sit in the unchanged paragraph
    expect(s.dateMoved).toBe(0); // "amended" is not a moved date
  });

  it('share weighs changed paragraphs by length', () => {
    const a = art({ paragraphs: [
      { label: '1', change: 'unchanged', before_text: 'x'.repeat(30), after_text: 'x'.repeat(30), before_obligations: ob(), after_obligations: ob() },
      { label: '2', change: 'added', before_text: null, after_text: 'y'.repeat(10), before_obligations: null, after_obligations: ob() },
    ] });
    expect(articleStats(a).share).toBe(25);
  });

  it('measures read the matching field', () => {
    const s = articleStats(byKey('ai_act/art/26'), 4);
    expect(measureOf(s, 'findings')).toBe(4);
    expect(measureOf(s, 'dateMoved')).toBe(3);
    expect(measureOf(s, 'changed')).toBe(0);
  });
});

describe('heat', () => {
  it('is 0 for nothing and 5 for the maximum, on a square-root scale', () => {
    expect(heat(0, 10)).toBe(0);
    expect(heat(10, 10)).toBe(5);
    expect(heat(1, 100)).toBe(1);
    expect(heat(25, 100)).toBe(3);
    expect(heat(3, 0)).toBe(0);
  });
});

describe('paragraph view model', () => {
  it('diffs a modified paragraph into before and after sides', () => {
    const vm = paragraphVM(byKey('ai_act/art/27').paragraphs[1]);
    expect(vm.inline.some((s) => s.op === 'add' && s.text.includes('simplified'))).toBe(true);
    expect(vm.before.map((s) => s.text).join('')).toBe('4. The AI Office shall develop a template.');
    expect(vm.after.map((s) => s.text).join('')).toBe('4. The AI Office shall develop a simplified template.');
    expect(vm.withheld).toEqual(['amended_2026']);
  });

  it('an added paragraph has an empty before side', () => {
    const vm = paragraphVM(byKey('ai_act/art/4a').paragraphs[0]);
    expect(vm.before).toEqual([]);
    expect(vm.after.map((s) => s.text).join('')).toContain('special categories');
  });

  it('reads withheld reasons', () => {
    expect(reasonText('date_moved_2026')).toBe('date moved (2026)');
    expect(reasonText('other')).toBe('other');
  });
});

describe('relation text', () => {
  it('names new, renumbered and unchanged articles', () => {
    const c = COMPARISONS[1];
    expect(relationText(byKey('ai_act/art/4a'), c)).toMatch(/^New: not in Regulation/);
    expect(relationText(byKey('ai_act/art/26'), c)).toBe('text unchanged');
    expect(relationText(art({ number: '57', before_number: '53', change: 'modified' }), c)).toBe('was Article 53 · text changed');
  });
});

describe('findings layer', () => {
  const run = (id: string, scenario_id: string, status: RunSummary['status'], finished_at: string): RunSummary => ({ run_id: id, scenario_id, status, system_version: 'sv', finished_at });

  it('takes the newest finished run of a mapped scenario', () => {
    const runs = [
      run('old', 'omnibus_2026', 'succeeded', '2026-11-01T10:00:00Z'),
      run('new', 'omnibus_2026', 'degraded', '2026-11-02T10:00:00Z'),
      run('failed', 'omnibus_2026', 'failed', '2026-11-03T10:00:00Z'),
      run('other', 'eval_sme_impacts', 'succeeded', '2026-11-04T10:00:00Z'),
    ];
    expect(latestRunFor(runs, COMPARISONS[1])?.run_id).toBe('new');
    expect(latestRunFor(runs, { scenario_ids: [] })).toBeNull();
  });

  it('groups dossier findings by provision and reports those outside the comparison', () => {
    const groups = findingsByKey(sampleRun());
    expect(groups.get('ai_act/innovation/regulatory_sandboxes')?.length).toBeGreaterThan(0);
    const outside = unplacedFindings(groups, [art({ provision_key: 'ai_act/innovation/regulatory_sandboxes' })]);
    const total = [...groups.values()].reduce((n, f) => n + f.length, 0);
    expect(outside.length).toBe(total - groups.get('ai_act/innovation/regulatory_sandboxes')!.length);
  });
});
