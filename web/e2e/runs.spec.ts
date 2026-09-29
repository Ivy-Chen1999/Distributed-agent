// Start runs through the scenario picker and watch the pipeline fill in live.
import { SCENARIOS, SCENARIO_NAMES, expect, getRun, listRuns, nav, openConsole, pipeNode, runButton, screenTitle, startFromPicker, statusChip, test, toast, type RunDetail } from './helpers';
import type { Page } from '@playwright/test';

const EXPERTS = ['legal', 'fiscal', 'stakeholder'];
const ALL_NODES = ['diff', 'planner', 'router', ...EXPERTS, 'board', 'citation', 'synthesis', 'dossier'];

async function newestRun(page: Page): Promise<RunDetail> {
  const [first] = await listRuns(page.request, 1);
  return getRun(page.request, first.run_id);
}

test.describe('live pipeline', () => {
  test('SME impacts: statuses progress, live strip, router, board and selection', async ({ page }) => {
    await openConsole(page);
    await startFromPicker(page, SCENARIOS.sme);
    await expect(toast(page)).toContainText(/Run queued · run_/);
    await expect(screenTitle(page)).toHaveText('Run pipeline');

    // Running: live badge, disabled Run button, pulsing status.
    await expect(nav(page, 'Run pipeline')).toContainText('live');
    await expect(runButton(page)).toHaveText('Running…');
    await expect(runButton(page)).toBeDisabled();
    await expect(statusChip(page)).toHaveText(/Queued|Running/);

    // queued -> running -> done, observed on the planner and on synthesis.
    await expect(pipeNode(page, 'planner')).toContainText('Running');
    await expect(pipeNode(page, 'synthesis')).toContainText('Queued');
    await expect(pipeNode(page, 'legal')).toContainText('Running');
    await expect(pipeNode(page, 'planner')).toContainText('Done');
    await expect(pipeNode(page, 'stakeholder')).toContainText('Running');

    // Router decisions: shadow mode, one row per expert.
    const router = page.getByText('Router decisions', { exact: true }).locator('xpath=../..');
    await expect(router).toContainText('shadow');
    await expect(router).toContainText('Shadow mode logs each decision but runs every expert anyway.');
    for (const name of ['Legal', 'Fiscal', 'Stakeholder']) await expect(router).toContainText(name);
    await expect(router).toContainText('relevant');
    await expect(pipeNode(page, 'router')).toContainText('shadow');

    // Board feed while experts are still running: counts with "Checking quote".
    const board = page.getByText('Shared impact board · live', { exact: true }).locator('xpath=../..');
    await expect(board.getByText('Checking quote').first()).toBeVisible();
    await expect(board).toContainText(/Posted \d+ findings? to the shared board\./);

    // Selecting a node updates the "Selected node" panel.
    await pipeNode(page, 'planner').click();
    const selected = page.getByText('Selected node', { exact: true }).locator('xpath=..');
    await expect(selected).toContainText('Impact Planner');
    await expect(selected).toContainText('Turns the diff into an impact plan');
    await pipeNode(page, 'citation').click();
    await expect(selected).toContainText('Citation check');

    // Finished.
    await expect(statusChip(page)).toHaveText('Succeeded', { timeout: 30_000 });
    await expect(nav(page, 'Run pipeline')).not.toContainText('live');
    await expect(runButton(page)).toHaveText('Re-run');
    await expect(runButton(page)).toBeEnabled();
    for (const id of ALL_NODES) await expect(pipeNode(page, id)).toContainText('Done');

    const run = await newestRun(page);
    expect(run.status).toBe('succeeded');
    const g = run.grounding!;
    // Strip: elapsed, tokens, cost, quotes verified.
    const strip = page.locator('main').getByText('quotes verified', { exact: true }).locator('..');
    await expect(strip).toContainText(`${g.passed}/${g.total}`);
    const tokens = run.usage!.reduce((a, u) => a + u.input_tokens + u.output_tokens, 0);
    const kTok = tokens >= 1000 ? (tokens / 1000).toFixed(1) + 'k' : String(tokens);
    await expect(page.locator('main').getByText('tokens', { exact: true }).locator('..')).toContainText(kTok);
    const cost = run.usage!.reduce((a, u) => a + (u.cost_usd ?? 0), 0);
    await expect(page.locator('main').getByText('cost', { exact: true }).locator('..')).toContainText('$' + cost.toFixed(2));
    await expect(page.locator('main').getByText('elapsed', { exact: true }).locator('..')).toContainText(/\d+\.\ds/);
    await expect(pipeNode(page, 'citation')).toContainText(`${g.passed}/${g.total}`);
    await expect(pipeNode(page, 'planner')).toContainText('tok');

    // Board feed after the run: every finding, all quotes verified.
    await expect(board).toContainText(`${run.board!.length} of ${run.board!.length} findings`);
    await expect(board.getByText('Quote verified')).toHaveCount(run.board!.length);
    await expect(board.getByText('Quote not found')).toHaveCount(0);
    await expect(board.getByText('Checking quote')).toHaveCount(0);
  });

  test('Provider compliance costs: one quote is not found', async ({ page }) => {
    await openConsole(page);
    await startFromPicker(page, SCENARIOS.costs);
    await expect(screenTitle(page)).toHaveText('Run pipeline');
    await expect(pipeNode(page, 'legal')).toContainText(/Running|Done/);
    await expect(statusChip(page)).toHaveText('Succeeded', { timeout: 30_000 });
    const run = await newestRun(page);
    expect(run.scenario_id).toBe(SCENARIOS.costs);
    const g = run.grounding!;
    expect(g.passed).toBe(g.total - 1);
    const board = page.getByText('Shared impact board · live', { exact: true }).locator('xpath=../..');
    await expect(board.getByText('Quote not found')).toHaveCount(1);
    await expect(board.getByText('Quote verified')).toHaveCount(run.board!.length - 1);
    await expect(page.locator('main').getByText('quotes verified', { exact: true }).locator('..')).toContainText(`${g.passed}/${g.total}`);
    await expect(page.locator('header')).toContainText(SCENARIO_NAMES[SCENARIOS.costs]);
  });

  test('Penalties amended (demo diff): an expert times out and the run is degraded', async ({ page }) => {
    await openConsole(page);
    await startFromPicker(page, SCENARIOS.demo);
    await expect(screenTitle(page)).toHaveText('Run pipeline');
    await expect(pipeNode(page, 'stakeholder')).toContainText('Running');
    await expect(pipeNode(page, 'stakeholder')).toContainText('Failed · timeout', { timeout: 30_000 });
    await expect(statusChip(page)).toHaveText('Degraded', { timeout: 30_000 });
    await expect(pipeNode(page, 'legal')).toContainText('Done');
    await expect(pipeNode(page, 'dossier')).toContainText('Done');
    await expect(pipeNode(page, 'diff')).toContainText('3 changed provisions');
    const run = await newestRun(page);
    expect(run.status).toBe('degraded');
    expect(run.failures).toEqual([expect.objectContaining({ agent: 'stakeholder', error_kind: 'timeout' })]);
  });

  test('the picker lists every scenario and preselects the current one', async ({ page }) => {
    await openConsole(page);
    await runButton(page).click();
    const dialog = page.getByRole('dialog', { name: 'Start a run' });
    for (const [id, name] of Object.entries(SCENARIO_NAMES)) {
      await expect(dialog.getByRole('radio', { name: new RegExp(id) })).toContainText(name);
    }
    await expect(dialog.getByRole('radio', { name: /Demo diff/ })).toHaveCount(1);
    await expect(dialog.getByRole('radio', { name: /Evaluation · IA §6\.1\.4/ })).toHaveCount(1);
    // The newest run is the degraded demo run from the previous test (or any other): its
    // scenario is preselected.
    const [latest] = await listRuns(page.request, 1);
    await expect(dialog.getByRole('radio', { name: new RegExp(latest?.scenario_id ?? SCENARIOS.sme) })).toHaveAttribute('aria-checked', 'true');
    // Close via the overlay.
    await page.mouse.click(10, 10);
    await expect(dialog).toBeHidden();
  });
});
