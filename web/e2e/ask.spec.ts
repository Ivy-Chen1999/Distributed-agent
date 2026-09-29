// Ask WOMM: suggestions, typed questions, citations into Run detail, uncovered questions.
import { SCENARIOS, expect, finishedRun, go, listRuns, openConsole, pipeNode, screenTitle, startFromPicker, test, type RunDetail } from './helpers';
import type { Page } from '@playwright/test';

const bubbles = (page: Page) => page.locator('main [aria-live="polite"] > div');
const lastBot = (page: Page) => bubbles(page).filter({ hasText: /^WOMM · / }).last();

let run: RunDetail;
test.beforeEach(async ({ page }) => {
  run = await finishedRun(page.request, SCENARIOS.sme);
  await openConsole(page);
  await go(page, 'Ask WOMM');
});

test('greeting and the "Answering from" panel describe the run', async ({ page }) => {
  const d = run.dossier!;
  const g = run.grounding!;
  await expect(bubbles(page).first()).toContainText(`The SME impacts run finished in`);
  await expect(bubbles(page).first()).toContainText(`${d.impacts.length} impacts, all ${g.total} evidence quotes found verbatim`);
  const panel = page.getByText('Answering from', { exact: true }).locator('..');
  await expect(panel).toContainText(`${run.run_id.slice(0, 12)} · ${d.impacts.length} impacts · ${g.passed} grounded quotes`);
  await expect(panel).toContainText("If the dossier doesn't cover something, WOMM says so instead of guessing.");
});

test('a suggestion chip asks, shows "Reading the dossier…" and cites impacts and findings', async ({ page }) => {
  const im = run.dossier!.impacts[0];
  const f = im.findings[0];
  await page.getByRole('button', { name: 'Who carries the penalty risk?' }).click();
  await expect(bubbles(page).filter({ hasText: /^Who carries the penalty risk\?$/ })).toHaveCount(1);
  await expect(page.getByText('Reading the dossier…')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Ask', exact: true })).toBeDisabled();
  await expect(page.getByText('Reading the dossier…')).toBeHidden();
  const answer = lastBot(page);
  await expect(answer).toContainText("WOMM · from this run's dossier");
  await expect(answer).toContainText(im.summary);
  await expect(answer.getByRole('button')).toHaveText([im.impact_id, f.finding_id]);

  // The impact chip jumps to the impact in Run detail.
  await answer.getByRole('button', { name: im.impact_id }).click();
  await expect(screenTitle(page)).toHaveText('Run detail');
  await expect(page.getByRole('tab', { name: /^Impacts/ })).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator(`#imp-${im.impact_id} button[aria-expanded]`)).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator(`#imp-${im.impact_id}`)).toBeInViewport();

  // The finding chip opens the impact that contains the finding; the chat is kept per run.
  await go(page, 'Ask WOMM');
  await lastBot(page).getByRole('button', { name: f.finding_id }).click();
  await expect(screenTitle(page)).toHaveText('Run detail');
  await expect(page.locator(`#imp-${im.impact_id} button[aria-expanded]`)).toHaveAttribute('aria-expanded', 'true');
  await expect(page.getByText(f.finding_id, { exact: true })).toBeVisible();
});

test('typed questions go out with Enter and with the Ask button; uncovered questions say so', async ({ page }) => {
  const input = page.getByLabel('Question');
  await input.fill('Which obligations change for SMEs?');
  await input.press('Enter');
  await expect(input).toHaveValue('');
  await expect(bubbles(page).filter({ hasText: /^Which obligations change for SMEs\?$/ })).toHaveCount(1);
  await expect(lastBot(page)).toContainText("from this run's dossier", { timeout: 10_000 });

  await input.fill('What will the weather be in Brussels tomorrow?');
  await page.getByRole('button', { name: 'Ask', exact: true }).click();
  await expect(page.getByText('Reading the dossier…')).toBeVisible();
  await expect(page.getByText('Reading the dossier…')).toBeHidden();
  const answer = lastBot(page);
  await expect(answer).toContainText('WOMM · not covered by this dossier');
  await expect(answer).toContainText('This dossier does not cover that.');
  await expect(answer.getByRole('button')).toHaveCount(0);
  // Blank questions are not sent.
  const before = await bubbles(page).count();
  await input.fill('   ');
  await input.press('Enter');
  await expect(bubbles(page)).toHaveCount(before);
});

test('"Start a new run" opens the picker and the new run goes live in the pipeline', async ({ page }) => {
  const before = (await listRuns(page.request, 1))[0].run_id;
  await startFromPicker(page, SCENARIOS.costs, page.getByRole('button', { name: 'Start a new run' }));
  await expect(screenTitle(page)).toHaveText('Run pipeline');
  await expect(page.locator('header')).toContainText(SCENARIOS.costs);
  const now = (await listRuns(page.request, 1))[0];
  expect(now.run_id).not.toBe(before);
  expect(now.scenario_id).toBe(SCENARIOS.costs);
  await expect(pipeNode(page, 'dossier')).toContainText('Done', { timeout: 30_000 });
  // Ask now answers from the new run.
  await go(page, 'Ask WOMM');
  await expect(page.getByText('Answering from', { exact: true }).locator('..')).toContainText(now.run_id.slice(0, 12));
});
