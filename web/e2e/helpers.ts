// Shared fixtures and helpers for the console e2e specs.
import { expect, test as base, type APIRequestContext, type Locator, type Page } from '@playwright/test';

export const TOKEN = process.env.WOMM_E2E_TOKEN ?? process.env.WOMM_E2E_LIVE_TOKEN ?? '';
export const AUTH = { Authorization: `Bearer ${TOKEN}` };

export const SCENARIOS = {
  sme: 'eval_sme_impacts',
  costs: 'eval_provider_compliance_costs',
  demo: 'demo_penalties_amended',
} as const;

/** Scenario names as the console shows them (model/scenario.ts scenarioName). */
export const SCENARIO_NAMES: Record<string, string> = {
  eval_sme_impacts: 'SME impacts',
  eval_provider_compliance_costs: 'Provider compliance costs',
  demo_penalties_amended: 'Penalties amended',
};

export const SCREENS = ['Overview', 'Run pipeline', 'Run detail', 'Agents', 'Topology', 'Ask WOMM', 'Settings'] as const;
export type ScreenTitle = (typeof SCREENS)[number];

/** Design tokens (TH in web/design/WOMM Console.dc.html). */
export const TH = {
  light: { bg: '#F3F6F6', card: '#FFFFFF', soft: '#EDF2F2', line: '#DCE4E4', ink: '#0F0F0F', n1: '#4A565A', n2: '#626E71', accInk: '#00737A', accSoft: '#D6F5F6', side: '#0F0F0F', hl: '#C4F1F3' },
  dark: { bg: '#0A1011', card: '#111A1C', soft: '#182326', line: '#253235', ink: '#EDF3F3', n1: '#A9B5B7', n2: '#8A9799', accInk: '#3ED9E0', accSoft: '#0C3033', side: '#050809', hl: '#0F4A4E' },
} as const;

export function rgb(hex: string): string {
  const n = parseInt(hex.slice(1), 16);
  return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
}

export interface RunDetail {
  run_id: string;
  scenario_id: string;
  status: string;
  system_version: string;
  grounding?: { passed: number; total: number } | null;
  board?: { finding_id: string; agent: string; provision_key: string; evidence: { source_id: string; quote: string }[] }[] | null;
  failures?: { agent: string; error_kind: string }[] | null;
  usage?: { role: string; agent?: string | null; input_tokens: number; output_tokens: number; cost_usd?: number | null }[] | null;
  dossier?: {
    impacts: { impact_id: string; summary: string; findings: { finding_id: string; agent: string; provision_key: string; affected_actor: string; evidence: { source_id: string; quote: string }[] }[] }[];
    chains: { impact_ids: string[]; description: string }[];
    disagreements: { finding_ids: string[]; note: string }[];
    open_questions: { question: string; finding_id?: string | null; reason: string }[];
    failed_experts: { agent: string; error_kind: string }[];
  } | null;
}

export interface RunEvent {
  seq: number;
  node: string;
  event: string;
  payload: Record<string, unknown>;
  at: string;
}

export async function startRun(request: APIRequestContext, scenarioId: string, overrides?: object): Promise<string> {
  const res = await request.post('/runs', { headers: AUTH, data: overrides ? { scenario_id: scenarioId, overrides } : { scenario_id: scenarioId } });
  expect(res.status(), await res.text()).toBe(202);
  return ((await res.json()) as { run_id: string }).run_id;
}

export async function getRun(request: APIRequestContext, runId: string): Promise<RunDetail> {
  const res = await request.get(`/runs/${runId}`, { headers: AUTH });
  expect(res.ok()).toBeTruthy();
  return (await res.json()) as RunDetail;
}

export async function getEvents(request: APIRequestContext, runId: string): Promise<RunEvent[]> {
  const res = await request.get(`/runs/${runId}/events?limit=1000`, { headers: AUTH });
  return ((await res.json()) as { events: RunEvent[] }).events;
}

export async function waitRun(request: APIRequestContext, runId: string, timeoutMs = 60_000): Promise<RunDetail> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const run = await getRun(request, runId);
    if (!['queued', 'running'].includes(run.status)) return run;
    if (Date.now() > deadline) throw new Error(`run ${runId} still ${run.status}`);
    await new Promise((r) => setTimeout(r, 250));
  }
}

/** Start a run through the API and wait for it; it becomes the console's latest run. */
export async function finishedRun(request: APIRequestContext, scenarioId: string, overrides?: object): Promise<RunDetail> {
  return waitRun(request, await startRun(request, scenarioId, overrides));
}

export async function listRuns(request: APIRequestContext, limit = 20): Promise<{ run_id: string; scenario_id: string; status: string; system_version: string }[]> {
  const res = await request.get(`/runs?limit=${limit}`, { headers: AUTH });
  return ((await res.json()) as { runs: { run_id: string; scenario_id: string; status: string; system_version: string }[] }).runs;
}

export async function systemInfo(request: APIRequestContext): Promise<{ version_id: string; available_backends: string[]; [k: string]: unknown }> {
  const res = await request.get('/system', { headers: AUTH });
  return (await res.json()) as { version_id: string; available_backends: string[] };
}

/** Put the token (and optionally the theme) in localStorage before the console loads. */
export async function signIn(page: Page, opts: { theme?: 'light' | 'dark' } = {}): Promise<void> {
  await page.addInitScript(
    ([token, theme]) => {
      localStorage.setItem('womm.token', token);
      if (theme) localStorage.setItem('womm.theme', theme);
    },
    [TOKEN, opts.theme ?? ''] as const,
  );
}

/** Open the console signed in and wait for the shell. */
export async function openConsole(page: Page, opts: { theme?: 'light' | 'dark' } = {}): Promise<void> {
  await signIn(page, opts);
  await page.goto('/');
  await expect(page.locator('header')).toBeVisible();
}

export const nav = (page: Page, title: ScreenTitle): Locator => page.locator('aside nav button', { hasText: title });

export async function go(page: Page, title: ScreenTitle): Promise<void> {
  await nav(page, title).click();
  await expect(screenTitle(page)).toHaveText(title);
}

export const screenTitle = (page: Page): Locator => page.locator('header > div').first().locator('div').first();
export const runButton = (page: Page): Locator => page.locator('header button', { hasText: /^(Run|Re-run|Running…|Starting…)$/ });
export const statusChip = (page: Page): Locator => page.locator('header > div:nth-child(2) > span').first();
export const svButton = (page: Page): Locator => page.getByRole('button', { name: /^sv_/ });
export const toast = (page: Page): Locator => page.getByRole('status').filter({ hasNotText: /^$/ }).last();
export const pipeNode = (page: Page, id: string): Locator => page.locator(`[data-node="${id}"]`);
export const topoNode = (page: Page, id: string): Locator => page.locator(`[data-topo="${id}"]`);
export const card = (page: Page, label: string | RegExp): Locator =>
  page.locator('main div').filter({ has: page.getByText(label, { exact: typeof label === 'string' }) });

/** Start a run through the header Run button and the scenario picker. */
export async function startFromPicker(page: Page, scenarioId: string, opener?: Locator): Promise<void> {
  await (opener ?? runButton(page)).click();
  const dialog = page.getByRole('dialog', { name: 'Start a run' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('radio', { name: new RegExp(scenarioId) }).click();
  await expect(dialog.getByRole('radio', { name: new RegExp(scenarioId) })).toHaveAttribute('aria-checked', 'true');
  await dialog.getByRole('button', { name: /^Run$/ }).click();
  await expect(dialog).toBeHidden();
}

export const shortRunId = (id: string): string => id.slice(0, 12);

/** Collects console errors and page errors; `expectClean` fails the test if there were any. */
export interface ErrorLog {
  console: string[];
  page: string[];
  expectClean: () => void;
}

export const test = base.extend<{ errors: ErrorLog }>({
  errors: [
    async ({ page }, use) => {
      const log: ErrorLog = {
        console: [],
        page: [],
        expectClean: () => {
          expect(log.page, 'page errors').toEqual([]);
          expect(log.console, 'console errors').toEqual([]);
        },
      };
      page.on('console', (m) => m.type() === 'error' && log.console.push(m.text()));
      page.on('pageerror', (e) => log.page.push(String(e)));
      await use(log);
      // Uncaught exceptions are never acceptable, whatever the test mocks.
      expect(log.page, 'page errors').toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };
