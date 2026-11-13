import { describe, expect, it } from 'vitest';
import { evalBasis, evalRows } from './evaluation';
import type { EvalReport } from '../types';

const report = (over: Partial<EvalReport['summary']> = {}): EvalReport => ({
  report_id: 1,
  system_version: 'sv_d67e9df8e4e4',
  created_at: '2026-11-13T18:00:00Z',
  repetitions: 2,
  experiment_url: null,
  summary: {
    cases: 11,
    splits: { train: 8, val: 3 },
    runs: 22,
    excluded_runs: 0,
    reference_impacts: { value: 41 / 52, range: [0.654, 0.881], found: 41, total: 52 },
    omissions_addressed: { value: 0.8, range: [0.7, 0.9], found: 8, total: 10 },
    grounding: { value: 0.97, range: [0.95, 0.99] },
    precision: { value: null, range: null },
    ...over,
  },
});

describe('published evaluation', () => {
  it('shows counts with a range, rates for grounding, and skips missing metrics', () => {
    const rows = evalRows(report());
    expect(rows.map((r) => r.name)).toEqual(['Reference impacts found', 'Omissions addressed', 'Citation check']);
    expect(rows[0]).toMatchObject({ value: '41 of 52', range: 'about 65–88%' });
    expect(rows[2]).toMatchObject({ value: '97%', range: 'about 95–99%' });
  });

  it('a single case has no range', () => {
    const rows = evalRows(report({ reference_impacts: { value: 1, range: null, found: 3, total: 3 } }));
    expect(rows[0].range).toBe('');
  });

  it('describes what the scores rest on, including runs left out', () => {
    expect(evalBasis(report())).toBe('8 train and 3 val reference cases, 2 runs each');
    expect(evalBasis(report({ excluded_runs: 1 }))).toMatch(/1 run without a verdict left out$/);
  });
});
