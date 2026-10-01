// Run detail: counts, the five tabs, provenance, the source panel, the event log, the demo diff.
import { SCENARIOS, expect, finishedRun, getEvents, go, openConsole, screenTitle, test, type RunDetail } from './helpers';
import type { Page } from '@playwright/test';

const tab = (page: Page, name: string) => page.getByRole('tab', { name: new RegExp(`^${name}`) });
const impactCard = (page: Page, id: string) => page.locator(`#imp-${id}`);
const squash = (s: string) => s.replace(/\s+/g, ' ').trim();
const AREA: Record<string, string> = {
  'ai_act/innovation/regulatory_sandboxes': 'Regulatory sandboxes',
  'ai_act/innovation/sandbox_personal_data': 'Sandbox personal data',
  'ai_act/innovation/sme_measures': 'SME measures',
  'ai_act/penalties/penalties': 'Penalties',
  'ai_act/high_risk/provider_obligations': 'Provider obligations',
};

async function openDetail(page: Page, scenario: string): Promise<RunDetail> {
  const run = await finishedRun(page.request, scenario);
  await openConsole(page);
  await go(page, 'Run detail');
  await expect(tab(page, 'Impacts')).toBeVisible();
  return run;
}

test('counts and tab badges match the dossier', async ({ page }) => {
  const run = await openDetail(page, SCENARIOS.sme);
  const d = run.dossier!;
  const header = page.getByText('Impact dossier', { exact: true }).locator('xpath=../..');
  await expect(header).toContainText('SME impacts · AI Act proposal');
  await expect(header).toContainText(run.system_version);
  await expect(header).toContainText('git ');
  for (const [n, label] of [
    [d.impacts.length, 'impacts'],
    [d.open_questions.length, 'open questions'],
    [d.disagreements.length, d.disagreements.length === 1 ? 'disagreement' : 'disagreements'],
    [0, 'failed experts'],
  ] as const) {
    await expect(header.getByText(label, { exact: true }).locator('..')).toContainText(String(n));
  }
  await expect(page.getByRole('tab')).toHaveCount(5);
  await expect(tab(page, 'Impacts')).toHaveText(`Impacts${d.impacts.length}`);
  await expect(tab(page, 'Impact chains')).toHaveText(`Impact chains${d.chains.length}`);
  await expect(tab(page, 'Disagreements')).toHaveText(`Disagreements${d.disagreements.length}`);
  await expect(tab(page, 'Open questions')).toHaveText(`Open questions${d.open_questions.length}`);
  await expect(tab(page, 'Event log')).toHaveText('Event log');
  await expect(page.getByText(`Showing all ${d.impacts.length} impacts.`)).toBeVisible();
});

test('impacts group by provision area and affected actor; cards expand and collapse with a provenance chain', async ({ page }) => {
  const run = await openDetail(page, SCENARIOS.sme);
  const d = run.dossier!;
  // Provision area (default): one group per provision key of the primary findings.
  await expect(page.getByRole('button', { name: 'Provision area' })).toHaveAttribute('aria-pressed', 'true');
  const keys = [...new Set(d.impacts.map((i) => i.findings[0].provision_key))];
  for (const k of keys) {
    const label = page.locator('main span', { hasText: new RegExp(`^${AREA[k]}$`) });
    await expect(label).toHaveCount(1);
    await expect(label.locator('..')).toContainText(k);
  }
  // Affected actor.
  await page.getByRole('button', { name: 'Affected actor' }).click();
  await expect(page.getByRole('button', { name: 'Affected actor' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('main').getByText(/^\d+ impacts?$/).first()).toBeVisible();
  const actorGroups = page.locator('main span', { hasText: /^(Small providers|Authorities|Participants|Providers and operators|Individuals)$/ });
  expect(await actorGroups.count()).toBeGreaterThan(0);
  for (const k of keys) await expect(page.locator('main span', { hasText: new RegExp(`^${AREA[k]}$`) })).toHaveCount(0);
  for (const im of d.impacts) await expect(impactCard(page, im.impact_id)).toHaveCount(1);
  await page.getByRole('button', { name: 'Provision area' }).click();

  // Expand, check the provenance chain of every finding, collapse.
  const im = d.impacts[0];
  const toggle = impactCard(page, im.impact_id).locator('button[aria-expanded]');
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(toggle).toContainText(im.summary);
  for (const f of im.findings) {
    const block = impactCard(page, im.impact_id).getByText(f.finding_id, { exact: true }).locator('xpath=../..');
    await expect(block).toBeVisible();
    const steps = await block.locator('div[style*="text-transform: uppercase"]').allTextContents();
    expect(steps.slice(0, 3)).toEqual(['Regulatory change', 'Affected actor', 'Mechanism']);
    expect(steps.filter((s) => s === 'Evidence quote')).toHaveLength(f.evidence.length);
    expect(steps.at(-1)).toMatch(/^Sources?$/);
    expect(steps.length).toBe(4 + f.evidence.length);
    await expect(block).toContainText(f.provision_key);
    await expect(block.getByText('View in source')).toHaveCount(f.evidence.length);
  }
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(page.getByText('View in source')).toHaveCount(0);
});

test('"View in source" highlights the quote inside the source text; closes via X, overlay and Escape', async ({ page }) => {
  const run = await openDetail(page, SCENARIOS.sme);
  const im = run.dossier!.impacts[0];
  const f = im.findings[0];
  const ev = f.evidence[0];
  await impactCard(page, im.impact_id).locator('button[aria-expanded]').click();
  const quoteBtn = impactCard(page, im.impact_id).getByRole('button', { name: /View in source/ }).first();

  const dialog = page.getByRole('dialog', { name: 'Source' });
  for (const close of ['x', 'overlay', 'escape'] as const) {
    await quoteBtn.click();
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText(ev.source_id);
    await expect(dialog).toContainText('Quote matched word for word in the source');
    const mark = dialog.locator('mark');
    await expect(mark).toHaveCount(1);
    expect(squash((await mark.textContent()) ?? '')).toBe(squash(ev.quote));
    await expect(mark).toBeInViewport();
    // The mark sits inside the full source text, with text around it.
    const body = squash((await mark.locator('..').textContent()) ?? '');
    expect(body.length).toBeGreaterThan(squash(ev.quote).length + 20);
    expect(body).toContain(squash(ev.quote));
    if (close === 'x') await dialog.getByRole('button', { name: 'Close' }).click();
    else if (close === 'overlay') await page.mouse.click(40, 450);
    else await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
  }
});

test('impact chains: every step opens its impact', async ({ page }) => {
  const run = await openDetail(page, SCENARIOS.sme);
  const d = run.dossier!;
  expect(d.chains.length).toBeGreaterThan(0);
  await tab(page, 'Impact chains').click();
  for (const ch of d.chains) await expect(page.getByText(ch.description)).toBeVisible();
  for (const [ci, ch] of d.chains.entries()) {
    for (const id of ch.impact_ids) {
      await tab(page, 'Impact chains').click();
      const chainCard = page.getByText(d.chains[ci].description).locator('..');
      await chainCard.getByRole('button', { name: new RegExp(`^${id} `) }).click();
      await expect(tab(page, 'Impacts')).toHaveAttribute('aria-selected', 'true');
      await expect(impactCard(page, id).locator('button[aria-expanded]')).toHaveAttribute('aria-expanded', 'true');
      await expect(impactCard(page, id)).toBeInViewport();
    }
  }
});

test('disagreements show both readings side by side', async ({ page }) => {
  const run = await openDetail(page, SCENARIOS.sme);
  const dg = run.dossier!.disagreements[0];
  await tab(page, 'Disagreements').click();
  const cardEl = page.getByText(dg.note, { exact: true }).locator('..');
  await expect(cardEl).toContainText('Reading A');
  await expect(cardEl).toContainText('Reading B');
  for (const fid of dg.finding_ids) await expect(cardEl).toContainText(fid);
  await expect(cardEl).toContainText('We keep both readings in the dossier.');
  const sides = cardEl.locator('div[style*="border-top: 4px solid"]');
  await expect(sides).toHaveCount(2);
  const [a, b] = await sides.evaluateAll((els) => els.map((e) => e.getBoundingClientRect()));
  expect(Math.abs(a.top - b.top)).toBeLessThan(2); // side by side, not stacked
  expect(b.left).toBeGreaterThan(a.right - 1);
  // The impact link opens the impact.
  await cardEl.getByRole('button', { name: /^I\d+$/ }).first().click();
  await expect(tab(page, 'Impacts')).toHaveAttribute('aria-selected', 'true');
});

test('open questions include the evidence-unresolved finding', async ({ page }) => {
  const run = await openDetail(page, SCENARIOS.costs);
  const qs = run.dossier!.open_questions;
  await tab(page, 'Open questions').click();
  for (const [i, q] of qs.entries()) {
    const row = page.locator('main').getByText(`Q${i + 1}`, { exact: true }).locator('..');
    await expect(row).toContainText(q.question);
    await expect(row).toContainText(q.reason === 'evidence_unresolved' ? 'Evidence unresolved' : 'Raised by synthesis');
  }
  await expect(page.getByText('Evidence unresolved', { exact: true })).toHaveCount(1);
  await expect(page.getByText('None. All 3 experts returned valid findings.')).toBeVisible();
});

test('event log lines match the run events', async ({ page }) => {
  const run = await openDetail(page, SCENARIOS.sme);
  const events = await getEvents(page.request, run.run_id);
  await tab(page, 'Event log').click();
  const log = page.locator('main div[style*="background: rgb(11, 17, 18)"]');
  const lines = log.locator(':scope > div');
  const finished = events.filter((e) => e.event === 'finished');
  // run start, diff, dispatch and board lines plus one per finished node.
  await expect(lines).toHaveCount(finished.length + 4);
  const text = (await lines.allTextContents()).map(squash);
  const g = run.grounding!;
  const d = run.dossier!;
  const expectLine = (re: RegExp) => expect(text.some((t) => re.test(t)), `${re} in\n${text.join('\n')}`).toBe(true);
  expectLine(new RegExp(`INFO ?run ?started · scenario ${run.scenario_id} · ${run.system_version}`));
  expectLine(/INFO ?diff ?4 changed provisions/);
  expectLine(/INFO ?planner ?impact plan ready · 2 focus areas · [\d,]+ in \/ [\d,]+ out · \d+\.\ds/);
  expectLine(/INFO ?router ?legal=relevant fiscal=relevant stakeholder=relevant · mode=shadow decider=stub/);
  expectLine(/INFO ?dispatch ?experts started in parallel ×3/);
  for (const agent of ['legal', 'fiscal', 'stakeholder']) {
    const n = run.board!.filter((f) => f.agent === agent).length;
    expectLine(new RegExp(`INFO ?${agent} ?posted ${n} findings to board · \\d+\\.\\ds`));
  }
  expectLine(new RegExp(`INFO ?citation ?${g.passed}/${g.total} quotes found verbatim`));
  expectLine(new RegExp(`INFO ?synthesis ?${d.impacts.length} impacts · ${d.chains.length} chains · 1 disagreement · ${d.open_questions.length} open questions`));
  expectLine(new RegExp(`INFO ?dossier ?assembled · status=succeeded · ${d.impacts.length} impacts`));
  // Timestamps are wall-clock times of the events.
  for (const t of text) expect(t).toMatch(/^\d{1,2}:\d\d:\d\d\.\d/);
});

test('demo diff: modified provisions across renumbering', async ({ page }) => {
  const run = await openDetail(page, SCENARIOS.demo);
  await expect(page.locator('aside')).toContainText('EU AI Act · proposal → adopted text');
  await expect(page.locator('aside')).toContainText('COM(2021) 206 → Reg 2024/1689 · Art 16, 62, 99');
  const im = run.dossier!.impacts.find((i) => i.findings[0].provision_key === 'ai_act/innovation/sme_measures')!;
  await impactCard(page, im.impact_id).locator('button[aria-expanded]').click();
  const change = impactCard(page, im.impact_id).getByText('Regulatory change', { exact: true }).first().locator('..');
  await expect(change).toContainText('ai_act/innovation/sme_measures · Art 62 · modified');
  await expect(impactCard(page, im.impact_id)).toContainText('Art 62');
  // Its quote is found in the adopted text.
  await impactCard(page, im.impact_id).getByRole('button', { name: /View in source/ }).first().click();
  await expect(page.getByRole('dialog', { name: 'Source' })).toContainText('reg2024_1689/art_62');
  await expect(page.getByRole('dialog', { name: 'Source' }).locator('mark')).toHaveCount(1);
  await page.keyboard.press('Escape');
  await go(page, 'Run pipeline');
  await expect(page.locator('[data-node="diff"]')).toContainText('3 changed provisions');
  await go(page, 'Topology');
  await page.locator('[data-topo="diff"]').click();
  await expect(page.getByText('Compares provision keys between versions, following renumbered articles across texts.')).toBeVisible();
  await expect(screenTitle(page)).toHaveText('Topology');
});
