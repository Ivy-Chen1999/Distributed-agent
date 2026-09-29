import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { App } from './App';
import { TOKEN_KEY } from './api';
import { mockApi } from './test/fixtures';

let mock: ReturnType<typeof mockApi>;

async function boot() {
  localStorage.setItem(TOKEN_KEY, 'test-token-1234567890');
  render(<App />);
  // Wait until the run and its dossier have loaded.
  await screen.findByText('How are SMEs and start-ups affected?');
  await waitFor(() => expect(screen.getAllByText('Succeeded').length).toBeGreaterThan(0));
}

const nav = (name: string) => {
  const navEl = document.querySelector('nav')!;
  fireEvent.click(within(navEl as HTMLElement).getByText(name));
};

beforeEach(() => {
  mock = mockApi();
  vi.stubGlobal('fetch', vi.fn(mock.fn));
});
afterEach(() => vi.unstubAllGlobals());

describe('WOMM Console', () => {
  it('asks for a token when none is stored and uses it afterwards', async () => {
    render(<App />);
    const input = screen.getByLabelText('API token');
    fireEvent.change(input, { target: { value: 'abcdefghijklmnop' } });
    fireEvent.click(screen.getByText('Connect'));
    await screen.findByText('How are SMEs and start-ups affected?');
    expect(localStorage.getItem(TOKEN_KEY)).toBe('abcdefghijklmnop');
    expect(mock.calls.every((c) => c.auth === 'Bearer abcdefghijklmnop')).toBe(true);
  });

  it('shows the token prompt again after a 401', async () => {
    mock = mockApi({ '/system': () => ({ status: 401, body: { detail: 'invalid' } }) });
    vi.stubGlobal('fetch', vi.fn(mock.fn));
    localStorage.setItem(TOKEN_KEY, 'bad');
    render(<App />);
    await screen.findByText('Token rejected');
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
  });

  it('renders the overview from the API', async () => {
    await boot();
    expect(screen.getByText('EU AI Act · proposal')).toBeTruthy();
    expect(screen.getByText('COM(2021) 206 · Art 53–55, 71')).toBeTruthy();
    expect(screen.getAllByText('4m 48s').length).toBeGreaterThan(0);
    expect(screen.getByText('119.9k')).toBeTruthy();
    expect(screen.getByText('64/64 quotes verified')).toBeTruthy();
    expect(screen.getByText('Recent runs')).toBeTruthy();
    expect(screen.getByText('Needs your review')).toBeTruthy();
  });

  it('renders the pipeline and replays a finished run', async () => {
    await boot();
    nav('Run pipeline');
    expect(screen.getByText('Agent graph')).toBeTruthy();
    expect(screen.getByText('Router decisions')).toBeTruthy();
    expect(screen.getByText('44 of 44 findings')).toBeTruthy();
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    try {
      fireEvent.click(screen.getByText('Replay'));
      expect(screen.getAllByText('Replaying').length).toBeGreaterThan(0);
      expect(screen.getByText('Board is empty')).toBeTruthy();
      act(() => {
        vi.advanceTimersByTime(20_000);
      });
      expect(screen.getAllByText('Succeeded').length).toBeGreaterThan(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it('renders the dossier with provenance and opens the source panel', async () => {
    await boot();
    fireEvent.click(screen.getByText('Open impact dossier'));
    await screen.findByText('Regulatory sandboxes');
    fireEvent.click(screen.getAllByText('I1')[0]);
    const quotes = await screen.findAllByText('View in source');
    fireEvent.click(quotes[0]);
    const panel = await screen.findByRole('dialog', { name: 'Source' });
    await within(panel).findByText('Quote matched word for word in the source');
    expect(panel.querySelector('mark')?.textContent).toMatch(/^This shall take place under the direct supervision/);
    fireEvent.click(within(panel).getByLabelText('Close'));
    for (const tab of ['Impact chains', 'Disagreements', 'Open questions', 'Event log']) {
      fireEvent.click(screen.getByRole('tab', { name: new RegExp(tab) }));
    }
    expect(screen.getByText(/dossier: |assembled · status=succeeded/)).toBeTruthy();
  });

  it('renders agents, topology and settings', async () => {
    await boot();
    nav('Agents');
    expect(screen.getByText(/Each agent runs in its own isolated process/)).toBeTruthy();
    expect(screen.getAllByText('Open in topology').length).toBe(6);
    fireEvent.click(screen.getAllByText('Open in topology')[2]);
    expect(screen.getByText('Process topology')).toBeTruthy();
    expect(screen.getByText('Inspector')).toBeTruthy();
    nav('Settings');
    expect(screen.getByText('LLM backend per role')).toBeTruthy();
    expect(screen.getByText('5 of 5 passed')).toBeTruthy();
    fireEvent.click(screen.getAllByText('api')[0]);
    expect(screen.getByTestId('staged').textContent).toMatch(/planner → api/);
    expect(screen.getByTestId('staged').textContent).toMatch(/id will differ from sv_44a681965332/);
  });

  it('starts a run with staged overrides and reports errors as a toast', async () => {
    await boot();
    nav('Settings');
    fireEvent.click(screen.getByText('Enforce'));
    fireEvent.click(screen.getByText('Re-run'));
    const dialog = await screen.findByRole('dialog', { name: 'Start a run' });
    expect(within(dialog).getByRole('radio', { checked: true }).textContent).toMatch(/SME impacts/);
    fireEvent.click(within(dialog).getByText('Run'));
    await waitFor(() => expect(mock.calls.some((c) => c.method === 'POST' && c.url === '/runs')).toBe(true));
    const post = mock.calls.find((c) => c.method === 'POST' && c.url === '/runs')!;
    expect(post.body).toEqual({ scenario_id: 'eval_sme_impacts', overrides: { router_mode: 'active' } });
  });

  it('shows 503 from POST /runs as a toast', async () => {
    mock = mockApi({ '/runs': (c) => (c.method === 'POST' ? { status: 503, body: { detail: 'backend unavailable: no credentials' } } : undefined) });
    vi.stubGlobal('fetch', vi.fn(mock.fn));
    await boot();
    fireEvent.click(screen.getByText('Re-run'));
    const dialog = await screen.findByRole('dialog', { name: 'Start a run' });
    fireEvent.click(within(dialog).getByText('Run'));
    await screen.findByText('Backend unavailable: no credentials');
  });

  it('asks WOMM and jumps from a citation to the impact', async () => {
    await boot();
    nav('Ask WOMM');
    expect(screen.getByText(/all 64 evidence quotes found verbatim/)).toBeTruthy();
    fireEvent.click(screen.getByText('Who carries the penalty risk?'));
    await screen.findByText('Penalty risk sits with all providers (I19).');
    fireEvent.click(screen.getByText('f_083bb47fcfc7'));
    await waitFor(() => expect(document.getElementById('imp-I19')).toBeTruthy());
    expect(document.getElementById('imp-I19')!.querySelector('[aria-expanded="true"]')).toBeTruthy();
  });

  it('shows an empty state when there are no runs', async () => {
    mock = mockApi({ '/runs?': () => ({ status: 200, body: { runs: [] } }) });
    vi.stubGlobal('fetch', vi.fn(mock.fn));
    localStorage.setItem(TOKEN_KEY, 'test-token-1234567890');
    render(<App />);
    await screen.findByText('No runs yet', { selector: 'div' });
    nav('Run pipeline');
    expect(screen.getByText('No run selected')).toBeTruthy();
    nav('Ask WOMM');
    expect(screen.getByText('No run to ask about')).toBeTruthy();
  });

  it('switches theme', async () => {
    await boot();
    const root = document.querySelector('[data-screen-label="WOMM Console"]') as HTMLElement;
    expect(root.dataset.theme).toBe('light');
    fireEvent.click(screen.getByTitle('Switch theme'));
    expect(root.dataset.theme).toBe('dark');
    expect(root.style.getPropertyValue('--bg')).toBe('#0A1011');
  });
});
