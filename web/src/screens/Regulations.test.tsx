// The Regulations screen: search, example entries, and opening an act on the Act screen.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { App } from '../App';
import { TOKEN_KEY } from '../api';
import { ROLE_KEY } from '../model/roles';
import { degradedRun, mockApi } from '../test/fixtures';
import { COMPARISONS, OMNIBUS, PROPOSAL_TO_FINAL } from '../test/act';
import { REGULATIONS } from '../test/regulations';

function boot(over: Parameters<typeof mockApi>[0] = {}) {
  const mock = mockApi({
    ...over,
    '/regulations': () => ({ status: 200, body: REGULATIONS }),
    '/corpus/ai_act/comparisons': () => ({ status: 200, body: COMPARISONS }),
    '/corpus/ai_act/compare/reg2024_1689/reg2024_1689_c20260727': () => ({ status: 200, body: OMNIBUS }),
    '/corpus/ai_act/compare/com2021_206/reg2024_1689': () => ({ status: 200, body: PROPOSAL_TO_FINAL }),
  });
  vi.stubGlobal('fetch', vi.fn(mock.fn));
  localStorage.setItem(TOKEN_KEY, 'test-token-1234567890');
  localStorage.setItem(ROLE_KEY, 'reader');
  render(<App />);
}

const search = () => screen.getByLabelText('Find a regulation');
const items = () => within(screen.getByRole('list', { name: 'Regulations' })).getAllByRole('listitem');
const title = () => document.querySelector('header div div')?.textContent;

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe('Regulations screen', () => {
  it('lists the analysed acts, with versions, depth and analyses', async () => {
    boot();
    expect(await screen.findByTestId('regs-count')).toHaveProperty('textContent', '1 regulation');
    expect(document.querySelector('header')!.textContent).toContain('Acts WOMM has analysed');
    expect(document.querySelector('header')!.textContent).not.toContain('eval_sme_impacts');
    const ai = items()[0];
    expect(within(ai).getByText('Artificial Intelligence Act')).toBeTruthy();
    expect(within(ai).getByText('Versions compared')).toBeTruthy();
    expect(within(ai).getByRole('button', { name: '1 finished analysis' })).toBeTruthy();
    expect(within(ai).getByText(/adopted: Regulation \(EU\) 2024\/1689/)).toBeTruthy();
    expect(ai.textContent).toContain('2 comparisons · 1 finished analysis'); // the sample run's scenario is on the act
  });

  it('example entries are off by default, labelled, and cannot be opened', async () => {
    boot();
    await screen.findByTestId('regs-count');
    fireEvent.click(screen.getByLabelText('Show example entries (development)'));
    expect(items()).toHaveLength(6);
    fireEvent.change(search(), { target: { value: 'GDPR' } });
    const gdpr = items()[0];
    expect(within(gdpr).getByText('Example entry')).toBeTruthy();
    expect(within(gdpr).getByText('Illustrative only')).toBeTruthy();
    expect(within(gdpr).queryByRole('button', { name: /^Open/ })).toBeNull();
  });

  it('a number nobody analysed says so', async () => {
    boot();
    await screen.findByTestId('regs-count');
    fireEvent.change(search(), { target: { value: 'Directive (EU) 2022/2555' } });
    expect(screen.getByTestId('regs-query').textContent).toBe('Recognised 32022L2555, but WOMM has not analysed that act.');
    expect(screen.getByText('No regulation matches')).toBeTruthy();
  });

  it('opening the act shows the law in force against its latest change', async () => {
    boot();
    await screen.findByTestId('regs-count');
    fireEvent.click(within(items()[0]).getByRole('button', { name: 'Open' }));
    expect(title()).toBe('Act');
    await waitFor(() => expect((screen.getByLabelText('Comparison') as HTMLSelectElement).value).toBe('reg2024_1689->reg2024_1689_c20260727'));
  });

  it('a pasted proposal link opens proposal -> adopted text at the renumbered article', async () => {
    boot();
    await screen.findByTestId('regs-count');
    fireEvent.change(search(), { target: { value: 'https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:52021PC0206#art_53' } });
    expect(screen.getByTestId('regs-query').textContent).toBe('Recognised 52021PC0206, article 53.');
    fireEvent.click(within(items()[0]).getByRole('button', { name: 'Open at Article 53' }));
    await waitFor(() => expect((screen.getByLabelText('Comparison') as HTMLSelectElement).value).toBe('com2021_206->reg2024_1689'));
    // The article renders collapsed, then the target opens it: wait for the open state, not just
    // for the article (proposal Art 53 is final Art 57).
    await waitFor(() => expect(document.querySelector('article[data-provision="ai_act/innovation/regulatory_sandboxes"]')?.getAttribute('data-open')).toBe('true'));
  });

  it('Enter opens the single match', async () => {
    boot();
    await screen.findByTestId('regs-count');
    fireEvent.change(search(), { target: { value: 'AI Act' } });
    fireEvent.submit(search().closest('form')!);
    expect(title()).toBe('Act');
  });
});

describe('reading an analysis as a reader', () => {
  it('lists the act analyses and opens one as a document, without run machinery', async () => {
    const run = degradedRun();
    boot({
      '/runs': (c) => {
        const path = c.url.split('?')[0];
        if (path === '/runs') return { status: 200, body: { runs: [{ run_id: run.run_id, scenario_id: run.scenario_id, status: 'degraded', system_version: run.system_version, created_at: run.created_at, finished_at: '2026-11-02T10:00:00Z', impacts: 25 }] } };
        if (path === `/runs/${run.run_id}`) return { status: 200, body: run };
        return undefined;
      },
    });
    await screen.findByTestId('regs-count');
    fireEvent.click(within(items()[0]).getByRole('button', { name: '1 finished analysis' }));
    const list = screen.getByRole('list', { name: 'Analyses' });
    const row = within(list).getByRole('button');
    expect(row.textContent).toContain('COM(2021) 206');
    expect(row.textContent).toContain('25 impacts');
    expect(row.textContent).toContain('Incomplete');
    fireEvent.click(row);
    expect(title()).toBe('Analysis');
    expect(document.querySelector('header')!.textContent).not.toContain(run.scenario_id); // no scenario id
    expect(await screen.findByTestId('analysis-incomplete')).toHaveProperty('textContent', 'This analysis is incomplete: the workforce view is missing, because that expert did not finish. The other findings are unaffected.');
    const main = document.querySelector('main')!;
    await waitFor(() => expect(within(main).getAllByRole('tab').length).toBeGreaterThan(0));
    expect(within(main).getAllByRole('tab').map((t) => t.textContent?.replace(/\d+$/, ''))).toEqual(['Impacts', 'Impact chains', 'Disagreements', 'Open questions', 'Costs']);
    expect(main.textContent).not.toContain(run.system_version);
    expect(main.textContent).not.toContain(run.run_id.slice(0, 12));
    expect(main.textContent).not.toContain('failed experts');
    expect(main.textContent).not.toMatch(/ai_act\/[a-z_]+\//); // no internal provision keys
    expect(within(main).getAllByText(/^model confidence /).length).toBeGreaterThan(0);
    expect(within(main).getAllByText(/^model confidence /)[0].getAttribute('title')).toMatch(/not a verification/);

    expect(screen.getByTestId('only-mine').textContent).toMatch(/^Only impacts that mention deployers \(\d+ of 25\)$/);

    // The Act screen then lays this analysis's findings over the comparison it analysed.
    fireEvent.click(within(document.querySelector('nav') as HTMLElement).getByText('Act'));
    fireEvent.change(await screen.findByLabelText('Comparison'), { target: { value: 'com2021_206->reg2024_1689' } });
    await waitFor(() => expect(screen.getByTestId('act-run').textContent).toMatch(/^Findings of the analysis you have open: \d+/));
  });

  it('Analysis with nothing open sends the reader to Regulations', async () => {
    boot({ '/runs': (c) => (c.url.split('?')[0] === '/runs' ? { status: 200, body: { runs: [] } } : undefined) });
    await screen.findByTestId('regs-count');
    fireEvent.click(within(document.querySelector('nav') as HTMLElement).getByText('Analysis'));
    expect(screen.getByText('No analysis open')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Find a regulation' }));
    expect(title()).toBe('Regulations');
  });
});
