// Act screen against the real API and the committed corpus: the Omnibus comparison opens first,
// the map shows where application dates moved, and a tile opens the article in the diff view.
import { expect, go, openConsole, test } from './helpers';

test('the Omnibus comparison opens first and lists the amended articles', async ({ page }) => {
  await openConsole(page);
  await go(page, 'Act');
  await expect(page.getByLabel('Comparison', { exact: true })).toHaveValue('reg2024_1689->reg2024_1689_c20260727');
  await expect(page.getByTestId('act-stat')).toContainText(/^45 of 133 articles and annexes changed/);
  await expect(page.locator('article[data-provision="ai_act/art/27"]')).toBeVisible();
  await expect(page.locator('article[data-provision="ai_act/art/26"]')).toHaveCount(0);
});

test('the map shows moved dates, and a tile opens that article side by side', async ({ page }) => {
  await openConsole(page);
  await go(page, 'Act');
  await page.getByRole('button', { name: 'Side by side' }).click();
  await page.getByRole('button', { name: 'Map' }).click();
  await page.getByLabel('Measure', { exact: true }).selectOption('dateMoved');
  const tile = page.getByRole('button', { name: /^Article 26, Obligations of deployers of high-risk AI systems: 21 · / });
  await expect(tile).toBeVisible();
  await tile.click();
  await expect(page.getByRole('button', { name: 'Side by side' })).toHaveAttribute('aria-pressed', 'true');
  const card = page.locator('article[data-provision="ai_act/art/26"]');
  await expect(card).toBeVisible();
  await expect(card).toContainText('21 with a moved date');
});

test('switching to proposal → adopted text shows renumbered articles', async ({ page }) => {
  await openConsole(page);
  await go(page, 'Act');
  await page.getByLabel('Comparison', { exact: true }).selectOption('com2021_206->reg2024_1689');
  await expect(page.getByTestId('act-stat')).toContainText(/of 134 articles and annexes changed/);
  await expect(page.locator('article[data-provision="ai_act/art/26"]')).toContainText('was Article 29');
});
