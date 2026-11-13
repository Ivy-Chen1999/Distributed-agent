import { describe, expect, it } from 'vitest';
import { analysisSteps } from './progress';
import { buildTrace } from './trace';
import { syntheticEvents } from '../test/fixtures';

const EXPERTS = ['legal', 'fiscal', 'stakeholder'];
const trace = (cutoff = Infinity) => buildTrace({ events: syntheticEvents().filter((e) => e.seq <= cutoff), experts: EXPERTS, run: null });

describe('analysisSteps', () => {
  it('names the steps of an analysis in plain words, in pipeline order', () => {
    expect(analysisSteps(trace(), 999).map((s) => s.label)).toEqual(['Reading the changes', 'Experts reading the law', 'Checking every quote against the source', 'Combining the findings', 'Writing the analysis']);
  });

  it('marks everything done once the run is over', () => {
    expect(analysisSteps(trace(), 999).every((s) => s.state === 'done')).toBe(true);
  });

  it('shows the experts step as active with how many have finished', () => {
    // Up to the fiscal expert finishing (seq 9): planner and router done, 1 of 3 experts done.
    const steps = analysisSteps(trace(9), 80);
    expect(steps[0].state).toBe('done');
    expect(steps[1]).toMatchObject({ state: 'active', detail: '1 of 3 finished' });
    expect(steps.slice(2).every((s) => s.state === 'waiting')).toBe(true);
  });

  it('starts with reading the changes', () => {
    const steps = analysisSteps(trace(1), 5);
    expect(steps[0].state).toBe('active');
    expect(steps[1]).toMatchObject({ state: 'waiting', detail: '3 experts' });
  });

  it('marks a failed expert, and the run carries on', () => {
    const tr = buildTrace({ events: syntheticEvents({ workforce: true }), experts: [...EXPERTS, 'workforce'], run: null });
    const experts = analysisSteps(tr, 999)[1];
    expect(experts.state).toBe('done');
    expect(experts.detail).toBe('3 of 4 finished, 1 failed');
  });
});
