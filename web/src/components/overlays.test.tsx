import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { RunPicker, SourcePanel } from './overlays';
import type { Loadable } from '../hooks';
import type { Scenario, ScenarioSources, SourceDoc } from '../types';
import { demoScenario, scenarios } from '../test/fixtures';

const scenarioSources: Loadable<ScenarioSources> = {
  status: 'ready',
  data: {
    scenario_id: 'eval_sme_impacts',
    changes: [],
    sources: [{ source_id: 'com2021_206/art_71', title: 'Scenario text of Article 71', kind: 'provision', text: 'Member States shall lay down the rules on penalties.' }],
  },
};

const view: SourceDoc = {
  source_id: 'com2021_206/obligations/art_71',
  title: 'COM(2021) 206, Article 71 (obligation records, full view)',
  kind: 'obligations',
  text: '[ob_1]\nprimary actor: Member States\naction: lay down the rules on penalties',
};

function panel() {
  return screen.getByRole('dialog', { name: 'Source' });
}

describe('SourcePanel', () => {
  it("resolves an obligation-view citation from the run's citable sources", () => {
    render(<SourcePanel sourceId={view.source_id} quote="lay down the rules on penalties" sources={scenarioSources} runSources={[view]} onClose={() => {}} />);
    expect(within(panel()).getByText(view.title)).toBeTruthy();
    expect(within(panel()).getByText('Quote matched word for word in the source')).toBeTruthy();
    expect(panel().querySelector('mark')?.textContent).toBe('lay down the rules on penalties');
  });

  it("prefers the run's copy of a source over the scenario's", () => {
    const runCopy: SourceDoc = { ...scenarioSources.data!.sources[0], title: 'Run copy of Article 71' };
    render(<SourcePanel sourceId={runCopy.source_id} quote="rules on penalties" sources={scenarioSources} runSources={[runCopy]} onClose={() => {}} />);
    expect(within(panel()).getByText('Run copy of Article 71')).toBeTruthy();
  });

  it('falls back to the scenario sources when the run has none (older runs)', () => {
    render(<SourcePanel sourceId="com2021_206/art_71" quote="rules on penalties" sources={scenarioSources} runSources={null} onClose={() => {}} />);
    expect(within(panel()).getByText('Scenario text of Article 71')).toBeTruthy();
  });

  it('says a citation is unresolved when neither has the source', () => {
    render(<SourcePanel sourceId={view.source_id} quote="lay down" sources={scenarioSources} onClose={() => {}} />);
    expect(within(panel()).getByText('This source is not part of the scenario.')).toBeTruthy();
  });
});

describe('RunPicker', () => {
  const ready: Loadable<Scenario[]> = { status: 'ready', data: [...scenarios, demoScenario] };
  const picker = (initial: string) => <RunPicker scenarios={ready} initial={initial} staged={null} busy={false} onRun={() => {}} onClose={() => {}} />;
  const checked = () => within(screen.getByRole('dialog', { name: 'Start a run' })).getByRole('radio', { checked: true }).textContent;

  it('follows the current scenario when it resolves after the picker opened', () => {
    // Opened before the runs loaded: the app passes its default scenario first.
    const { rerender } = render(picker('eval_sme_impacts'));
    expect(checked()).toMatch(/eval_sme_impacts/);
    rerender(picker('demo_penalties_amended'));
    expect(checked()).toMatch(/demo_penalties_amended/);
  });

  it("keeps the user's choice when the current scenario changes", () => {
    const { rerender } = render(picker('eval_sme_impacts'));
    fireEvent.click(screen.getByRole('radio', { name: /eval_provider_compliance_costs/ }));
    rerender(picker('demo_penalties_amended'));
    expect(checked()).toMatch(/eval_provider_compliance_costs/);
  });
});
