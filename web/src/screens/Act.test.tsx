// The Act screen inside the console: comparison picker, inline / side by side / map, and the
// findings of the latest run of the selected comparison.
import { goTo } from '../test/nav';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { App } from '../App';
import { TOKEN_KEY } from '../api';
import { mockApi } from '../test/fixtures';
import { COMPARISONS, OMNIBUS, PROPOSAL_TO_FINAL } from '../test/act';

function open(over: Parameters<typeof mockApi>[0] = {}, hash?: string) {
  const mock = mockApi({
    '/corpus/ai_act/comparisons': () => ({ status: 200, body: COMPARISONS }),
    '/corpus/ai_act/compare/reg2024_1689/reg2024_1689_c20260727': () => ({ status: 200, body: OMNIBUS }),
    '/corpus/ai_act/compare/com2021_206/reg2024_1689': () => ({ status: 200, body: PROPOSAL_TO_FINAL }),
    ...over,
  });
  vi.stubGlobal('fetch', vi.fn(mock.fn));
  localStorage.setItem(TOKEN_KEY, 'test-token-1234567890');
  if (hash) window.location.hash = hash;
  render(<App />);
  if (!hash) goTo('Act');
  return mock;
}

const article = (key: string) => document.querySelector(`article[data-provision="${key}"]`) as HTMLElement | null;
const present = (key: string) => waitFor(() => {
  const el = article(key);
  if (!el) throw new Error(`no article ${key}`);
  return el;
});

const expand = (card: HTMLElement) => fireEvent.click(within(card).getAllByRole('button')[0]);

afterEach(() => vi.unstubAllGlobals());

describe('Act screen', () => {
  it('opens the newest comparison and lists only changed articles', async () => {
    open();
    expect(await screen.findByTestId('act-stat')).toBeTruthy();
    expect((screen.getByLabelText('Comparison') as HTMLSelectElement).value).toBe('reg2024_1689->reg2024_1689_c20260727');
    expect(screen.getByTestId('act-stat').textContent).toMatch(/^3 of 5 articles and annexes changed/);
    expect(article('ai_act/art/27')).toBeTruthy();
    expect(article('ai_act/art/4a')).toBeTruthy();
    expect(article('ai_act/art/26')).toBeNull(); // unchanged, hidden by default
    fireEvent.click(screen.getByLabelText('Show unchanged articles'));
    expect(article('ai_act/art/26')).toBeTruthy();
    expect(within(article('ai_act/art/26')!).getByText('3 with a moved date')).toBeTruthy();
  });

  it('keeps the comparison in the URL, so a reload or a shared link opens the same one', async () => {
    open({}, '#/act?cmp=com2021_206-%3Ereg2024_1689');
    await screen.findByTestId('act-stat');
    expect((screen.getByLabelText('Comparison') as HTMLSelectElement).value).toBe('com2021_206->reg2024_1689');
    fireEvent.change(screen.getByLabelText('Comparison'), { target: { value: 'reg2024_1689->reg2024_1689_c20260727' } });
    await waitFor(() => expect(decodeURIComponent(window.location.hash)).toBe('#/act?cmp=reg2024_1689->reg2024_1689_c20260727'));
  });

  it('marks changed words and shows both sides side by side', async () => {
    open();
    const card = await present('ai_act/art/27');
    expect(card.querySelector('ins')).toBeNull(); // collapsed by default
    expand(card);
    expand(article('ai_act/art/4a')!);
    expect(within(card).getByText('simplified', { exact: false }).closest('ins')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Side by side' }));
    const sbs = article('ai_act/art/27')!;
    expect(within(sbs).getByText('Regulation (EU) 2024/1689')).toBeTruthy();
    expect(within(article('ai_act/art/4a')!).getByText('no counterpart')).toBeTruthy();
  });

  it('the map colours articles by the measure and a tile opens the article in the last diff view', async () => {
    open();
    await screen.findByTestId('act-stat');
    fireEvent.click(screen.getByRole('button', { name: 'Side by side' }));
    fireEvent.click(screen.getByRole('button', { name: 'Map' }));
    fireEvent.change(screen.getByLabelText('Measure'), { target: { value: 'dateMoved' } });
    const tile = screen.getByRole('button', { name: /^Article 26, .*: 3 · Duties whose application date moved · text unchanged$/ });
    expect(within(screen.getByRole('complementary', { name: 'Highest on this measure' })).getByText(/Article 26/)).toBeTruthy();
    fireEvent.click(tile);
    expect(screen.getByRole('button', { name: 'Side by side' }).getAttribute('aria-pressed')).toBe('true');
    expect(article('ai_act/art/26')).toBeTruthy(); // unchanged, shown because it was opened
  });

  it('lays the latest run of the comparison over the articles it cites', async () => {
    open();
    await screen.findByTestId('act-stat');
    expect(screen.getByTestId('act-run').textContent).toMatch(/No finished analysis of this comparison yet/);
    fireEvent.change(screen.getByLabelText('Comparison'), { target: { value: 'com2021_206->reg2024_1689' } });
    await waitFor(() => expect(screen.getByTestId('act-run').textContent).toMatch(/Findings of the latest analysis: \d+/));
    const card = await present('ai_act/innovation/regulatory_sandboxes');
    expect(within(card).getByText(/^was Article 53 · text changed/)).toBeTruthy();
    expand(card);
    expect(within(card).getByRole('region', { name: 'Findings' }).querySelectorAll('[data-finding]').length).toBeGreaterThan(0);
  });

  it('shows the comparison in the header and sidebar, and expands or collapses every article', async () => {
    open();
    await screen.findByTestId('act-stat');
    const header = document.querySelector('header')!;
    await waitFor(() => expect(header.textContent).toContain('Whole act · Regulation (EU) 2024/1689 → Regulation (EU) 2024/1689 as consolidated on 27 July 2026'));
    expect(header.textContent).not.toContain('eval_sme_impacts');
    expect(document.querySelector('aside')!.textContent).toContain('AI Act');
    expect(document.querySelectorAll('article[data-open="true"]')).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Expand all' }));
    expect(document.querySelectorAll('article[data-open="true"]')).toHaveLength(3);
    fireEvent.click(screen.getByRole('button', { name: 'Collapse all' }));
    expect(document.querySelectorAll('article[data-open="true"]')).toHaveLength(0);
    goTo('Overview');
    await waitFor(() => expect(document.querySelector('header')!.textContent).toContain('eval_sme_impacts'));
  });

  it('starts an assessment of the comparison and opens it as an analysis', async () => {
    const mock = open();
    await screen.findByTestId('act-stat');
    fireEvent.click(screen.getByRole('button', { name: 'Run assessment' }));
    await waitFor(() => expect(mock.calls.some((c) => c.method === 'POST' && c.url === '/runs')).toBe(true));
    expect(mock.calls.find((c) => c.method === 'POST')!.body).toEqual({ scenario_id: 'omnibus_2026' });
  });

  it('counts the duties of the role in the law the reader picks, and remembers it', async () => {
    open();
    await screen.findByTestId('act-stat');
    fireEvent.click(screen.getByLabelText('Show unchanged articles'));
    const chips = () => within(article('ai_act/art/26')!).queryByText(/deployer duties/);
    expect(chips()?.textContent).toBe('3 deployer duties');
    fireEvent.change(screen.getByLabelText('Your role in the law'), { target: { value: 'public' } });
    expect(chips()).toBeNull();
    expect((screen.getByLabelText('Measure') as HTMLSelectElement).textContent).toContain('Public body duties in changed paragraphs');
    expect(localStorage.getItem('womm.actor')).toBe('public');
    localStorage.removeItem('womm.actor');
  });

  it('shows an error with a retry when the comparison cannot load', async () => {
    open({ '/corpus/ai_act/compare/reg2024_1689/reg2024_1689_c20260727': () => ({ status: 500, body: { detail: 'boom' } }) });
    expect(await screen.findByText('Cannot load the comparison')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
  });
});
