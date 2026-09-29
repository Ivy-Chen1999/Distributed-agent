// Agents cards and the process topology (inspector, chips, edges, no clipped nodes).
import { SCENARIOS, expect, finishedRun, go, openConsole, screenTitle, test, topoNode, type RunDetail } from './helpers';
import type { Page } from '@playwright/test';

const EXPERTS = ['legal', 'fiscal', 'stakeholder'];
const NAMES: Record<string, string> = {
  diff: 'Regulatory diff', planner: 'Impact Planner', router: 'Router · Jev', legal: 'Legal', fiscal: 'Fiscal',
  stakeholder: 'Stakeholder', board: 'Impact Board', citation: 'Citation check', synthesis: 'Synthesis', dossier: 'Impact Dossier',
};
const NODES = Object.keys(NAMES);
const inspector = (page: Page) => page.getByText('Inspector', { exact: true }).locator('..');
const kTok = (n: number) => (n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n));

let run: RunDetail;
test.beforeEach(async ({ page }) => {
  run = await finishedRun(page.request, SCENARIOS.sme);
  await openConsole(page);
});

test('one card per agent with its stats; "Open in topology" selects it', async ({ page }) => {
  await go(page, 'Agents');
  const ids = ['planner', 'router', ...EXPERTS, 'synthesis'];
  await expect(page.locator('[data-agent]')).toHaveCount(ids.length);
  for (const id of ids) {
    const c = page.locator(`[data-agent="${id}"]`);
    await expect(c).toContainText(NAMES[id]);
    await expect(c).toContainText('Done');
    const stats = c.locator('div[style*="grid-template-columns: repeat(3"] > div');
    await expect(stats).toHaveCount(3);
    if (id === 'router') {
      await expect(c).toContainText('decisions');
      await expect(stats.nth(1)).toContainText('shadow');
      await expect(stats.nth(2)).toContainText('stub');
      await expect(c).toContainText('stub decider');
    } else {
      const usage = run.usage!.filter((u) => (id === 'planner' || id === 'synthesis' ? u.role === id : u.agent === id));
      const tokens = usage.reduce((a, u) => a + u.input_tokens + u.output_tokens, 0);
      await expect(stats.nth(0)).toContainText(/\d+\.\ds/);
      await expect(stats.nth(1)).toContainText(kTok(tokens));
      await expect(stats.nth(2)).toContainText(/\$\d+\.\d\d/);
      await expect(c).toContainText('claude-sonnet-5');
      await expect(c).toContainText(/prompts\/[\w./-]+\.md/);
    }
  }
  for (const id of ids) {
    await go(page, 'Agents');
    await page.locator(`[data-agent="${id}"]`).getByRole('button', { name: 'Open in topology' }).click();
    await expect(screenTitle(page)).toHaveText('Topology');
    await expect(inspector(page)).toContainText(NAMES[id]);
    await expect(topoNode(page, id)).toHaveCSS('border-top-width', '2px');
  }
});

test('every topology node opens its facts in the inspector; chips navigate; edges render', async ({ page }) => {
  await go(page, 'Topology');
  // 2 (diff→planner→router) + 3 router→expert + 3 expert→board + 4 (board, citation, synthesis, dossier)
  await expect(page.locator('main svg path')).toHaveCount(2 + EXPERTS.length * 2 + 4);
  for (const id of NODES) {
    await topoNode(page, id).click();
    await expect(inspector(page)).toContainText(NAMES[id]);
    await expect(inspector(page).getByText('Role', { exact: true }).locator('..')).toBeVisible();
    await expect(inspector(page).getByText('Latency', { exact: true }).locator('..')).toBeVisible();
  }
  await topoNode(page, 'legal').click();
  for (const k of ['Backend', 'Model', 'Prompt', 'Prompt hash', 'Tokens in / out', 'Cost']) {
    await expect(inspector(page).getByText(k, { exact: true })).toBeVisible();
  }
  await expect(inspector(page).getByText('Backend', { exact: true }).locator('..')).toContainText('fake');
  await expect(topoNode(page, 'legal')).toContainText('fake');
  await topoNode(page, 'citation').click();
  await expect(inspector(page).getByText('Quotes verified', { exact: true }).locator('..')).toContainText(`${run.grounding!.passed}/${run.grounding!.total}`);
  await topoNode(page, 'router').click();
  await expect(inspector(page).getByText('Mode', { exact: true }).locator('..')).toContainText('shadow');

  // "Receives from" / "Sends to" chips walk the graph.
  await topoNode(page, 'planner').click();
  const receives = inspector(page).getByText('Receives from', { exact: true }).locator('xpath=following-sibling::div[1]');
  const sends = inspector(page).getByText('Sends to', { exact: true }).locator('xpath=following-sibling::div[1]');
  await expect(receives.getByRole('button')).toHaveText(['Regulatory diff']);
  await expect(sends.getByRole('button')).toHaveText(['Router · Jev']);
  await sends.getByRole('button', { name: 'Router · Jev' }).click();
  await expect(inspector(page)).toContainText('Router · Jev');
  await expect(sends.getByRole('button')).toHaveText(['Legal', 'Fiscal', 'Stakeholder']);
  await sends.getByRole('button', { name: 'Fiscal' }).click();
  await expect(sends.getByRole('button')).toHaveText(['Impact Board']);
  await sends.getByRole('button', { name: 'Impact Board' }).click();
  await expect(receives.getByRole('button')).toHaveText(['Legal', 'Fiscal', 'Stakeholder']);
  await expect(sends.getByRole('button')).toHaveText(['Citation check', 'Synthesis']);
  await receives.getByRole('button', { name: 'Stakeholder' }).click();
  await expect(inspector(page)).toContainText('Stakeholder');
  await topoNode(page, 'diff').click();
  await expect(receives).toContainText('—');
  await topoNode(page, 'dossier').click();
  await expect(sends).toContainText('—');
  // The selected node's edges are highlighted.
  const highlighted = await page.locator('main svg path[stroke="#00C4CC"]').count();
  expect(highlighted).toBe(1);
});

for (const width of [1440, 1024, 820]) {
  test(`no topology node is clipped at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await go(page, 'Topology');
    // The positioned canvas has side margins so the outer nodes (translated by -50%) stay
    // inside the scroll container and the card, which clip their overflow.
    const scroller = page.locator('main svg').locator('xpath=../..');
    const cardEl = scroller.locator('..');
    for (const id of NODES) {
      const node = topoNode(page, id);
      await node.scrollIntoViewIfNeeded();
      // The scroll container (overflow-x: auto) is the visible window onto the canvas.
      const [n, c, s] = await Promise.all([node.boundingBox(), cardEl.boundingBox(), scroller.boundingBox()]);
      expect(n && c && s).toBeTruthy();
      for (const box of [c!, s!]) {
        expect(n!.x, `${id} left`).toBeGreaterThanOrEqual(box.x - 0.5);
        expect(n!.x + n!.width, `${id} right`).toBeLessThanOrEqual(box.x + box.width + 0.5);
        expect(n!.y, `${id} top`).toBeGreaterThanOrEqual(box.y - 0.5);
        expect(n!.y + n!.height, `${id} bottom`).toBeLessThanOrEqual(box.y + box.height + 0.5);
      }
      await expect(node).toBeInViewport({ ratio: 1 });
    }
  });
}
