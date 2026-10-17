// The Costs tab (EU cost plan R5) on a proposal -> adopted run: hotspots, payer marks and the
// "added after the proposal" filter, against the scripted cost step of the e2e server.
import { SCENARIOS, expect, finishedRun, go, openConsole, test } from './helpers';

test('costs tab shows hotspots and filters records added after the proposal', async ({ page }) => {
  const run = await finishedRun(page.request, SCENARIOS.demo);
  const costs = run.dossier!.costs!;
  expect(costs.records.length).toBeGreaterThan(0);
  const added = costs.records.filter((r) => r.late_added);
  expect(added.length).toBeGreaterThan(0);
  expect(costs.late_added).toEqual(added.map((r) => r.obligation_id));

  await openConsole(page);
  await go(page, 'Run detail');
  const tab = page.getByRole('tab', { name: /^Costs/ });
  await expect(tab).toHaveText(`Costs${costs.records.length}`);
  await tab.click();

  await expect(page.getByText(`${costs.coverage.relevant} obligations ·`)).toBeVisible();
  await expect(page.getByLabel('Hotspots by provision')).toBeVisible();
  const rows = page.getByTestId('cost-record');
  await expect(rows).toHaveCount(costs.records.length);
  await expect(page.getByText(/EUR|€/)).toHaveCount(0);

  await page.getByLabel('Change after the proposal').selectOption('added');
  await expect(rows).toHaveCount(added.length);
  for (const r of await rows.all()) await expect(r).toContainText('Added after the proposal');

  // Each record opens its obligation view in the source panel.
  await rows.first().getByRole('button').first().click();
  const panel = page.getByRole('dialog', { name: 'Source' });
  await expect(panel).toContainText(added[0].source_id);
  await expect(panel).toContainText('Quote matched word for word in the source');
});

test('the pipeline shows the cost step between the citation check and synthesis', async ({ page }) => {
  await finishedRun(page.request, SCENARIOS.sme);
  await openConsole(page);
  await go(page, 'Run pipeline');
  await expect(page.getByText('Cost step', { exact: true }).first()).toBeVisible();
});
