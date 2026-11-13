import { describe, expect, it } from 'vitest';
import { NO_RUN_FILTER, RUN_CAP, RUN_PAGE, canShowMore, filterRuns, isFiltering, matchesStatus, nextRunLimit, scenarioChoices } from './runFilter';
import type { RunStatus, RunSummary } from '../types';

let n = 0;
const run = (status: RunStatus, scenario_id = 'eval_sme_impacts'): RunSummary => ({ run_id: `run_${++n}`, scenario_id, status, system_version: 'sv_x' });
const ALL: RunStatus[] = ['queued', 'running', 'succeeded', 'degraded', 'failed', 'no_changes'];

describe('matchesStatus', () => {
  it('"all" matches every status', () => {
    for (const s of ALL) expect(matchesStatus(s, 'all')).toBe(true);
  });

  it('"active" is queued and running only', () => {
    expect(ALL.filter((s) => matchesStatus(s, 'active'))).toEqual(['queued', 'running']);
  });

  it('the other filters match their own status only', () => {
    for (const f of ['succeeded', 'degraded', 'failed'] as const) expect(ALL.filter((s) => matchesStatus(s, f))).toEqual([f]);
  });
});

describe('filterRuns', () => {
  const runs = [run('succeeded', 'a'), run('failed', 'b'), run('degraded', 'a'), run('failed', 'a'), run('running', 'b')];

  it('returns every run, in order, when no filter is set', () => {
    expect(filterRuns(runs, NO_RUN_FILTER)).toEqual(runs);
  });

  it('filters by status', () => {
    expect(filterRuns(runs, { status: 'failed', scenario: '' }).map((r) => r.run_id)).toEqual([runs[1].run_id, runs[3].run_id]);
  });

  it('filters by scenario', () => {
    expect(filterRuns(runs, { status: 'all', scenario: 'b' })).toHaveLength(2);
  });

  it('combines both filters', () => {
    expect(filterRuns(runs, { status: 'failed', scenario: 'a' }).map((r) => r.run_id)).toEqual([runs[3].run_id]);
  });

  it('can match nothing, and leaves the input alone', () => {
    const before = runs.length;
    expect(filterRuns(runs, { status: 'succeeded', scenario: 'b' })).toEqual([]);
    expect(runs).toHaveLength(before);
  });

  it('knows when a filter is set', () => {
    expect(isFiltering(NO_RUN_FILTER)).toBe(false);
    expect(isFiltering({ status: 'failed', scenario: '' })).toBe(true);
    expect(isFiltering({ status: 'all', scenario: 'a' })).toBe(true);
  });
});

describe('scenarioChoices', () => {
  it('lists each scenario once, sorted by display name', () => {
    const runs = [run('succeeded', 'z_id'), run('failed', 'a_id'), run('failed', 'z_id')];
    const names: Record<string, string> = { z_id: 'Alpha', a_id: 'Zulu' };
    expect(scenarioChoices(runs, (id) => names[id])).toEqual([
      { id: 'z_id', name: 'Alpha' },
      { id: 'a_id', name: 'Zulu' },
    ]);
  });

  it('is empty without runs', () => {
    expect(scenarioChoices([], (id) => id)).toEqual([]);
  });
});

describe('paging', () => {
  it('starts at 20 runs and grows 20 at a time up to the API cap', () => {
    expect(RUN_PAGE).toBe(20);
    expect(nextRunLimit(20)).toBe(40);
    expect(nextRunLimit(190)).toBe(RUN_CAP);
    expect(nextRunLimit(RUN_CAP)).toBe(RUN_CAP);
  });

  it('offers "show more" only after a full page, below the cap', () => {
    expect(canShowMore(20, 20)).toBe(true);
    expect(canShowMore(13, 20)).toBe(false);
    expect(canShowMore(0, 20)).toBe(false);
    expect(canShowMore(RUN_CAP, RUN_CAP)).toBe(false);
  });
});
