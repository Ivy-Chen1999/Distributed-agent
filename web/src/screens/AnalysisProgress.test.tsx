// A running analysis in the law interface shows where it is, in plain words, instead of
// "come back later".
import { afterEach, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { App } from '../App';
import { TOKEN_KEY } from '../api';
import { ROLE_KEY } from '../model/roles';
import { mockApi, sampleRun, syntheticEvents, type MockCall } from '../test/fixtures';

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

it('shows the steps of a running analysis and how many experts have finished', async () => {
  const run = { ...sampleRun(), run_id: 'run_live', status: 'running' as const, dossier: null, finished_at: null };
  const events = syntheticEvents().filter((e) => e.seq <= 8); // up to the fiscal expert finishing
  const mock = mockApi({
    '/runs?': () => ({ status: 200, body: { runs: [{ run_id: run.run_id, scenario_id: run.scenario_id, status: 'running', system_version: run.system_version }] } }),
    '/runs/run_live': (c: MockCall) => {
      const [path, query = ''] = c.url.split('?');
      if (path === '/runs/run_live/events') {
        const after = Number(new URLSearchParams(query).get('after') ?? 0);
        const page = events.filter((e) => e.seq > after);
        return { status: 200, body: { run_id: run.run_id, events: page, next_after: page.at(-1)?.seq ?? after } };
      }
      return path === '/runs/run_live' ? { status: 200, body: run } : undefined;
    },
  });
  vi.stubGlobal('fetch', vi.fn(mock.fn));
  localStorage.setItem(TOKEN_KEY, 'test-token-1234567890');
  localStorage.setItem(ROLE_KEY, 'law');
  window.location.hash = '#/detail?run=run_live';
  render(<App />);
  const progress = await screen.findByRole('region', { name: 'Analysis progress' });
  expect(within(progress).getByText('This analysis is running')).toBeTruthy();
  expect(await within(progress).findByText('1 of 3 finished')).toBeTruthy();
  expect(screen.queryByText('This analysis is not finished yet')).toBeNull();
});
