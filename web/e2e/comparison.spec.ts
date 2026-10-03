// Run detail: the proposal / final-text provision comparison of the demo diff scenario (R36 page 2).
import AxeBuilder from '@axe-core/playwright';
import type { Locator, Page } from '@playwright/test';
import { AUTH, SCENARIOS, expect, finishedRun, go, openConsole, test } from './helpers';
import type { ScenarioSources } from '../src/types';

const section = (page: Page) => page.getByTestId('provision-comparison');
const change = (page: Page, key: string) => section(page).locator(`[data-provision="${key}"]`);
const WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];

async function openDemoDetail(page: Page, theme?: 'light' | 'dark'): Promise<void> {
  await finishedRun(page.request, SCENARIOS.demo);
  await openConsole(page, { theme });
  await go(page, 'Run detail');
  await expect(section(page)).toBeVisible();
}

async function noPageScroll(page: Page): Promise<void> {
  const [scroll, client] = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
  expect(scroll).toBeLessThanOrEqual(client);
}

/** The two columns sit side by side at the same height. */
async function sideBySide(card: Locator): Promise<void> {
  const before = await card.locator('[data-side="before"]').boundingBox();
  const after = await card.locator('[data-side="after"]').boundingBox();
  expect(Math.abs(before!.y - after!.y)).toBeLessThan(2);
  expect(after!.x).toBeGreaterThan(before!.x + before!.width - 1);
}

for (const width of [1280, 1024]) {
  test(`demo diff at ${width} px: every change, side by side, with marked deletions and additions`, async ({ page, errors }) => {
    await page.setViewportSize({ width, height: 900 });
    const res = await page.request.get(`/scenarios/${SCENARIOS.demo}/sources`, { headers: AUTH });
    const data = (await res.json()) as ScenarioSources;
    await openDemoDetail(page);
    await expect(section(page).getByRole('heading', { name: 'Provision comparison' })).toBeVisible();
    await expect(section(page)).toContainText('Proposal COM(2021) 206 → final text Regulation (EU) 2024/1689, word by word');

    // One card per change, labelled across the renumbering; all start collapsed.
    expect(data.changes.map((c) => `${c.before!.article}→${c.after!.article}`)).toEqual(['16→16', '55→62', '71→99']);
    const toggles = section(page).locator('[data-provision] > button[aria-expanded]');
    await expect(toggles).toHaveCount(3);
    for (const c of data.changes) {
      const toggle = change(page, c.provision_key).locator('> button');
      await expect(toggle).toContainText(`Art ${c.before!.article} → Art ${c.after!.article}`);
      await expect(toggle).toContainText('Modified');
      await expect(toggle).toContainText(c.provision_key);
      await expect(toggle).toContainText(/−\d+ \/ \+\d+ words/);
      await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    }

    // Penalties, Art 71 → Art 99: open it and check both columns.
    const pen = change(page, 'ai_act/penalties/penalties');
    await pen.locator('> button').click();
    await expect(pen.locator('> button')).toHaveAttribute('aria-expanded', 'true');
    await expect(pen.locator('[data-side="before"]')).toContainText('Proposal · COM(2021) 206 · Art 71');
    await expect(pen.locator('[data-side="after"]')).toContainText('Final text · Regulation (EU) 2024/1689 · Art 99');
    await sideBySide(pen);

    // Deletions only on the proposal side, struck through; additions only on the final side, underlined.
    const del = pen.locator('[data-side="before"] del');
    const ins = pen.locator('[data-side="after"] ins');
    expect(await del.count()).toBeGreaterThan(0);
    expect(await ins.count()).toBeGreaterThan(0);
    await expect(pen.locator('[data-side="before"] ins')).toHaveCount(0);
    await expect(pen.locator('[data-side="after"] del')).toHaveCount(0);
    await expect(del.first()).toHaveCSS('text-decoration-line', 'line-through');
    await expect(ins.first()).toHaveCSS('text-decoration-line', 'underline');
    await expect(del.first()).toContainText('[removed: ');
    await expect(ins.first()).toContainText('[added: ');
    // A known wording change: "In compliance with" became "In accordance with".
    await expect(del.first()).toContainText('compliance');
    await expect(ins.first()).toContainText('accordance');

    // Each column is exactly its source text once the screen-reader markers are taken out.
    const text = (col: string) =>
      pen.locator(`[data-side="${col}"] > div`).nth(1).evaluate((el) => {
        const c = el.cloneNode(true) as HTMLElement;
        c.querySelectorAll('.sr-only').forEach((s) => s.remove());
        return c.textContent;
      });
    const src = (id: string) => data.sources.find((s) => s.source_id === id)!.text;
    const penChange = data.changes.find((c) => c.provision_key === 'ai_act/penalties/penalties')!;
    expect(await text('before')).toBe(src(penChange.before!.source_id));
    expect(await text('after')).toBe(src(penChange.after!.source_id));

    const first = change(page, data.changes[0].provision_key);
    await first.locator('> button').click();
    await sideBySide(first);
    await noPageScroll(page);
    await page.screenshot({ path: `test-results/comparison-${width}.png`, fullPage: true });

    // Collapsing hides the columns again.
    await pen.locator('> button').click();
    await expect(pen.locator('[data-side]')).toHaveCount(0);
    errors.expectClean();
  });
}

for (const theme of ['light', 'dark'] as const) {
  test(`${theme} theme: the comparison has no WCAG 2.1 AA violations`, async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await openDemoDetail(page, theme);
    await change(page, 'ai_act/penalties/penalties').locator('> button').click();
    await page.waitForTimeout(450);
    const { violations } = await new AxeBuilder({ page }).withTags(WCAG).include('[data-testid="provision-comparison"]').analyze();
    expect(violations.map((v) => `${v.id} ×${v.nodes.length} — ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
  });
}

test('an evaluation scenario (no proposal → final-text diff) has no comparison', async ({ page }) => {
  await finishedRun(page.request, SCENARIOS.sme);
  await openConsole(page);
  await go(page, 'Run detail');
  await expect(page.getByRole('tab', { name: /^Impacts/ })).toBeVisible();
  await expect(section(page)).toHaveCount(0);
});

test('the provision texts failing to load shows an error with a working retry', async ({ page }) => {
  let fail = true;
  await page.route(`**/scenarios/${SCENARIOS.demo}/sources`, (route) =>
    fail ? route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'sources unavailable' }) }) : route.continue(),
  );
  await openDemoDetail(page);
  await expect(section(page).getByRole('alert')).toContainText('Could not load the provision texts');
  await expect(section(page).getByRole('alert')).toContainText('sources unavailable');
  fail = false;
  await section(page).getByRole('button', { name: 'Retry' }).click();
  await expect(section(page).locator('[data-provision]')).toHaveCount(3);
});
