import { describe, expect, it } from 'vitest';
import { incompleteNote } from './analysis';
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
