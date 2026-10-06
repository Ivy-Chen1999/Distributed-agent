import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Evolution } from './Evolution';
import { TOKEN_KEY } from '../api';
import { PROMPT, REJECTED, TOPOLOGY, details, diffs, lineage } from '../test/evolution';

type Handler = (path: string) => { status: number; body: unknown } | undefined;

function stub(override?: Handler) {
  const calls: string[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url;
    calls.push(url);
    const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
    const o = override?.(url);
    if (o) return json(o.status, o.body);
    if (url === '/evolution/lineage') return json(200, lineage());
    const m = url.match(/^\/evolution\/candidates\/([^/]+)(\/diff)?$/);
    if (m && m[2] && diffs[m[1]]) return json(200, diffs[m[1]]);
    if (m && !m[2] && details[m[1]]) return json(200, details[m[1]]);
    return json(404, { detail: `unknown version ${m?.[1] ?? url}` });
  });
  vi.stubGlobal('fetch', fn);
  return calls;
}

const tree = () => screen.getByRole('list', { name: 'Lineage' });
const pick = (id: string) => fireEvent.click(within(tree()).getByText(id.slice(0, 15)).closest('button')!);

beforeEach(() => localStorage.setItem(TOKEN_KEY, 'test-token-1234567890'));
afterEach(() => vi.unstubAllGlobals());

describe('Evolution page', () => {
  it('renders the lineage tree with the topology badge and opens the latest decision', async () => {
    stub();
    render(<Evolution />);
    await screen.findByRole('list', { name: 'Lineage' });
    const rows = within(tree()).getAllByRole('button');
    expect(rows).toHaveLength(4);
    const topo = rows.find((b) => b.getAttribute('data-version') === TOPOLOGY.version_id)!;
    expect(within(topo).getByText('New expert')).toBeTruthy();
    expect(within(topo).getByText('Promoted')).toBeTruthy();
    expect(within(topo).getByText('+1 api twin')).toBeTruthy();
    // The latest decided candidate opens first.
    expect(await screen.findByRole('heading', { name: REJECTED.version_id })).toBeTruthy();
  });

  it('shows a promoted weak-mode decision verbatim and the new-expert highlight', async () => {
    stub();
    render(<Evolution />);
    await screen.findByRole('list', { name: 'Lineage' });
    pick(TOPOLOGY.version_id);
    const label = await screen.findAllByText('promoted (weak threshold: directional)');
    expect(label.length).toBeGreaterThan(0);
    const highlight = screen.getByTestId('new-expert');
    expect(within(highlight).getByText('New expert: workforce')).toBeTruthy();
    expect(within(highlight).getByText(/effects on workers, skills/)).toBeTruthy();
    expect(within(highlight).getByText(/no expert owns it/)).toBeTruthy();
    const holdout = screen.getByTestId('holdout');
    expect(within(holdout).getByText('Weak threshold (directional) · sv_topologyapi1 vs incumbent sv_seedapi00001 · 3 proposals')).toBeTruthy();
    expect(within(holdout).getByText('+0.060')).toBeTruthy();
    expect(within(holdout).getAllByText('CI not available')).toHaveLength(3);
  });

  it('a candidate without a decision shows train/val only and "not submitted to holdout"', async () => {
    stub();
    render(<Evolution />);
    await screen.findByRole('list', { name: 'Lineage' });
    pick(PROMPT.version_id);
    const holdout = await screen.findByTestId('holdout');
    await waitFor(() => expect(within(screen.getByTestId('holdout')).getByText(/not submitted to holdout/)).toBeTruthy());
    expect(within(holdout).queryByTestId('decision')).toBeNull();
    expect(within(screen.getByTestId('metrics')).getByText('0.620 ± 0.020')).toBeTruthy();
    expect(screen.queryByTestId('new-expert')).toBeNull();
  });

  it('an R37 regression shows a warning and leaves the decision badge unchanged', async () => {
    stub();
    render(<Evolution />);
    await screen.findByRole('heading', { name: REJECTED.version_id });
    expect(screen.getByTestId('r37-warning').textContent).toMatch(/decision is unchanged/);
    const row = within(tree()).getAllByRole('button').find((b) => b.getAttribute('data-version') === REJECTED.version_id)!;
    expect(within(row).getByText('Promoted')).toBeTruthy();
    expect(within(row).getByText('R37 regression')).toBeTruthy();
    expect(screen.getByTestId('decision-label').textContent).toBe(REJECTED.label);
  });

  it('loads the per-prompt diffs only when asked', async () => {
    const calls = stub();
    render(<Evolution />);
    await screen.findByRole('list', { name: 'Lineage' });
    pick(PROMPT.version_id);
    fireEvent.click(await screen.findByText('Show prompt diffs'));
    const block = (await screen.findByText('+Quantify every cost you name.')).closest('[data-prompt-diff]')!;
    expect(block.getAttribute('data-prompt-diff')).toBe('expert:fiscal');
    expect(calls.filter((c) => c.endsWith('/diff'))).toEqual([`/evolution/candidates/${PROMPT.version_id}/diff`]);
  });

  it('has an empty state', async () => {
    stub((p) => (p === '/evolution/lineage' ? { status: 200, body: { nodes: [], publish_summary: false } } : undefined));
    render(<Evolution />);
    expect(await screen.findByText('No candidates yet')).toBeTruthy();
    expect(screen.getByText(/womm evolve cycle/)).toBeTruthy();
  });

  it('has an error state with retry', async () => {
    let fail = true;
    stub((p) => (p === '/evolution/lineage' && fail ? { status: 500, body: { detail: 'database unavailable' } } : undefined));
    render(<Evolution />);
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/database unavailable/);
    fail = false;
    fireEvent.click(within(alert).getByText('Retry'));
    expect(await screen.findByRole('list', { name: 'Lineage' })).toBeTruthy();
  });

  it('shows the error for an unknown candidate', async () => {
    stub((p) => (p.startsWith('/evolution/candidates/') ? { status: 404, body: { detail: 'unknown version' } } : undefined));
    render(<Evolution />);
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/Could not load the candidate/);
  });
});
