// Every screen before any run exists. The run list is mocked empty so this holds whatever ran
// before in the same database.
import { expect, go, nav, openConsole, runButton, statusChip, test, topoNode } from './helpers';

test.beforeEach(async ({ page }) => {
  await page.route(/\/runs\?limit=\d+$/, (route) => route.fulfill({ json: { runs: [] } }));
  await openConsole(page);
});

test('overview offers to start the first run', async ({ page }) => {
  await expect(page.getByText('No runs yet', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('Start a run to see what a regulation change means for whom.', { exact: false })).toBeVisible();
  await expect(statusChip(page)).toHaveText('No runs yet');
  await expect(runButton(page)).toHaveText('Run');
  await expect(runButton(page)).toBeEnabled();
  await expect(nav(page, 'Run pipeline')).not.toContainText('live');
  await page.getByRole('button', { name: 'Start a run' }).click();
  const dialog = page.getByRole('dialog', { name: 'Start a run' });
  await expect(dialog.getByRole('radio')).toHaveCount(3);
  await dialog.getByRole('button', { name: 'Close' }).click();
  await expect(dialog).toBeHidden();
});

test('pipeline, detail and ask have empty states', async ({ page }) => {
  await go(page, 'Run pipeline');
  await expect(page.getByText('No run selected')).toBeVisible();
  await expect(page.getByText('Start a run to watch the agent graph fill in live.')).toBeVisible();
  await go(page, 'Run detail');
  await expect(page.getByText('No run selected')).toBeVisible();
  await expect(page.getByText('Pick a run on the overview, or start a new one.')).toBeVisible();
  await go(page, 'Ask WOMM');
  await expect(page.getByText('No run to ask about')).toBeVisible();
  await expect(page.getByLabel('Question')).toHaveCount(0);
});

test('agents list the configured agents, all queued', async ({ page }) => {
  await go(page, 'Agents');
  const cards = page.locator('[data-agent]');
  await expect(cards).toHaveCount(6);
  for (const id of ['planner', 'router', 'legal', 'fiscal', 'stakeholder', 'synthesis']) {
    const c = page.locator(`[data-agent="${id}"]`);
    await expect(c).toBeVisible();
    if (id !== 'router') await expect(c).toContainText('Queued');
  }
  await expect(page.locator('[data-agent="legal"]')).toContainText(/prompts\/([\w.-]+\/)?legal\.md/);
});

test('topology shows the whole graph with nothing started', async ({ page }) => {
  await go(page, 'Topology');
  for (const id of ['diff', 'planner', 'router', 'legal', 'fiscal', 'stakeholder', 'board', 'citation', 'synthesis', 'dossier']) {
    await expect(topoNode(page, id)).toBeVisible();
  }
  await expect(page.getByText('Inspector')).toBeVisible();
  await topoNode(page, 'planner').click();
  await expect(page.getByText('Latency').locator('..')).toContainText('—');
});

test('settings do not depend on runs', async ({ page }) => {
  await go(page, 'Settings');
  await expect(page.getByText('System version', { exact: true })).toBeVisible();
  await expect(page.getByText('LLM backend per role')).toBeVisible();
});
