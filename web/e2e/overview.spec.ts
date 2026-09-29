// Overview: KPIs, latency bars, flow pills, "Needs your review", recent runs.
import { SCENARIOS, SCENARIO_NAMES, expect, finishedRun, go, listRuns, openConsole, screenTitle, shortRunId, test } from './helpers';
import type { Page } from '@playwright/test';

const kpi = (page: Page, label: string) => page.locator('main').getByText(label, { exact: true }).first().locator('..');
const review = (page: Page, title: RegExp) => page.getByText('Needs your review', { exact: true }).locator('..').getByRole('button').filter({ hasText: title });

test('KPIs, latency bars and flow pills of the latest run', async ({ page }) => {
  const run = await finishedRun(page.request, SCENARIOS.costs);
  await openConsole(page);
  await expect(page.getByText(/^Latest run · \d+ \w{3} \d{4}, \d\d:\d\d$/)).toBeVisible();
  await expect(page.locator('main h1')).toHaveText('Who bears which compliance costs and administrative burdens?');
  await expect(page.locator('main p').first()).toContainText('11 provisions changed: compliance with requirements, risk management');

  await expect(kpi(page, 'Status')).toContainText('Succeeded');
  await expect(kpi(page, 'Status')).toContainText('All experts returned findings');
  await expect(kpi(page, 'Total time')).toContainText(/\d+\.\ds/);
  await expect(kpi(page, 'Tokens')).toContainText(/\d(\.\d)?k/);
  const cost = run.usage!.reduce((a, u) => a + (u.cost_usd ?? 0), 0);
  await expect(kpi(page, 'Cost')).toContainText('$' + cost.toFixed(2));
  await expect(kpi(page, 'Cost')).toContainText('Model: claude-sonnet-5');
  const g = run.grounding!;
  await expect(kpi(page, 'Grounding')).toContainText(`${Math.round((g.passed / g.total) * 100)}%`);
  await expect(kpi(page, 'Grounding')).toContainText(`${g.passed}/${g.total} quotes verified`);

  const latency = page.getByText('Agent latency', { exact: true }).locator('xpath=../..');
  for (const pill of ['Regulatory diff', 'Impact Planner', 'Router · Jev', 'Legal', 'Fiscal', 'Stakeholder', 'Synthesis', 'Impact Dossier']) {
    await expect(latency.getByText(pill, { exact: true }).first()).toBeVisible();
  }
  // One bar per LLM node, each with its latency and cost.
  for (const name of ['Impact Planner', 'Legal', 'Fiscal', 'Stakeholder', 'Synthesis']) {
    const row = latency.locator('div[style*="grid-template-columns: 110px"]').filter({ hasText: name });
    await expect(row).toHaveCount(1);
    await expect(row).toContainText(/\d+\.\ds · \$\d+\.\d\d/);
  }
  // Experts run in parallel: the stakeholder (slowest scripted expert) has the widest expert bar.
  const width = async (name: string) =>
    latency.locator('div[style*="grid-template-columns: 110px"]').filter({ hasText: name }).locator('div > div').evaluate((el) => parseFloat((el as HTMLElement).style.width));
  expect(await width('Stakeholder')).toBeGreaterThan(await width('Legal'));
});

test('"Needs your review" rows open the matching Run detail tab', async ({ page }) => {
  const run = await finishedRun(page.request, SCENARIOS.costs);
  await openConsole(page);
  const d = run.dossier!;
  await expect(review(page, /Disagreement/)).toContainText(String(d.disagreements.length));
  await expect(review(page, /Open questions/)).toContainText(String(d.open_questions.length));
  await expect(review(page, /Open questions/)).toContainText('1 from unresolved evidence');
  await expect(review(page, /Failed experts/)).toContainText('None in this run');
  await expect(review(page, /Unresolved evidence/)).toContainText('Quotes the citation check could not find in the source');

  const cases: [RegExp, string][] = [
    [/Disagreement/, 'Disagreements'],
    [/Open questions/, 'Open questions'],
    [/Failed experts/, 'Open questions'],
    [/Unresolved evidence/, 'Open questions'],
  ];
  for (const [row, tab] of cases) {
    await go(page, 'Overview');
    await review(page, row).click();
    await expect(screenTitle(page)).toHaveText('Run detail');
    await expect(page.getByRole('tab', { name: new RegExp(`^${tab}`) })).toHaveAttribute('aria-selected', 'true');
  }
  await expect(page.getByText('Failed experts', { exact: true })).toBeVisible();
});

test('recent runs lists every run and each row opens its detail', async ({ page }) => {
  // Make sure every scenario and a degraded run are in the table.
  await finishedRun(page.request, SCENARIOS.demo);
  await finishedRun(page.request, SCENARIOS.sme);
  const runs = await listRuns(page.request, 20);
  await openConsole(page);
  const table = page.getByText('Recent runs', { exact: true }).locator('..');
  const rows = table.locator('button[title^="run_"]');
  await expect(rows).toHaveCount(runs.length);
  for (const [i, r] of runs.entries()) {
    const row = rows.nth(i);
    await expect(row).toHaveAttribute('title', r.run_id);
    await expect(row).toContainText(shortRunId(r.run_id));
    await expect(row).toContainText(SCENARIO_NAMES[r.scenario_id]);
    await expect(row).toContainText(r.system_version);
    await expect(row).toContainText({ succeeded: 'Succeeded', degraded: 'Degraded' }[r.status] ?? r.status);
  }
  // The newest run is highlighted.
  await expect(rows.first()).toHaveCSS('background-color', /rgb\(237, 242, 242\)/);

  for (const r of runs) {
    await go(page, 'Overview');
    await table.locator(`button[title="${r.run_id}"]`).click();
    await expect(screenTitle(page)).toHaveText('Run detail');
    await expect(page.locator('main').getByTitle(r.run_id)).toContainText(shortRunId(r.run_id));
    await expect(page.locator('header')).toContainText(r.scenario_id);
  }
});

test('"Open impact dossier" goes to Run detail', async ({ page }) => {
  await finishedRun(page.request, SCENARIOS.sme);
  await openConsole(page);
  await page.getByRole('button', { name: 'Open impact dossier' }).click();
  await expect(screenTitle(page)).toHaveText('Run detail');
  await expect(page.getByRole('tab', { name: /^Impacts/ })).toHaveAttribute('aria-selected', 'true');
});
