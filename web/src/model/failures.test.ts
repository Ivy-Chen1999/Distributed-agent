import { describe, expect, it } from 'vitest';
import { UNRESOLVED_QUOTE_HELP, failureHelp } from './failures';

const KINDS = ['timeout', 'rate_limit', 'auth', 'schema_invalid', 'process_error', 'no_data_in_scope', 'pipeline_failed', 'orphaned', 'cancelled'];

describe('failureHelp', () => {
  it('explains every failure kind the API can report, in two full sentences', () => {
    for (const kind of KINDS) {
      const h = failureHelp(kind);
      expect(h.what).toMatch(/^[A-Z].*\.$/);
      expect(h.next).toMatch(/^[A-Z].*\.$/);
    }
  });

  it('gives each kind its own explanation', () => {
    expect(new Set(KINDS.map((k) => failureHelp(k).what)).size).toBe(KINDS.length);
  });

  it('says a skipped expert is not an error', () => {
    expect(failureHelp('no_data_in_scope').what).toContain('not an error');
  });

  it('names an unknown kind and points to the event log', () => {
    const h = failureHelp('disk_on_fire');
    expect(h.what).toContain('disk on fire');
    expect(h.next).toContain('event log');
  });

  it('copes with a missing kind', () => {
    expect(failureHelp(null).what).toBe('Something went wrong.');
    expect(failureHelp(undefined).what).toBe('Something went wrong.');
  });

  it('explains an unverified quote', () => {
    expect(UNRESOLVED_QUOTE_HELP).toContain('unverified');
  });
});
