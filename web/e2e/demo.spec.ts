// Demo recording: a captioned end-to-end tour of the console against a real WOMM server, using
// the newest finished run (real LLM output). Skipped unless WOMM_E2E_DEMO=1, WOMM_E2E_LIVE_URL
// and WOMM_E2E_LIVE_TOKEN are set; the videos land in WOMM_DEMO_DIR (default test-results/demo):
//   WOMM_E2E_DEMO=1 WOMM_E2E_LIVE_URL=http://localhost:8000 WOMM_E2E_LIVE_TOKEN=… npx playwright test
// The token goes straight into localStorage, so it never appears on screen.
import path from 'node:path';
import { expect, test, type Browser, type Page } from '@playwright/test';

const URL = process.env.WOMM_E2E_LIVE_URL;
const TOKEN = process.env.WOMM_E2E_LIVE_TOKEN;
const DEMO = process.env.WOMM_E2E_DEMO === '1' && !!URL && !!TOKEN;
const OUT = process.env.WOMM_DEMO_DIR ?? path.resolve('test-results/demo');

test.skip(!DEMO, 'set WOMM_E2E_DEMO=1, WOMM_E2E_LIVE_URL and WOMM_E2E_LIVE_TOKEN to record the demo');

async function recorder(browser: Browser, name: string, width: number, height: number): Promise<Page> {
  const context = await browser.newContext({
    baseURL: URL,
    viewport: { width, height },
    deviceScaleFactor: 1,
    recordVideo: { dir: path.join(OUT, name), size: { width, height } },
  });
  await context.addInitScript((t) => localStorage.setItem('womm.token', t), TOKEN!);
  return context.newPage();
}

/** A caption bar over the page, outside the app's own DOM. */
async function caption(page: Page, text: string, holdMs = 2200): Promise<void> {
  await page.evaluate((t) => {
    let el = document.getElementById('demo-caption');
    if (!el) {
      el = document.createElement('div');
      el.id = 'demo-caption';
      el.setAttribute('aria-hidden', 'true');
      Object.assign(el.style, {
        position: 'fixed', left: '50%', bottom: '24px', transform: 'translateX(-50%)', zIndex: '99999',
        maxWidth: 'min(900px, 92vw)', padding: '12px 20px', borderRadius: '10px', background: 'rgba(15,15,15,.88)',
        color: '#fff', font: "600 16px 'Manrope', sans-serif", lineHeight: '1.45', textAlign: 'center',
        pointerEvents: 'none', boxShadow: '0 8px 30px rgba(0,0,0,.25)',
      });
      document.body.appendChild(el);
    }
    el.textContent = t;
  }, text);
  await page.waitForTimeout(holdMs);
}

const nav = (page: Page, title: string) => page.locator('aside nav button', { hasText: title });

async function slowScroll(page: Page, px: number, steps = 8): Promise<void> {
  for (let i = 0; i < steps; i++) {
    await page.mouse.wheel(0, px / steps);
    await page.waitForTimeout(120);
  }
}

test('desktop tour', async ({ browser }) => {
  test.setTimeout(10 * 60_000);
  const page = await recorder(browser, 'desktop', 1440, 900);
  await page.goto('/');
  await expect(page.locator('header')).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  await caption(page, 'WOMM — a multi-agent Regulatory Impact Assessment of the EU AI Act. Real run, real Claude output.', 3500);

  // Overview.
  await caption(page, 'Overview: the latest run — status, time, tokens, grounding (quotes verified word for word).', 3200);
  await slowScroll(page, 700);
  await caption(page, 'Agent latency and what needs an analyst: disagreements, open questions, failed experts.', 3000);
  await slowScroll(page, -700);

  // Pipeline replay.
  await nav(page, 'Run pipeline').click();
  await caption(page, 'Run pipeline: diff → Impact Planner → router (Jev) → three experts in parallel → board → citation check → synthesis → dossier.', 3500);
  await page.getByRole('button', { name: '8×' }).click();
  await page.getByRole('button', { name: 'Replay' }).click();
  await caption(page, 'Replaying the real 6-minute run at 8× from its recorded events.', 6000);
  await caption(page, 'The planner turns the diff into impact hypotheses; the router scores each expert (shadow mode).', 9000);
  await page.locator('[data-node="legal"]').click();
  await caption(page, 'Legal, Fiscal and Stakeholder experts run in parallel as isolated Claude calls.', 9000);
  await caption(page, 'Each node shows its backend, model, prompt hash, tokens and latency.', 9000);
  await caption(page, 'Findings land on one shared impact board; every quote is checked word for word.', 9000);
  await expect(page.locator('[data-node="dossier"]')).toContainText('Done', { timeout: 60_000 });
  await caption(page, 'Synthesis merges findings into impacts, chains, disagreements and open questions.', 3000);
  await slowScroll(page, 900);
  await page.waitForTimeout(1500);
  await slowScroll(page, -900);

  // Dossier.
  await nav(page, 'Run detail').click();
  await caption(page, 'The Impact Dossier: impacts grouped by provision, each merged from several experts.', 3000);
  await page.locator('main button[aria-expanded]').first().click();
  await caption(page, 'Every finding traces back: regulatory change → affected actor → mechanism → evidence quote → source.', 3500);
  await page.getByRole('button', { name: /View in source/ }).first().click();
  await caption(page, 'The quote highlighted in the original legal text.', 3500);
  await page.keyboard.press('Escape');
  for (const tab of [/Disagreements/, /Open questions/]) {
    await page.getByRole('tab', { name: tab }).click();
    await caption(page, tab.source === 'Disagreements' ? 'Disagreements between experts are kept, not averaged away.' : 'Open questions for an analyst, including evidence that could not be verified.', 3000);
  }

  // Agents and topology.
  await nav(page, 'Agents').click();
  await caption(page, 'Agents: one card per role with its prompt file, hash, model and cost.', 3000);
  await nav(page, 'Topology').click();
  await page.locator('[data-topo="synthesis"]').click();
  await caption(page, 'Topology: how data flows; dashed boxes are isolated `claude -p` subprocesses.', 3500);

  // Ask.
  await nav(page, 'Ask WOMM').click();
  await caption(page, 'Ask WOMM answers only from this run’s dossier and cites impact ids.', 2500);
  await page.getByLabel('Question').click();
  await page.getByLabel('Question').pressSequentially('Who carries the penalty risk?', { delay: 45 });
  await page.getByLabel('Question').press('Enter');
  await caption(page, 'Waiting for a real answer from Claude…', 1000);
  await expect(page.getByText('Reading the dossier…')).toBeHidden({ timeout: 180_000 });
  await caption(page, 'The answer, with citations back into the dossier.', 4500);

  // Settings and theme.
  await nav(page, 'Settings').click();
  await caption(page, 'Settings: the content-addressed system version, backends per role and the router mode.', 3500);
  await page.getByTitle('Switch theme').click();
  await nav(page, 'Overview').click();
  await caption(page, 'Dark theme. Every screen passes an automated WCAG 2.1 AA check.', 3500);
  await page.context().close();
});

test('phone tour', async ({ browser }) => {
  test.setTimeout(5 * 60_000);
  const page = await recorder(browser, 'phone', 390, 844);
  await page.goto('/');
  await expect(page.locator('header')).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  await caption(page, 'The same console on a phone.', 2500);
  await slowScroll(page, 900, 10);
  await slowScroll(page, -900, 10);
  const menu = page.getByRole('button', { name: 'Open navigation' });
  for (const [title, text, scroll] of [
    ['Run pipeline', 'The pipeline runs top to bottom.', 1400],
    ['Topology', 'So does the topology.', 380],
    ['Run detail', 'The dossier stacks into one column.', 1400],
  ] as const) {
    await menu.click();
    await page.waitForTimeout(700);
    await page.locator('aside nav button', { hasText: title }).click();
    await caption(page, text, 2000);
    await slowScroll(page, scroll, 12);
    await page.waitForTimeout(800);
  }
  await page.context().close();
});
