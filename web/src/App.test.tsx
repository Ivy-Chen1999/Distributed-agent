import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { App } from './App';
import { TOKEN_KEY } from './api';
import { mockApi, sampleRun, system, type MockCall } from './test/fixtures';

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
        vi.advanceTimersByTime(120_000); // the 287.5 s fixture run at 3×
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

  it('renders settings for a system version without a source file', async () => {
    mock = mockApi({ '/system': () => ({ status: 200, body: { ...system, source_path: null } }) });
    vi.stubGlobal('fetch', vi.fn(mock.fn));
    await boot();
    nav('Settings');
    expect(screen.getByText('LLM backend per role')).toBeTruthy();
    expect(screen.getByText(/A content hash of the version spec/)).toBeTruthy();
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

  describe('provision comparison', () => {
    const DEMO = 'demo_penalties_amended';
    /** The fixture run, re-labelled as a run of the demo diff scenario. */
    const demoRoutes = (sources?: (call: MockCall) => { status: number; body: unknown } | undefined) => {
      const run = { ...sampleRun(), scenario_id: DEMO };
      return {
        '/runs': (c: MockCall) => {
          const path = c.url.split('?')[0];
          if (c.method !== 'GET') return undefined;
          if (path === '/runs') return { status: 200, body: { runs: [{ run_id: run.run_id, scenario_id: DEMO, status: 'succeeded', system_version: run.system_version, duration_s: 287.5, impacts: 25, grounding: run.grounding }] } };
          if (path === `/runs/${run.run_id}`) return { status: 200, body: run };
          return undefined;
        },
        ...(sources ? { [`/scenarios/${DEMO}/sources`]: sources } : {}),
      };
    };
    const bootDemo = async () => {
      localStorage.setItem(TOKEN_KEY, 'test-token-1234567890');
      render(<App />);
      await waitFor(() => expect(screen.getAllByText('Succeeded').length).toBeGreaterThan(0));
      nav('Run detail');
    };

    it('is hidden for a scenario without a proposal → final-text diff', async () => {
      await boot();
      nav('Run detail');
      await screen.findByRole('tab', { name: /Impacts/ });
      expect(screen.queryByRole('heading', { name: 'Provision comparison' })).toBeNull();
      expect(screen.queryByTestId('provision-comparison')).toBeNull();
    });

    it('shows the demo diff side by side with marked deletions and additions', async () => {
      mock = mockApi(demoRoutes());
      vi.stubGlobal('fetch', vi.fn(mock.fn));
      await bootDemo();
      const section = await screen.findByTestId('provision-comparison');
      expect(within(section).getByRole('heading', { name: 'Provision comparison' })).toBeTruthy();
      await within(section).findByText('Art 55 → Art 62');
      expect(section.textContent).toContain('Proposal COM(2021) 206 → final text Regulation (EU) 2024/1689');
      // Changes start collapsed; opening one shows both columns, the deletion struck in the
      // proposal and the addition in the final text.
      const sme = section.querySelector('[data-provision="ai_act/innovation/sme_measures"]') as HTMLElement;
      expect(sme.querySelector('[data-side]')).toBeNull();
      expect(within(sme).getByText('−4 / +2 words')).toBeTruthy();
      fireEvent.click(within(sme).getByRole('button', { expanded: false }));
      const before = sme.querySelector('[data-side="before"]')!;
      const after = sme.querySelector('[data-side="after"]')!;
      expect(before.textContent).toMatch(/^Proposal · COM\(2021\) 206 · Art 55/);
      expect(after.textContent).toMatch(/^Final text · Regulation \(EU\) 2024\/1689 · Art 62/);
      expect(before.querySelector('del')?.textContent).toBe('[removed: small-scale providers and]');
      expect(before.querySelector('ins')).toBeNull();
      expect(after.querySelector('ins')?.textContent).toBe('[added: SMEs, including]');
      expect(after.querySelector('del')).toBeNull();
      expect(getComputedStyle(before.querySelector('del')!).textDecoration).toMatch(/line-through/);
      expect(getComputedStyle(after.querySelector('ins')!).textDecoration).toMatch(/underline/);
      // The renumbered, unchanged article opens on demand.
      const pen = section.querySelector('[data-provision="ai_act/penalties/penalties"]') as HTMLElement;
      const toggle = within(pen).getByRole('button', { expanded: false });
      expect(toggle.textContent).toMatch(/Art 71 → Art 99.*Modified.*wording unchanged/);
      fireEvent.click(toggle);
      expect(within(pen).getByText(/Same wording; only the article number changed \(Art 71 → Art 99\)/)).toBeTruthy();
      expect(pen.querySelectorAll('del, ins')).toHaveLength(0);
      fireEvent.click(within(sme).getByRole('button', { expanded: true }));
      expect(sme.querySelector('[data-side]')).toBeNull();
    });

    it('shows an error with a retry when the provision texts fail to load', async () => {
      let fail = true;
      mock = mockApi(demoRoutes(() => (fail ? { status: 500, body: { detail: 'sources unavailable' } } : undefined)));
      vi.stubGlobal('fetch', vi.fn(mock.fn));
      await bootDemo();
      const section = await screen.findByTestId('provision-comparison');
      await within(section).findByText('Could not load the provision texts');
      expect(within(section).getByRole('alert').textContent).toContain('sources unavailable');
      fail = false;
      fireEvent.click(within(section).getByText('Retry'));
      await within(section).findByText('Art 55 → Art 62');
    });

    it('shows an empty state when no change has both a proposal and a final text', async () => {
      mock = mockApi(demoRoutes(() => ({ status: 200, body: { scenario_id: DEMO, changes: [], sources: [] } })));
      vi.stubGlobal('fetch', vi.fn(mock.fn));
      await bootDemo();
      await within(await screen.findByTestId('provision-comparison')).findByText('No provisions to compare');
    });
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
