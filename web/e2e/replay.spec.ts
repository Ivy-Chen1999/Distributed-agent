// Replay a finished run at every speed: statuses reset, then replay to completion.
import { SCENARIOS, expect, finishedRun, go, openConsole, pipeNode, statusChip, test } from './helpers';
import type { Page } from '@playwright/test';

const NODES = ['planner', 'legal', 'synthesis', 'dossier'];

/** Record every text a pipeline node shows (MutationObserver), so fast replays are observable. */
async function recordStatuses(page: Page): Promise<void> {
  await page.evaluate((nodes) => {
    const w = window as unknown as { __seen: Record<string, string[]>; __obs?: MutationObserver };
    w.__obs?.disconnect();
    w.__seen = Object.fromEntries(nodes.map((n) => [n, []]));
    const snap = () => {
      for (const n of nodes) {
        const el = document.querySelector(`[data-node="${n}"]`);
        const st = el?.textContent?.match(/Queued|Running|Done|Failed|Skipped/)?.[0];
        const list = w.__seen[n];
        if (st && list[list.length - 1] !== st) list.push(st);
      }
      const chip = document.querySelector('header > div:nth-child(2) > span')?.textContent ?? '';
      const l = (w.__seen.header ??= []);
      if (l[l.length - 1] !== chip) l.push(chip);
    };
    w.__obs = new MutationObserver(snap);
    w.__obs.observe(document.body, { subtree: true, childList: true, characterData: true });
    snap();
  }, NODES);
}

const seen = (page: Page) => page.evaluate(() => (window as unknown as { __seen: Record<string, string[]> }).__seen);

test('replay at 1×, 2×, 3×, 4× and 8× resets and replays to completion', async ({ page }) => {
  await finishedRun(page.request, SCENARIOS.sme);
  await openConsole(page);
  await go(page, 'Run pipeline');
  await expect(statusChip(page)).toHaveText('Succeeded');
  const replay = page.getByRole('button', { name: 'Replay', exact: true });
  await expect(replay).toBeVisible();
  // Default speed is 3×.
  await expect(page.getByRole('button', { name: '3×' })).toHaveAttribute('aria-pressed', 'true');

  for (const speed of [1, 2, 3, 4, 8]) {
    const btn = page.getByRole('button', { name: `${speed}×` });
    await btn.click();
    await expect(btn).toHaveAttribute('aria-pressed', 'true');
    await recordStatuses(page);
    await replay.click();
    // Finished again: Replay button back, final statuses.
    await expect(replay).toBeVisible({ timeout: 20_000 });
    await expect(statusChip(page)).toHaveText('Succeeded');
    for (const id of NODES) await expect(pipeNode(page, id)).toContainText('Done');
    const s = await seen(page);
    expect(s.header, `speed ${speed}`).toContain('Replaying');
    // Everything restarted from the beginning: synthesis was done, queued again during the
    // replay and done at the end; the planner was reset too (queued or running near clock 0).
    expect(s.synthesis[0], `speed ${speed}`).toBe('Done');
    expect(s.synthesis.indexOf('Queued'), `speed ${speed}`).toBeGreaterThan(0);
    expect(s.synthesis.at(-1)).toBe('Done');
    expect(s.dossier).toContain('Queued');
    expect(['Queued', 'Running'], `planner at ${speed}×: ${s.planner}`).toContain(s.planner[1]);
    expect(s.planner.at(-1)).toBe('Done');
  }
});

test('stop replay returns to the finished state; the overview "Watch pipeline" starts a replay', async ({ page }) => {
  await finishedRun(page.request, SCENARIOS.sme);
  // Fake timers. install() alone keeps time flowing, so on a slow machine the replay would run
  // ahead of the assertions: pause the clock before starting the replay, then advance it only
  // with runFor().
  await page.clock.install();
  await openConsole(page);
  await expect(statusChip(page)).toHaveText('Succeeded');
  await page.clock.pauseAt(await page.evaluate(() => Date.now() + 50));
  await page.getByRole('button', { name: 'Watch pipeline' }).click();
  await expect(page.getByRole('button', { name: 'Stop replay' })).toBeVisible();
  await expect(statusChip(page)).toHaveText('Replaying');
  // Clock 0: the planner starts a few ms after the run, so it is queued or just running.
  await expect(pipeNode(page, 'planner')).toContainText(/Queued|Running/);
  await expect(pipeNode(page, 'synthesis')).toContainText('Queued');
  await page.getByRole('button', { name: '1×' }).click();
  await page.clock.runFor(100);
  await expect(pipeNode(page, 'planner')).toContainText(/Running|Done/);
  await expect(pipeNode(page, 'dossier')).toContainText('Queued');
  await page.getByRole('button', { name: 'Stop replay' }).click();
  await expect(page.getByRole('button', { name: 'Replay', exact: true })).toBeVisible();
  await expect(statusChip(page)).toHaveText('Succeeded');
  await expect(pipeNode(page, 'dossier')).toContainText('Done');
});
