// Settings: system version facts, staged overrides and the derived version, the self-check.
import { SCENARIOS, expect, finishedRun, getRun, go, listRuns, openConsole, statusChip, svButton, systemInfo, test, toast, topoNode } from './helpers';
import type { Page } from '@playwright/test';

const row = (page: Page, label: string) => page.locator('main').getByText(label, { exact: true }).locator('..');
const staged = (page: Page) => page.getByTestId('staged');

test('system version facts and the fake backend self-check state', async ({ page }) => {
  const sys = await systemInfo(page.request);
  await openConsole(page);
  await go(page, 'Settings');
  await expect(row(page, 'id')).toContainText(sys.version_id);
  // The e2e version is built in code, so it has no source file (regression: this crashed).
  await expect(row(page, 'file')).toContainText('—');
  await expect(row(page, 'git')).toContainText(/(clean|dirty)\)|—/);
  await expect(row(page, 'claude CLI')).toContainText('—');
  await expect(row(page, 'max parallel calls')).toContainText('3');
  await expect(page.getByText('Backend not ready')).toHaveCount(0);
  const check = page.getByText('Isolation self-check', { exact: true }).locator('xpath=../..');
  await expect(check).toContainText('not run');
  await expect(check).toContainText('The self-check runs only when a role uses the claude_code backend.');
  for (const role of ['Planner', 'Experts', 'Synthesis', 'Coverage judge']) {
    const r = row(page, role);
    await expect(r.getByRole('button')).toHaveText(['api', 'fake']);
    await expect(r.getByRole('button', { name: 'fake' })).toHaveAttribute('aria-pressed', 'true');
  }
  await expect(page.getByRole('button', { name: 'Shadow' })).toHaveAttribute('aria-pressed', 'true');
  await expect(staged(page)).toHaveCount(0);
});

test('self-check results and a backend that is not ready (mocked /system)', async ({ page }) => {
  await page.route('**/system', async (route) => {
    const res = await route.fetch();
    const json = await res.json();
    json.backend_ready = false;
    json.backend_error = 'LLMError: [auth] Not logged in';
    json.code = { ...json.code, claude_cli_version: '2.1.284 (Claude Code)' };
    json.self_check = { passed: false, checks: { auth_preflight: true, no_tools: false, canary_absent: true }, problems: ['tools were offered to the model'] };
    await route.fulfill({ response: res, json });
  });
  await openConsole(page);
  await go(page, 'Settings');
  const check = page.getByText('Isolation self-check', { exact: true }).locator('xpath=../..');
  await expect(check).toContainText('2 of 3 passed');
  await expect(row(page, 'Claude CLI is logged in')).toContainText('Pass');
  await expect(row(page, 'No tools')).toContainText('Fail');
  await expect(row(page, 'No leaked user instructions')).toContainText('Pass');
  await expect(check).toContainText('tools were offered to the model');
  await expect(page.getByText('Backend not ready: LLMError: [auth] Not logged in')).toBeVisible();
  await expect(row(page, 'claude CLI')).toContainText('2.1.284');
});

test('staged overrides show a banner; the next run gets a derived system version', async ({ page }) => {
  await finishedRun(page.request, SCENARIOS.sme);
  const base = (await systemInfo(page.request)).version_id;
  await openConsole(page);
  await go(page, 'Settings');
  await expect(svButton(page)).toHaveText(base);

  await row(page, 'Planner').getByRole('button', { name: 'api' }).click();
  await expect(staged(page)).toContainText('Staged for the next run');
  await expect(staged(page)).toContainText('planner → api');
  await expect(staged(page)).toContainText(`so its id will differ from ${base}`);
  await expect(svButton(page)).toHaveText(`${base} · staged`);

  await row(page, 'Experts').getByRole('button', { name: 'api' }).click();
  await expect(staged(page)).toContainText('expert:legal → api · expert:fiscal → api · expert:stakeholder → api');
  await row(page, 'Experts').getByRole('button', { name: 'fake' }).click();
  await expect(staged(page)).not.toContainText('expert:');

  await page.getByRole('button', { name: 'Enforce' }).click();
  await expect(page.getByRole('button', { name: 'Enforce' })).toHaveAttribute('aria-pressed', 'true');
  await expect(staged(page)).toContainText('planner → api · router → enforce');
  await expect(page.getByText('Enforce mode skips experts marked not relevant.')).toBeVisible();

  // Reset clears everything.
  await staged(page).getByRole('button', { name: 'Reset' }).click();
  await expect(staged(page)).toHaveCount(0);
  await expect(svButton(page)).toHaveText(base);

  // Stage again and run.
  await row(page, 'Planner').getByRole('button', { name: 'api' }).click();
  await page.getByRole('button', { name: 'Enforce' }).click();
  await staged(page).getByRole('button', { name: 'Run with these settings' }).click();
  const dialog = page.getByRole('dialog', { name: 'Start a run' });
  await expect(dialog).toContainText('With staged settings: planner → api, router → active. The run gets a new system version id.');
  const submitted = page.waitForRequest((r) => r.method() === 'POST' && r.url().endsWith('/runs'));
  await dialog.getByRole('radio', { name: new RegExp(SCENARIOS.sme) }).click();
  await dialog.getByRole('button', { name: /^Run$/ }).click();
  expect((await submitted).postDataJSON()).toEqual({ scenario_id: SCENARIOS.sme, overrides: { backends: { planner: 'api' }, router_mode: 'active' } });
  await expect(toast(page)).toContainText(/Run queued · run_\w+ · sv_\w+/);
  const derived = (await toast(page).textContent())!.match(/sv_\w+/)![0];
  expect(derived).not.toBe(base);

  await expect(statusChip(page)).toHaveText('Succeeded', { timeout: 30_000 });
  await expect(svButton(page)).toHaveText(`${derived} · staged`);
  const [latest] = await listRuns(page.request, 1);
  expect(latest.system_version).toBe(derived);
  const run = await getRun(page.request, latest.run_id);
  expect(run.usage!.find((u) => u.role === 'planner')).toEqual(expect.objectContaining({ backend: 'api' }));

  await go(page, 'Overview');
  const tableRow = page.getByText('Recent runs', { exact: true }).locator('..').locator(`button[title="${latest.run_id}"]`);
  await expect(tableRow).toContainText(derived);
  const older = page.getByText('Recent runs', { exact: true }).locator('..').locator('button[title^="run_"]').nth(1);
  await expect(older).toContainText(base);
  await go(page, 'Run pipeline');
  await expect(page.getByText('Router decisions', { exact: true }).locator('xpath=../..')).toContainText('active');
  await expect(page.locator('[data-node="router"]')).not.toContainText('shadow');
  await go(page, 'Topology');
  await expect(topoNode(page, 'planner')).toContainText('api');
  await expect(topoNode(page, 'legal')).toContainText('fake');
});
