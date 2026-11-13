import { describe, expect, it } from 'vitest';
import { parseRoute, routeHash } from './route';
import { NO_RUN_FILTER } from './runFilter';

describe('parseRoute', () => {
  it('reads the screen and the run', () => {
    expect(parseRoute('#/detail?run=run_123')).toEqual({ screen: 'detail', run: 'run_123', filter: NO_RUN_FILTER });
  });

  it('falls back to Overview for an empty or unknown screen', () => {
    expect(parseRoute('').screen).toBe('overview');
    expect(parseRoute('#/').screen).toBe('overview');
    expect(parseRoute('#/nope?run=x').screen).toBe('overview');
  });

  it('reads the run filters', () => {
    expect(parseRoute('#/overview?status=failed&scenario=eval_sme_impacts').filter).toEqual({ status: 'failed', scenario: 'eval_sme_impacts' });
  });

  it('ignores an unknown status filter', () => {
    expect(parseRoute('#/overview?status=bogus').filter).toEqual(NO_RUN_FILTER);
  });

  it('decodes encoded values', () => {
    expect(parseRoute('#/detail?run=run%2Fa%20b').run).toBe('run/a b');
  });
});

describe('routeHash', () => {
  it('names the screen, and the run when one is chosen', () => {
    expect(routeHash('pipeline', null, NO_RUN_FILTER)).toBe('/pipeline');
    expect(routeHash('detail', 'run_123', NO_RUN_FILTER)).toBe('/detail?run=run_123');
  });

  it('carries the run filters on Overview only, and only when set', () => {
    const f = { status: 'degraded', scenario: 'eval_sme_impacts' } as const;
    expect(routeHash('overview', null, f)).toBe('/overview?status=degraded&scenario=eval_sme_impacts');
    expect(routeHash('overview', 'run_1', { status: 'all', scenario: 'x' })).toBe('/overview?run=run_1&scenario=x');
    expect(routeHash('detail', 'run_1', f)).toBe('/detail?run=run_1');
  });

  it('round-trips through parseRoute', () => {
    const f = { status: 'active', scenario: 'a&b=c' } as const;
    expect(parseRoute(`#${routeHash('overview', 'run/1', f)}`)).toEqual({ screen: 'overview', run: 'run/1', filter: f });
  });
});
