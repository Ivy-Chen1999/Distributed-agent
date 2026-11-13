// The two interfaces in the console shell: the system (default) and the law. Each lists its own
// screens; the law shows no run machinery or team tooling, keeps the chosen regulation in the
// sidebar, and the choice survives a reload.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { App } from './App';
import { TOKEN_KEY } from './api';
import { ROLE_KEY } from './model/roles';
import { mockApi } from './test/fixtures';
import { COMPARISONS, OMNIBUS } from './test/act';
import { REGULATIONS } from './test/regulations';
import { goTo } from './test/nav';

function boot(role?: string) {
  const mock = mockApi({
    '/corpus/ai_act/comparisons': () => ({ status: 200, body: COMPARISONS }),
    '/corpus/ai_act/compare/': () => ({ status: 200, body: OMNIBUS }),
    '/regulations': () => ({ status: 200, body: REGULATIONS }),
  });
  vi.stubGlobal('fetch', vi.fn(mock.fn));
  localStorage.setItem(TOKEN_KEY, 'test-token-1234567890');
  if (role) localStorage.setItem(ROLE_KEY, role);
  render(<App />);
}

const navItems = () => within(screen.getByRole('navigation', { name: 'Screens' })).getAllByRole('button').map((b) => b.textContent?.replace(/live$/, '').trim());
const title = () => document.querySelector('header div div')?.textContent;
const header = () => document.querySelector('header')!;
const switchTo = (name: string) => fireEvent.click(within(screen.getByRole('group', { name: 'Interface' })).getByRole('button', { name }));
const card = () => within(screen.getByRole('complementary', { name: 'Navigation' })).getByText('Regulation').parentElement!;

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe('interfaces in the console', () => {
  it('opens on the system: runs, agents and evaluation, with run status and team tooling', async () => {
    boot();
    expect(within(screen.getByRole('group', { name: 'Interface' })).getByRole('button', { name: 'The system' }).getAttribute('aria-pressed')).toBe('true');
    expect(navItems()).toEqual(['Overview', 'Run pipeline', 'Run detail', 'Agents', 'Topology', 'Evolution', 'Settings']);
    expect(title()).toBe('Overview');
    expect(screen.getByTitle('Copy system version')).toBeTruthy();
    await waitFor(() => expect(within(header()).getByRole('button', { name: /^(Run|Re-run)$/ })).toBeTruthy());
  });

  it('the law lists Regulations, Act and Analysis, starts at Regulations, and hides run machinery', async () => {
    boot('law');
    expect(navItems()).toEqual(['Regulations', 'Act', 'Analysis']);
    expect(title()).toBe('Regulations');
    expect(await screen.findByTestId('regs-count')).toBeTruthy();
    expect(screen.queryByTitle('Copy system version')).toBeNull();
    expect(within(header()).queryByRole('button', { name: /^(Run|Re-run)$/ })).toBeNull();
    expect(within(header()).queryByText(/^(Succeeded|Degraded|No runs yet)$/)).toBeNull();
    expect(card().textContent).toContain('No regulation chosen');
  });

  it('the law keeps the regulation the user chose in the sidebar while moving between screens', async () => {
    boot('law');
    goTo('Act');
    await screen.findByTestId('act-stat');
    await waitFor(() => expect(card().textContent).toContain('AI Act'));
    goTo('Analysis');
    expect(card().textContent).toContain('AI Act');
    switchTo('The system');
    expect(card().textContent).not.toContain('No regulation chosen'); // the system shows the selected run's scenario
  });

  it('switching interface goes to its home unless the screen is in both, and the choice persists', async () => {
    boot();
    goTo('Agents');
    expect(title()).toBe('Agents');
    switchTo('The law');
    expect(title()).toBe('Regulations'); // Agents is not in the law
    expect(localStorage.getItem(ROLE_KEY)).toBe('law');
    goTo('Analysis');
    switchTo('The system');
    expect(title()).toBe('Run detail'); // the same screen, named for the system
    cleanup();
    boot();
    await waitFor(() => expect(within(screen.getByRole('group', { name: 'Interface' })).getByRole('button', { name: 'The law' }).getAttribute('aria-pressed')).toBe('false'));
  });

  it('a role saved by the earlier five-preset console opens in its interface', () => {
    boot('reader');
    expect(navItems()).toEqual(['Regulations', 'Act', 'Analysis']);
  });
});
