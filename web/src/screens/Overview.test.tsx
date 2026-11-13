// The Overview runs table (filters, paging), the first-run walkthrough, and the plain-language
// failure explanations on the Run detail screen.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { App } from '../App';
import { TOKEN_KEY } from '../api';
import { degradedRun, mockApi, sampleRun, type MockCall } from '../test/fixtures';
import type { RunStatus, RunSummary } from '../types';

const PROVIDER = 'eval_provider_compliance_costs';

/** The newest run: the fixture run, which the mock API can serve in full. */
const newest = (): RunSummary => {
  const r = sampleRun();
  return { run_id: r.run_id, scenario_id: r.scenario_id, status: r.status, system_version: r.system_version, impacts: 25, duration_s: 287.5, grounding: r.grounding };
};

/** An older run that is only ever shown in the table. */
const older = (n: number, status: RunStatus, scenario_id = PROVIDER): RunSummary => ({
  run_id: `run_old${String(n).padStart(4, '0')}-0000`,
  scenario_id,
  status,
  system_version: 'sv_other',
  impacts: 3,
  duration_s: 60,
});

/** GET /runs answers from `pool`, newest first, honouring ?limit=. Other URLs fall through to the default mock. */
const runsRoute = (pool: RunSummary[]) => ({
  '/runs': (c: MockCall) => {
    const [path, query = ''] = c.url.split('?');
    if (c.method !== 'GET' || path !== '/runs') return undefined;
    const limit = Number(new URLSearchParams(query).get('limit') ?? 20);
    return { status: 200, body: { runs: pool.slice(0, limit) } };
  },
});

const rows = () => document.querySelectorAll('button[title^="run_"]');
const statusFilter = () => within(screen.getByRole('group', { name: 'Filter runs by status' }));

let mock: ReturnType<typeof mockApi>;

async function boot(pool: RunSummary[]) {
  mock = mockApi(runsRoute(pool));
  vi.stubGlobal('fetch', vi.fn(mock.fn));
  localStorage.setItem(TOKEN_KEY, 'test-token-1234567890');
  render(<App />);
  await waitFor(() => expect(rows().length).toBe(Math.min(pool.length, 20)));
}

afterEach(() => vi.unstubAllGlobals());

describe('first-run walkthrough', () => {
  it('explains the three steps when there are no runs', async () => {
    mock = mockApi({ '/runs?': () => ({ status: 200, body: { runs: [] } }) });
    vi.stubGlobal('fetch', vi.fn(mock.fn));
    localStorage.setItem(TOKEN_KEY, 'test-token-1234567890');
    render(<App />);
    await screen.findByText('How it works');
    for (const step of ['Pick a scenario', 'Watch the experts work', 'Read the findings']) expect(screen.getByText(step)).toBeTruthy();
  });

  it('is not shown once there are runs', async () => {
    await boot([newest()]);
    expect(screen.queryByText('How it works')).toBeNull();
  });
});

describe('recent runs filters', () => {
  const failed = older(1, 'failed');
  const degraded = older(2, 'degraded');

  it('filters by status and shows how many of the loaded runs match', async () => {
    await boot([newest(), failed, degraded]);
    fireEvent.click(statusFilter().getByText('Failed'));
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(rows()[0].getAttribute('title')).toBe(failed.run_id);
    expect(screen.getByText('1 of 3 loaded runs')).toBeTruthy();
    fireEvent.click(statusFilter().getByText('All'));
    await waitFor(() => expect(rows()).toHaveLength(3));
  });

  it('filters by scenario', async () => {
    await boot([newest(), failed, degraded]);
    fireEvent.change(screen.getByRole('combobox'), { target: { value: PROVIDER } });
    await waitFor(() => expect(rows()).toHaveLength(2));
    expect(screen.getByText('2 of 3 loaded runs')).toBeTruthy();
  });

  it('says so when nothing matches, and clears the filters', async () => {
    await boot([newest(), failed, degraded]);
    fireEvent.change(screen.getByRole('combobox'), { target: { value: PROVIDER } });
    fireEvent.click(statusFilter().getByText('Succeeded'));
    await screen.findByText(/No runs match these filters/);
    expect(rows()).toHaveLength(0);
    fireEvent.click(screen.getAllByRole('button', { name: 'Clear filters' })[0]);
    await waitFor(() => expect(rows()).toHaveLength(3));
    expect(screen.queryByText(/No runs match these filters/)).toBeNull();
  });

  it('keeps the filters in the URL and after visiting another screen', async () => {
    await boot([newest(), failed, degraded]);
    fireEvent.click(statusFilter().getByText('Failed'));
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(window.location.hash).toBe('#/overview?status=failed');
    const nav = within(document.querySelector('nav') as HTMLElement);
    fireEvent.click(nav.getByText('Run detail'));
    await waitFor(() => expect(window.location.hash).toMatch(/^#\/detail/));
    fireEvent.click(nav.getByText('Overview'));
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(window.location.hash).toBe('#/overview?status=failed');
  });

  it('tidies a hand-written link in place, without a history entry the back button would bounce on', async () => {
    window.location.hash = '#/overview?status=all&scenario=';
    await waitFor(() => expect(window.location.hash).toContain('status=all'));
    const entries = window.history.length;
    await boot([newest(), failed]);
    await waitFor(() => expect(window.location.hash).toBe('#/overview'));
    expect(window.history.length).toBe(entries);
  });

  it('opens with the filters a link names', async () => {
    window.location.hash = `#/overview?scenario=${PROVIDER}`;
    await waitFor(() => expect(window.location.hash).toContain(PROVIDER));
    mock = mockApi(runsRoute([newest(), failed, degraded]));
    vi.stubGlobal('fetch', vi.fn(mock.fn));
    localStorage.setItem(TOKEN_KEY, 'test-token-1234567890');
    render(<App />);
    await waitFor(() => expect(rows()).toHaveLength(2));
    expect(screen.getByText('2 of 3 loaded runs')).toBeTruthy();
  });
});

describe('paging', () => {
  const pool = [newest(), ...Array.from({ length: 24 }, (_, i) => older(i + 1, 'succeeded'))];

  it('loads 20 runs, then 20 more on request, and stops offering more when all are loaded', async () => {
    await boot(pool);
    expect(mock.calls.some((c) => c.url === '/runs?limit=20')).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Show more runs' }));
    await waitFor(() => expect(rows()).toHaveLength(25));
    expect(mock.calls.some((c) => c.url === '/runs?limit=40')).toBe(true);
    expect(screen.queryByRole('button', { name: 'Show more runs' })).toBeNull();
  });

  it('does not offer more when fewer than a full page came back', async () => {
    await boot([newest(), older(1, 'succeeded')]);
    expect(screen.queryByRole('button', { name: 'Show more runs' })).toBeNull();
  });
});

describe('failure explanations', () => {
  it('explains a failed expert and an unverified quote in plain language', async () => {
    const run = degradedRun();
    mock = mockApi({ [`/runs/${run.run_id}`]: (c) => (c.url.split('?')[0] === `/runs/${run.run_id}` ? { status: 200, body: run } : undefined) });
    vi.stubGlobal('fetch', vi.fn(mock.fn));
    localStorage.setItem(TOKEN_KEY, 'test-token-1234567890');
    render(<App />);
    await waitFor(() => expect(screen.getAllByText('Succeeded').length).toBeGreaterThan(0));
    fireEvent.click(within(document.querySelector('nav') as HTMLElement).getByText('Run detail'));
    fireEvent.click(await screen.findByRole('tab', { name: /^Open questions/ }));
    expect(await screen.findByText(/The model took too long to answer and was stopped\./)).toBeTruthy();
    expect(screen.getByText(/Run again\. If it keeps happening, the model provider may be slow/)).toBeTruthy();
    expect(screen.getByText(/word for word in the source/)).toBeTruthy();
  });
});
