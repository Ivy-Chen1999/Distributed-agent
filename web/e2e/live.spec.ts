// Live smoke test against a real WOMM server (real LLM backends; spends model budget).
// Skipped unless WOMM_E2E_LIVE=1, WOMM_E2E_LIVE_URL and WOMM_E2E_LIVE_TOKEN are set:
//   WOMM_E2E_LIVE=1 WOMM_E2E_LIVE_URL=https://… WOMM_E2E_LIVE_TOKEN=… npm run e2e
import { expect, test } from '@playwright/test';

const URL = process.env.WOMM_E2E_LIVE_URL;
const TOKEN = process.env.WOMM_E2E_LIVE_TOKEN;
const LIVE = process.env.WOMM_E2E_LIVE === '1' && !!URL && !!TOKEN;
const SCENARIO = 'eval_sme_impacts';

test.skip(!LIVE, 'set WOMM_E2E_LIVE=1, WOMM_E2E_LIVE_URL and WOMM_E2E_LIVE_TOKEN to run against a real server');
test.use({ baseURL: URL });

test('one real eval_sme_impacts run end to end', async ({ page }) => {
  test.setTimeout(12 * 60_000);
  const auth = { Authorization: `Bearer ${TOKEN}` };
  await page.addInitScript((t) => localStorage.setItem('womm.token', t), TOKEN!);
  await page.goto('/');
  await expect(page.locator('header')).toBeVisible();
  await expect(page.getByRole('dialog', { name: 'Connect to the WOMM API' })).toHaveCount(0);

  // Start through the picker.
  await page.locator('header button', { hasText: /^(Run|Re-run)$/ }).click();
  const dialog = page.getByRole('dialog', { name: 'Start a run' });
  await dialog.getByRole('radio', { name: new RegExp(SCENARIO) }).click();
  const accepted = page.waitForResponse((r) => r.request().method() === 'POST' && r.url().endsWith('/runs'));
  await dialog.getByRole('button', { name: /^Run$/ }).click();
  const res = await accepted;
  expect(res.status()).toBe(202);
  const { run_id: runId } = (await res.json()) as { run_id: string };
  await expect(page.locator('[data-node="planner"]')).toContainText(/Running|Done/, { timeout: 120_000 });

  // Wait up to 10 minutes for the run to finish.
  await expect
    .poll(async () => {
      // A transient network error (e.g. a keep-alive connection closed by the server) is not a
      // run failure: report it and keep polling.
      try {
        const r = await page.request.get(`/runs/${runId}`, { headers: auth });
        return ((await r.json()) as { status: string }).status;
      } catch (err) {
        return `network error: ${(err as Error).message}`;
      }
    }, {
      timeout: 10 * 60_000,
      intervals: [5_000],
    })
    .toMatch(/^(succeeded|degraded)$/);
  const run = (await (await page.request.get(`/runs/${runId}`, { headers: auth })).json()) as {
    dossier: { impacts: { impact_id: string }[] };
    grounding: { passed: number; total: number };
  };
  expect(run.dossier.impacts.length).toBeGreaterThan(0);
  expect(run.grounding.total).toBeGreaterThan(0);

  // Dossier on screen.
  await page.locator('aside nav button', { hasText: 'Run detail' }).click();
  await expect(page.getByRole('tab', { name: new RegExp(`^Impacts\\s*${run.dossier.impacts.length}$`) })).toBeVisible({ timeout: 30_000 });

  // One source quote.
  await page.locator('main button[aria-expanded]').first().click();
  await page.getByRole('button', { name: /View in source/ }).first().click();
  const source = page.getByRole('dialog', { name: 'Source' });
  await expect(source).toContainText('Quote matched word for word in the source');
  await expect(source.locator('mark')).toHaveCount(1);
  await page.keyboard.press('Escape');

  // One question.
  await page.locator('aside nav button', { hasText: 'Ask WOMM' }).click();
  await page.getByLabel('Question').fill('Who carries the penalty risk?');
  await page.getByLabel('Question').press('Enter');
  await expect(page.getByText('Reading the dossier…')).toBeVisible();
  await expect(page.getByText('Reading the dossier…')).toBeHidden({ timeout: 180_000 });
  const answer = page.locator('main [aria-live="polite"] > div').last();
  await expect(answer).toContainText(/WOMM · (from this run's dossier|not covered by this dossier)/);
  await expect(answer).not.toContainText('Could not answer');
});
