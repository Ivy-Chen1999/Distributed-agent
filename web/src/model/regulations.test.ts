import { describe, expect, it } from 'vitest';
import { EXAMPLE_REGULATIONS, analysesFor, analysesOf, fromApi, parseQuery, search, targetFor } from './regulations';
import { AI_ACT } from '../test/regulations';

const ai = fromApi(AI_ACT);
const all = [ai, ...EXAMPLE_REGULATIONS];

describe('parseQuery', () => {
  it('reads EUR-Lex, ELI, CELEX, COM and act numbers, with article anchors', () => {
    expect(parseQuery('https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32024R1689#art_26')).toEqual({ kind: 'id', celex: '32024R1689', article: '26', consolidated: false });
    expect(parseQuery('https://eur-lex.europa.eu/legal-content/NL/TXT/HTML/?uri=CELEX:32024R1689#027.001')).toMatchObject({ celex: '32024R1689', article: '27' });
    expect(parseQuery('http://data.europa.eu/eli/reg/2024/1689/oj')).toMatchObject({ celex: '32024R1689' });
    expect(parseQuery('COM(2021) 206')).toMatchObject({ celex: '52021PC0206' });
    expect(parseQuery('Regulation (EU) 2024/1689 Article 50')).toMatchObject({ celex: '32024R1689', article: '50' });
    expect(parseQuery('Directive (EU) 2022/2555')).toMatchObject({ celex: '32022L2555' });
    expect(parseQuery('CELEX:02024R1689-20260727')).toMatchObject({ celex: '32024R1689', consolidated: true });
  });

  it('treats anything else as text, and blank as empty', () => {
    expect(parseQuery('  ')).toEqual({ kind: 'empty' });
    expect(parseQuery('AI Act')).toEqual({ kind: 'text', text: 'AI Act' });
  });
});

describe('search', () => {
  it('finds by short name, title words and numbers, ignoring case and accents', () => {
    expect(search(all, parseQuery('ai act')).map((e) => e.regulation_id)).toEqual(['ai_act']);
    expect(search(all, parseQuery('AI-verordening')).map((e) => e.regulation_id)).toEqual(['ai_act']);
    expect(search(all, parseQuery('GDPR')).map((e) => e.regulation_id)).toEqual(['gdpr']);
    expect(search(all, parseQuery('résilience')).map((e) => e.regulation_id)).toEqual(['dora']);
    expect(search(all, parseQuery('COM(2021) 206')).map((e) => e.regulation_id)).toEqual(['ai_act']);
    expect(search(all, parseQuery('2016/679')).map((e) => e.regulation_id)).toEqual(['gdpr']);
    expect(search(all, parseQuery('nothing like this'))).toEqual([]);
  });
});

describe('where an entry opens', () => {
  it('defaults to the newest comparison, the law in force against its latest change', () => {
    expect(targetFor(ai, parseQuery(''))).toEqual({ comparison: 'reg2024_1689->reg2024_1689_c20260727', article: null, side: 'after' });
  });

  it('a proposal link opens proposal -> adopted text, with the article numbered as in the proposal', () => {
    expect(targetFor(ai, parseQuery('https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:52021PC0206#art_29'))).toEqual({ comparison: 'com2021_206->reg2024_1689', article: '29', side: 'before' });
  });

  it('an adopted-text or consolidated link opens the amendment comparison', () => {
    expect(targetFor(ai, parseQuery('CELEX:32024R1689#art_26'))).toMatchObject({ comparison: 'reg2024_1689->reg2024_1689_c20260727', article: '26', side: 'after' });
  });

  it('example entries and acts without a comparison open nothing', () => {
    expect(targetFor(EXAMPLE_REGULATIONS[0], parseQuery(''))).toBeNull();
    expect(EXAMPLE_REGULATIONS.every((e) => e.example && e.depth === 'none')).toBe(true);
  });

  it('lists finished analyses newest first, saying what each covered and whether it is complete', () => {
    const run = (run_id: string, scenario_id: string, status: string, finished_at: string, impacts = 3) => ({ run_id, scenario_id, status, system_version: 'sv', finished_at, impacts }) as never;
    const rows = analysesFor(ai, [
      run('old', 'eval_sme_impacts', 'succeeded', '2026-11-01T10:00:00Z', 4),
      run('new', 'demo_penalties_amended', 'degraded', '2026-11-02T10:00:00Z'),
      run('gone', 'demo_penalties_amended', 'failed', '2026-11-03T10:00:00Z'),
      run('other', 'eval_cloud_switching', 'succeeded', '2026-11-04T10:00:00Z'),
    ]);
    expect(rows.map((r) => [r.runId, r.what, r.complete])).toEqual([
      ['new', 'COM(2021) 206 → Regulation (EU) 2024/1689', false],
      ['old', 'COM(2021) 206', true],
    ]);
    expect(rows[1].impacts).toBe(4);
  });

  it('counts finished analyses of the entry scenarios', () => {
    const runs = [
      { run_id: 'a', scenario_id: 'omnibus_2026', status: 'succeeded', system_version: 'sv' },
      { run_id: 'b', scenario_id: 'omnibus_2026', status: 'failed', system_version: 'sv' },
      { run_id: 'c', scenario_id: 'eval_sme_impacts', status: 'degraded', system_version: 'sv' },
    ] as const;
    expect(analysesOf(ai, [...runs])).toBe(2);
  });
});
