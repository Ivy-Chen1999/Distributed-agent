// Header actions and error handling (mocked failures via page.route).
import { SCENARIOS, expect, finishedRun, go, nav, openConsole, runButton, screenTitle, startFromPicker, svButton, test, toast } from './helpers';

test.describe('header', () => {
  test('copy system version shows a toast and fills the clipboard', async ({ page, context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    const run = await finishedRun(page.request, SCENARIOS.sme);
    await openConsole(page);
    await expect(svButton(page)).toHaveText(run.system_version);
    await svButton(page).click();
    await expect(toast(page)).toHaveText(`Copied ${run.system_version}`);
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(run.system_version);
    await expect(toast(page)).toBeHidden({ timeout: 5_000 });
  });

  test('theme toggle switches and persists', async ({ page }) => {
    await openConsole(page);
    const root = page.locator('[data-screen-label="WOMM Console"]');
    await expect(root).toHaveAttribute('data-theme', 'light');
    await page.getByTitle('Switch theme').click();
    await expect(root).toHaveAttribute('data-theme', 'dark');
    expect(await page.evaluate(() => localStorage.getItem('womm.theme'))).toBe('dark');
    await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(10, 16, 17)');
    await page.reload();
    await expect(root).toHaveAttribute('data-theme', 'dark');
    await page.getByTitle('Switch theme').click();
    await expect(root).toHaveAttribute('data-theme', 'light');
    await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(243, 246, 246)');
  });

  test('Run button: Re-run when finished, Running… and disabled while a run is live', async ({ page }) => {
    await finishedRun(page.request, SCENARIOS.sme);
    await openConsole(page);
    await expect(runButton(page)).toHaveText('Re-run');
    await expect(runButton(page)).toBeEnabled();
    await go(page, 'Run detail');
    await startFromPicker(page, SCENARIOS.sme);
    // Started from Run detail: the console stays on the screen it was on.
    await expect(screenTitle(page)).toHaveText('Run detail');
    await expect(runButton(page)).toHaveText('Running…');
    await expect(runButton(page)).toBeDisabled();
    await expect(page.getByText('Dossier is assembling')).toBeVisible();
    await expect(nav(page, 'Run pipeline')).toContainText('live');
    await expect(runButton(page)).toHaveText('Re-run', { timeout: 30_000 });
    await expect(page.getByRole('tab', { name: /^Impacts/ })).toBeVisible();
  });
});

test.describe('errors', () => {
  test('POST /runs 503 and 429 show their messages as a toast', async ({ page }) => {
    await openConsole(page);
    await page.route('**/runs', (route) =>
      route.request().method() === 'POST' ? route.fulfill({ status: 503, json: { detail: 'backend unavailable: LLMError: [auth] Not logged in' } }) : route.fallback(),
    );
    await runButton(page).click();
    let dialog = page.getByRole('dialog', { name: 'Start a run' });
    await dialog.getByRole('button', { name: /^Run$/ }).click();
    await expect(toast(page)).toHaveText('Backend unavailable: LLMError: [auth] Not logged in');
    // The picker stays open so the user can retry.
    await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: 'Close' }).click();

    await page.unroute('**/runs');
    await page.route('**/runs', (route) =>
      route.request().method() === 'POST' ? route.fulfill({ status: 429, json: { detail: '20 runs already queued or running' } }) : route.fallback(),
    );
    await runButton(page).click();
    dialog = page.getByRole('dialog', { name: 'Start a run' });
    await dialog.getByRole('button', { name: /^Run$/ }).click();
    await expect(toast(page)).toHaveText('Run queue is full. Try again when a run finishes.');
    await expect(runButton(page)).toBeEnabled();
  });

  test('GET /runs 500 shows an error card on the overview and Retry recovers', async ({ page }) => {
    await finishedRun(page.request, SCENARIOS.sme);
    let fail = true;
    await page.route(/\/runs\?limit=\d+$/, (route) => (fail ? route.fulfill({ status: 500, json: { detail: 'database is down' } }) : route.fallback()));
    await openConsole(page);
    const alert = page.getByRole('alert').filter({ hasText: 'Could not load runs' });
    await expect(alert).toContainText('database is down');
    await expect(page.getByText('No runs yet', { exact: true })).toHaveCount(0);
    fail = false;
    await alert.getByRole('button', { name: 'Retry' }).click();
    await expect(alert).toBeHidden();
    await expect(page.getByText('Recent runs', { exact: true })).toBeVisible();
  });

  test('ask 502 shows the error in the chat', async ({ page }) => {
    await finishedRun(page.request, SCENARIOS.sme);
    await page.route('**/ask', (route) => route.fulfill({ status: 502, json: { detail: 'could not answer: [timeout] synthesis model timed out' } }));
    await openConsole(page);
    await go(page, 'Ask WOMM');
    await page.getByRole('button', { name: 'Show the disagreement' }).click();
    const err = page.locator('main [aria-live="polite"] > div').last();
    // The server's reason is shown (regression: a 502 from the API was reported as "Cannot reach the WOMM API").
    await expect(err).toContainText('Could not answer: [timeout] synthesis model timed out');
    await expect(err.locator('div').first()).toHaveCSS('border-top-color', 'rgb(229, 72, 77)');
    // A 502 without an API body (proxy in front of a dead API) still says the API is unreachable.
    await page.unroute('**/ask');
    await page.route('**/ask', (route) => route.fulfill({ status: 502, body: '' }));
    await page.getByRole('button', { name: 'Which experts ran?' }).click();
    await expect(page.locator('main [aria-live="polite"] > div').last()).toContainText('Could not answer: Cannot reach the WOMM API (502). Is it running?');
  });

  test('a 401 mid-session brings the token prompt back', async ({ page }) => {
    await finishedRun(page.request, SCENARIOS.sme);
    await openConsole(page);
    await page.route('**/runs', (route) => (route.request().method() === 'POST' ? route.fulfill({ status: 401, json: { detail: 'invalid or missing bearer token' } }) : route.fallback()));
    await runButton(page).click();
    await page.getByRole('dialog', { name: 'Start a run' }).getByRole('button', { name: /^Run$/ }).click();
    const prompt = page.getByRole('dialog', { name: 'Connect to the WOMM API' });
    await expect(prompt).toContainText('Token rejected');
    await expect(page.getByRole('dialog', { name: 'Start a run' })).toHaveCount(0);
    expect(await page.evaluate(() => localStorage.getItem('womm.token'))).toBeNull();
    await page.unroute('**/runs');
    await prompt.getByLabel('API token').fill(process.env.WOMM_E2E_TOKEN!);
    await prompt.getByRole('button', { name: 'Connect' }).click();
    await expect(prompt).toBeHidden();
    await expect(runButton(page)).toHaveText('Re-run');
  });
});
