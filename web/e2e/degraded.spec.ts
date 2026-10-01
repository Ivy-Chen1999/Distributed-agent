// A degraded run (demo diff: the stakeholder expert times out) on every screen that shows it.
import { SCENARIOS, expect, finishedRun, go, openConsole, pipeNode, rgb, statusChip, test, topoNode } from './helpers';

const RED = rgb('#E5484D');

test('the failed expert is red with its error_kind everywhere', async ({ page }) => {
  const run = await finishedRun(page.request, SCENARIOS.demo);
  expect(run.status).toBe('degraded');
  await openConsole(page);
  await expect(statusChip(page)).toHaveText('Degraded');

  // Overview: status KPI, sub-label, "Failed experts" row, red latency bar.
  const status = page.locator('main').getByText('Status', { exact: true }).first().locator('..');
  await expect(status).toContainText('Degraded');
  await expect(status).toContainText('Stakeholder expert timed out');
  const failedRow = page.getByText('Needs your review', { exact: true }).locator('..').getByRole('button').filter({ hasText: 'Failed experts' });
  await expect(failedRow).toContainText('1');
  await expect(failedRow).toContainText('Stakeholder · timeout');
  const bar = page.getByText('Agent latency', { exact: true }).locator('xpath=../..').locator('div[style*="grid-template-columns: 110px"]').filter({ hasText: 'Stakeholder' });
  await expect(bar).toContainText(/timeout · \d+\.\ds/);
  await expect(bar.locator('div > div')).toHaveCSS('background-color', RED);

  // Pipeline.
  await go(page, 'Run pipeline');
  await expect(pipeNode(page, 'stakeholder')).toContainText('Failed · timeout');
  await expect(pipeNode(page, 'stakeholder')).toHaveCSS('border-top-color', RED);
  await pipeNode(page, 'stakeholder').click();
  const selected = page.getByText('Selected node', { exact: true }).locator('..');
  await expect(selected).toContainText('Failed · timeout');
  await expect(selected).toContainText('Timed out in this run.');
  await expect(selected.getByText('error_kind', { exact: true }).locator('..')).toContainText('timeout');

  // Agents.
  await go(page, 'Agents');
  const agent = page.locator('[data-agent="stakeholder"]');
  await expect(agent).toContainText('Failed · timeout');
  await expect(agent).toHaveCSS('border-top-color', RED);
  await expect(agent.getByText('error', { exact: true }).locator('..')).toContainText('timeout');

  // Topology.
  await go(page, 'Topology');
  await topoNode(page, 'legal').click();
  await expect(topoNode(page, 'stakeholder')).toHaveCSS('border-top-color', RED);
  await topoNode(page, 'stakeholder').click();
  const inspector = page.getByText('Inspector', { exact: true }).locator('..');
  await expect(inspector).toContainText('Stakeholder');
  await expect(inspector.getByText('error_kind', { exact: true }).locator('..')).toContainText('timeout');

  // Run detail: failed-experts count and the "Failed experts" row.
  await go(page, 'Run detail');
  const header = page.getByText('Impact dossier', { exact: true }).locator('xpath=../..');
  await expect(header.getByText('failed experts', { exact: true }).locator('..')).toContainText('1');
  await page.getByRole('tab', { name: /^Open questions/ }).click();
  const failed = page.getByText('Failed experts', { exact: true }).locator('..');
  await expect(failed).toContainText('Stakeholder');
  await expect(failed).toContainText(/error_kind: timeout · \d+\.\ds/);
  await expect(failed).toContainText('Run continued as degraded');
  await expect(failed.locator('div[title]')).toHaveCSS('border-top-color', RED);

  // Event log: the failure is a WARN line.
  await page.getByRole('tab', { name: 'Event log' }).click();
  await expect(page.locator('main')).toContainText(/WARN\s*stakeholder\s*error_kind=timeout after \d+\.\ds · run continues as degraded/);
  await expect(page.locator('main')).toContainText(/WARN\s*dossier\s*assembled · status=degraded/);
});
