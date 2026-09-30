// Phone layout (390 px): the sidebar is a drawer behind the header's menu button.
import AxeBuilder from '@axe-core/playwright';
import { SCENARIOS, SCREENS, expect, finishedRun, go, openConsole, screenTitle, test } from './helpers';

test.use({ viewport: { width: 390, height: 844 } });

test('the navigation drawer opens, focuses, navigates and closes', async ({ page, errors }) => {
  await finishedRun(page.request, SCENARIOS.sme);
  await openConsole(page);
  const nav = page.locator('aside#womm-nav');
  const menu = page.getByRole('button', { name: 'Open navigation' });
  await expect(nav).toBeHidden();
  await expect(menu).toHaveAttribute('aria-expanded', 'false');

  await menu.click();
  await expect(nav).toBeVisible();
  await expect(menu).toHaveAttribute('aria-expanded', 'true');
  await expect(nav.locator('nav button[aria-current="page"]')).toBeFocused();

  // Choosing a screen navigates and closes the drawer.
  await nav.locator('nav button', { hasText: 'Topology' }).click();
  await expect(screenTitle(page)).toHaveText('Topology');
  await expect(nav).toBeHidden();

  // Escape and the backdrop close it too.
  await menu.click();
  await page.keyboard.press('Escape');
  await expect(nav).toBeHidden();
  await expect(menu).toBeFocused();
  await menu.click();
  await page.mouse.click(370, 400);
  await expect(nav).toBeHidden();
  errors.expectClean();
});

test('widening the window turns the drawer back into a sidebar', async ({ page }) => {
  await finishedRun(page.request, SCENARIOS.sme);
  await openConsole(page);
  await page.getByRole('button', { name: 'Open navigation' }).click();
  await page.setViewportSize({ width: 1280, height: 900 });
  await expect(page.getByRole('button', { name: 'Open navigation' })).toHaveCount(0);
  await expect(page.locator('aside#womm-nav')).toBeVisible();
  await expect(page.locator('aside#womm-nav')).toHaveCSS('position', 'sticky');
});

test('topology and pipeline run top to bottom', async ({ page }) => {
  await finishedRun(page.request, SCENARIOS.sme);
  await openConsole(page);
  await go(page, 'Topology');
  const diff = await page.locator('[data-topo="diff"]').boundingBox();
  const dossier = await page.locator('[data-topo="dossier"]').boundingBox();
  expect(dossier!.y).toBeGreaterThan(diff!.y + 400);
  await go(page, 'Run pipeline');
  const p1 = await page.locator('[data-node="diff"]').boundingBox();
  const p2 = await page.locator('[data-node="dossier"]').boundingBox();
  expect(p2!.y).toBeGreaterThan(p1!.y);
  expect(Math.abs(p2!.x - p1!.x)).toBeLessThan(40);
});

test('no WCAG 2.1 AA violations at phone width, drawer open or closed', async ({ page }) => {
  await finishedRun(page.request, SCENARIOS.sme);
  await openConsole(page);
  const report: string[] = [];
  const scan = async (where: string) => {
    const { violations } = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
    for (const v of violations) report.push(`${where}: ${v.id} ×${v.nodes.length} — ${v.nodes.slice(0, 2).map((n) => n.target.join(' ')).join(' | ')}`);
  };
  for (const screen of SCREENS) {
    await go(page, screen);
    await page.waitForTimeout(450);
    await scan(screen);
  }
  await page.getByRole('button', { name: 'Open navigation' }).click();
  await page.waitForTimeout(300);
  await scan('drawer');
  expect(report, report.join('\n')).toEqual([]);
});
