// The law interface against the real API on the scripted fake LLM: start from Regulations, open
// one of the act's finished analyses and read it as a document, with no run machinery in sight.
import { SCENARIOS, expect, finishedRun, openConsole, test } from './helpers';

test('in the law interface, a finished analysis opens from its regulation and reads without run machinery', async ({ page }) => {
  const run = await finishedRun(page.request, SCENARIOS.demo); // ends degraded: one expert times out
  await page.addInitScript(() => localStorage.setItem('womm.role', 'law'));
  await openConsole(page);

  await expect(page.locator('aside nav button')).toHaveText(['Regulations', 'Act', 'Analysis']);
  await expect(page.locator('header')).not.toContainText(/Degraded|Succeeded/);

  const act = page.locator('li[data-regulation="ai_act"]');
  await act.getByRole('button', { name: /finished analys/ }).click();
  const row = act.getByRole('list', { name: 'Analyses' }).locator(`li[data-run="${run.run_id}"] button`);
  await expect(row).toContainText('COM(2021) 206 → Regulation (EU) 2024/1689');
  await expect(row).toContainText('Incomplete');
  await row.click();

  const main = page.locator('main');
  await expect(page.getByTestId('analysis-incomplete')).toContainText('view is missing');
  await expect(main.getByRole('tab')).toHaveText([/^Impacts/, /^Impact chains/, /^Disagreements/, /^Open questions/, /^Costs/]);
  await expect(main).not.toContainText(run.system_version);
  await expect(main).not.toContainText('Event log');
});
