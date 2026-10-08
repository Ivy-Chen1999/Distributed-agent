// The dossier's Costs tab (EU cost plan R5): empty state, hotspots and record filters.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { App } from '../App';
import { TOKEN_KEY } from '../api';
import { mockApi, sampleRun } from '../test/fixtures';
import type { CostRecord, CostSection, RunDetail } from '../types';

const rec = (id: string, over: Partial<CostRecord>): CostRecord => ({
  obligation_id: id,
  unit_id: `u:${id}`,
  provision_key: 'ai_act/penalties/penalties',
  source_id: 'reg2024_1689/obligations/art_99',
  records_version: 'reg2024_1689',
  status: 'estimated',
  payer: 'member_state',
  payer_basis: 'rule_field',
  secondary_types: [],
  effort_type: 'new_process',
  one_off: 'medium',
  recurring: null,
  changed_after_proposal: false,
  late_added: false,
  ...over,
});

const COSTS: CostSection = {
  records: [
    rec('32024R1689:art99.par1#1', { late_added: true, changed_after_proposal: true, unit_delta: 'added' }),
    rec('32024R1689:art99.par2#1', { changed_after_proposal: true, unit_delta: 'modified', payer: null, payer_basis: 'unknown', one_off: 'low' }),
    rec('32024R1689:art99.par3#1', { payer: 'provider', payer_basis: 'inferred', status: 'not_costed', effort_type: null, one_off: null, reason: 'prohibition' }),
  ],
  hotspots: [{ dimension: 'provision', value: 'ai_act/penalties/penalties', recurrence: 'one_off', medium_or_high: 1, low: 1, records: 2, provision_keys: ['ai_act/penalties/penalties'] }],
  coverage: { relevant: 3, estimated: 2, not_costed: 1, not_estimated: 0, invalid_dropped: 0, not_covered_keys: ['ai_act/art/4a'], payers_by_basis: { rule_field: 1, unknown: 1, inferred: 1 } },
  late_added: ['32024R1689:art99.par1#1'],
  delta_basis: 'COM(2021) 206 proposal → Regulation (EU) 2024/1689 as adopted',
  notes: [],
};

async function openCosts(run: RunDetail) {
  const mock = mockApi({ [`/runs/${run.run_id}`]: (c) => (c.url.split('?')[0] === `/runs/${run.run_id}` ? { status: 200, body: run } : undefined) });
  vi.stubGlobal('fetch', vi.fn(mock.fn));
  localStorage.setItem(TOKEN_KEY, 'test-token-1234567890');
  render(<App />);
  await waitFor(() => expect(screen.getAllByText('Succeeded').length).toBeGreaterThan(0));
  fireEvent.click(within(document.querySelector('nav') as HTMLElement).getByText('Run detail'));
  fireEvent.click(await screen.findByRole('tab', { name: /^Costs/ }));
}

afterEach(() => vi.unstubAllGlobals());

describe('Costs tab', () => {
  it('shows the empty state for a version without a cost step', async () => {
    await openCosts(sampleRun());
    expect(screen.getByRole('tab', { name: /^Costs/ }).textContent).toBe('Costs');
    expect(await screen.findByText('This version has no cost step.')).toBeTruthy();
  });

  it('shows coverage, hotspots, marked payers and filters records added after the proposal', async () => {
    const run = sampleRun();
    run.dossier = { ...run.dossier!, costs: COSTS };
    await openCosts(run);
    expect(screen.getByRole('tab', { name: /^Costs/ }).textContent).toBe('Costs3');
    expect(await screen.findByText(/3 obligations · 2 estimated · 1 not costed/)).toBeTruthy();
    expect(screen.getByText(/Not covered \(no obligation records\): ai_act\/art\/4a/)).toBeTruthy();
    const hot = screen.getByLabelText('Hotspots by provision');
    expect(within(hot).getByText('1 med/high')).toBeTruthy();
    const all = screen.getAllByTestId('cost-record');
    expect(all).toHaveLength(3);
    expect(within(all[1]).getByText('Payer not identified')).toBeTruthy();
    expect(within(all[2]).getByText('Provider · inferred from the text')).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/EUR|€/);
    fireEvent.change(screen.getByLabelText('Change after the proposal'), { target: { value: 'added' } });
    const rows = screen.getAllByTestId('cost-record');
    expect(rows).toHaveLength(1);
    expect(within(rows[0]).getByText('Added after the proposal')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Change after the proposal'), { target: { value: 'changed' } });
    expect(screen.getAllByTestId('cost-record')).toHaveLength(2);
  });
});
