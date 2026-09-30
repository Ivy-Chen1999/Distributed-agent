// Accessibility: an axe-core WCAG 2.1 AA scan of every screen in both themes.
import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';
import { SCENARIOS, SCREENS, expect, finishedRun, go, openConsole, test } from './helpers';

const WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];

async function scan(page: Page, where: string, report: string[], include?: string): Promise<void> {
  let axe = new AxeBuilder({ page }).withTags(WCAG);
  if (include) axe = axe.include(include);
  const { violations } = await axe.analyze();
  for (const v of violations) report.push(`${where}: ${v.id} (${v.impact}) ×${v.nodes.length} — ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`);
}

for (const theme of ['light', 'dark'] as const) {
  test(`${theme} theme: no WCAG 2.1 AA violations on any screen`, async ({ page }) => {
    await finishedRun(page.request, SCENARIOS.sme);
    await openConsole(page, { theme });
    const report: string[] = [];
    for (const screen of SCREENS) {
      await go(page, screen);
      await page.waitForTimeout(450); // entry animations change opacity, which skews contrast
      await scan(page, screen, report);
    }
    await go(page, 'Run detail');
    await page.locator('main button[aria-expanded]').first().click();
    await page.getByRole('button', { name: /View in source/ }).first().click();
    await expect(page.getByRole('dialog', { name: 'Source' })).toBeVisible();
    await page.waitForTimeout(350);
    await scan(page, 'Source panel', report, '[role="dialog"]');
    expect(report, report.join('\n')).toEqual([]);
  });
}

test('the token prompt has no WCAG 2.1 AA violations', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('dialog').getByLabel('API token')).toBeVisible();
  await page.waitForTimeout(350);
  const report: string[] = [];
  await scan(page, 'Token prompt', report);
  expect(report, report.join('\n')).toEqual([]);
});

test('keyboard focus shows an accent outline, inputs included', async ({ page }) => {
  await finishedRun(page.request, SCENARIOS.sme);
  await openConsole(page);
  const button = page.locator('aside nav button').first();
  await button.focus();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Shift+Tab');
  await expect(button).toHaveCSS('outline-style', 'solid');
  await expect(button).toHaveCSS('outline-color', 'rgb(0, 115, 122)');
  await go(page, 'Ask WOMM');
  const input = page.locator('main input').first();
  await input.focus();
  await expect(input).toHaveCSS('outline-style', 'solid');
});
