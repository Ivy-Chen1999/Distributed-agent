// Evolution page (R36 page 4) against the scripted lineage archived by
// `python -m womm.api.e2e seed-evolution`: lineage tree, new-expert highlight, decisions shown
// verbatim, the R37 warning, prompt diffs, and the empty, error and accessibility checks.
import AxeBuilder from '@axe-core/playwright';
import type { APIRequestContext, Page } from '@playwright/test';
import { AUTH, expect, go, openConsole, test } from './helpers';
import type { Lineage, LineageNode } from '../src/types';

async function lineage(request: APIRequestContext): Promise<Lineage> {
  const res = await request.get('/evolution/lineage', { headers: AUTH });
  expect(res.ok()).toBeTruthy();
  return (await res.json()) as Lineage;
}

const byOrigin = (l: Lineage, origin: string, decision: string | null = null): LineageNode =>
  l.nodes.find((n) => n.origin === origin && n.decision === decision)!;

const treeRow = (page: Page, id: string) => page.getByRole('list', { name: 'Lineage' }).locator(`button[data-version="${id}"]`);

async function openEvolution(page: Page): Promise<void> {
  await openConsole(page);
  await go(page, 'Evolution');
  await expect(page.getByRole('list', { name: 'Lineage' })).toBeVisible();
}

test('lineage tree: seed, prompt child, topology grandchild and a rejected dev candidate', async ({ page, errors }) => {
  const l = await lineage(page.request);
  await openEvolution(page);
  await expect(page.getByRole('list', { name: 'Lineage' }).getByRole('button')).toHaveCount(4);
  const topo = byOrigin(l, 'topology', 'promoted');
  await expect(treeRow(page, topo.version_id)).toContainText('New expert');
  await expect(treeRow(page, topo.version_id)).toContainText('Promoted');
  await expect(treeRow(page, topo.version_id)).toContainText('+1 api twin');
  const seed = l.nodes.find((n) => n.origin === 'seed')!;
  await expect(treeRow(page, seed.version_id)).toContainText('Seed');
  errors.expectClean();
});

test('a topology candidate: new expert highlight and the weak-mode decision verbatim', async ({ page }) => {
  const topo = byOrigin(await lineage(page.request), 'topology', 'promoted');
  await openEvolution(page);
  await treeRow(page, topo.version_id).click();
  await expect(page.getByRole('heading', { name: topo.version_id })).toBeVisible();
  const expert = page.getByTestId('new-expert');
  await expect(expert).toContainText('New expert: workforce');
  await expect(expert).toContainText('effects on workers, skills, staffing and employment');
  await expect(expert).toContainText('missed impact · social environmental · no expert owns it');
  await expect(page.getByTestId('decision-label')).toHaveText('promoted (weak threshold: directional)');
  await expect(page.getByTestId('holdout')).toContainText('Weak threshold (directional)');
  await expect(page.getByTestId('r37')).toContainText('monitoring only: never gates a promotion');
  await expect(page.getByTestId('config-diff')).toContainText('Expert added: workforce');
});

test('a rejected dev candidate shows its reasons and the R37 regression without changing the badge', async ({ page }) => {
  const rejected = byOrigin(await lineage(page.request), 'gepa', 'rejected');
  await openEvolution(page);
  await treeRow(page, rejected.version_id).click();
  await expect(page.getByTestId('decision-label')).toHaveText('rejected (dev-only, not deployable; weak threshold: directional)');
  await expect(page.getByTestId('holdout')).toContainText('Reasons: grounding regression');
  await expect(page.getByTestId('r37-warning')).toContainText('the decision is unchanged');
  await expect(treeRow(page, rejected.version_id)).toContainText('Rejected');
  await expect(treeRow(page, rejected.version_id)).toContainText('R37 regression');
});

test('a candidate without a decision: train/val only, not submitted, prompt diffs on demand', async ({ page }) => {
  const prompt = byOrigin(await lineage(page.request), 'gepa', null);
  await openEvolution(page);
  await treeRow(page, prompt.version_id).click();
  await expect(page.getByTestId('holdout')).toContainText('not submitted to holdout');
  await expect(page.getByTestId('metrics')).toContainText('Coverage');
  await expect(page.getByTestId('r37')).toContainText('not run');
  await page.getByRole('button', { name: 'Show prompt diffs' }).click();
  const diff = page.locator('[data-prompt-diff="expert:fiscal"]');
  await expect(diff).toContainText('+Quantify every cost you name.');
  await expect(diff.locator('[data-line="add"]').first()).toBeVisible();
});

test('empty and error states (mocked lineage)', async ({ page }) => {
  await page.route('**/evolution/lineage', (route) => route.fulfill({ json: { nodes: [], publish_summary: false } }));
  await openConsole(page);
  await go(page, 'Evolution');
  await expect(page.getByText('No candidates yet')).toBeVisible();
  await page.unroute('**/evolution/lineage');
  await page.route('**/evolution/lineage', (route) => route.fulfill({ status: 500, json: { detail: 'database unavailable' } }));
  await go(page, 'Overview');
  await go(page, 'Evolution');
  await expect(page.getByRole('alert')).toContainText('database unavailable');
});

for (const theme of ['light', 'dark'] as const) {
  test(`${theme} theme: the evolution page has no WCAG 2.1 AA violations`, async ({ page }) => {
    const topo = byOrigin(await lineage(page.request), 'topology', 'promoted');
    await openConsole(page, { theme });
    await go(page, 'Evolution');
    await treeRow(page, topo.version_id).click();
    await expect(page.getByTestId('new-expert')).toBeVisible();
    await page.waitForTimeout(450);
    const { violations } = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
    expect(violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).slice(0, 3).join(' | ')}`)).toEqual([]);
  });
}
