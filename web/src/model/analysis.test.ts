import { describe, expect, it } from 'vitest';
import { coverage, coverageNote, incompleteNote, keyLabel } from './analysis';
import type { PlannerTrace } from '../types';
import { degradedRun, sampleRun } from '../test/fixtures';

describe('incomplete analyses', () => {
  it('a complete run has no note', () => {
    expect(incompleteNote('succeeded', sampleRun().dossier)).toBeNull();
  });

  it('names the missing expert views in words', () => {
    expect(incompleteNote('degraded', degradedRun().dossier)).toBe('This analysis is incomplete: the workforce view is missing, because that expert did not finish. The other findings are unaffected.');
    const d = degradedRun().dossier!;
    d.failed_experts = [...d.failed_experts, { agent: 'stakeholder', error_kind: 'timeout', message: 'x', attempts: 1 }];
    expect(incompleteNote('degraded', d)).toMatch(/the workforce and stakeholder views are missing, because those experts did not finish/);
  });

  it('a degraded run without failed experts lost its synthesis', () => {
    expect(incompleteNote('degraded', sampleRun().dossier)).toMatch(/final synthesis did not finish/);
  });
});

describe('impacts that concern the reader\'s role in the law', () => {
  it('matches the wording of a finding\'s affected actor, whole words only', async () => {
    const { mentionsActor } = await import('./analysis');
    expect(mentionsActor('deployers of high-risk AI systems', 'deployer')).toBe(true);
    expect(mentionsActor('users of AI systems located within the Union', 'deployer')).toBe(true);
    expect(mentionsActor('abusers of the system', 'deployer')).toBe(false);
    expect(mentionsActor('providers and start-ups participating in AI regulatory sandboxes', 'provider')).toBe(true);
    expect(mentionsActor('municipalities and other public bodies', 'public')).toBe(true);
    expect(mentionsActor('national competent authorities', 'public')).toBe(false); // authorities, not implementers
    expect(mentionsActor('notified bodies', 'notified_body')).toBe(true); // fallback: the actor id as words
  });

  it('lists the impacts with at least one matching finding', async () => {
    const { impactsMentioning } = await import('./analysis');
    const d = sampleRun().dossier!;
    const providers = impactsMentioning(d, 'provider');
    expect(providers.size).toBeGreaterThan(0);
    expect(providers.size).toBeLessThan(d.impacts.length);
  });
});

describe('whole-act coverage', () => {
  const trace = (keys: string[], dropped: PlannerTrace['dropped'] = []): PlannerTrace => ({
    keys,
    areas: [keys],
    notes: [],
    dispatched: ['legal'],
    selected: [...keys, ...(dropped ?? []).map((d) => d.key)],
    dropped,
  });

  it('labels provision keys as articles and annexes', () => {
    expect(keyLabel('ai_act/art/26')).toBe('Art 26');
    expect(keyLabel('ai_act/annex/III')).toBe('Annex III');
    expect(keyLabel('ai_act/penalties/penalties')).toBe('Penalties');
  });

  it('says nothing when every selected provision was analysed', () => {
    const t = trace(['ai_act/art/5', 'ai_act/art/26']);
    expect(coverage(t)).toEqual({ selected: 2, analysed: 2, dropped: [] });
    expect(coverageNote(t)).toBeNull();
  });

  it('names what was not analysed and why, grouped by reason', () => {
    const t = trace(['ai_act/art/5'], [
      { key: 'ai_act/art/6', reason: 'max_prompt_chars' },
      { key: 'ai_act/art/26', reason: 'max_prompt_chars' },
      { key: 'ai_act/art/99', reason: 'max_provisions' },
    ]);
    expect(coverageNote(t)).toBe(
      'This analysis covers 1 of the 4 provisions it selected. Not analysed: Art 6 and Art 26 (too long to fit in one analysis); Art 99 (beyond the limit of provisions one analysis covers).',
    );
  });

  it('preset runs and older runs have no coverage', () => {
    expect(coverage(null)).toBeNull();
    expect(coverage({ keys: ['k'], areas: [['k']], notes: [], dispatched: [] })).toBeNull();
  });
});
