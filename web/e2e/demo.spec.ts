// Demo recording: a captioned end-to-end tour of the console against a real WOMM server, using
// the newest finished run (real LLM output). Skipped unless WOMM_E2E_DEMO=1, WOMM_E2E_LIVE_URL
// and WOMM_E2E_LIVE_TOKEN are set:
//   WOMM_E2E_DEMO=1 WOMM_E2E_LIVE_URL=http://localhost:8000 WOMM_E2E_LIVE_TOKEN=… \
//     WOMM_DEMO_DIR=../runs/demo/raw npx playwright test && e2e/demo-encode.sh ../runs/demo/raw
//
// Frames come from Chrome's screencast at device-pixel resolution (2× desktop, 3× phone) with
// their real timestamps, so the encoded video is sharp and plays at true speed without the
// judder of Playwright's built-in recorder. Each scene centres one component and a spotlight
// dims the rest. The token goes straight into localStorage, so it never appears on screen.
import fs from 'node:fs';
import path from 'node:path';
import { expect, test, type Browser, type Locator, type Page } from '@playwright/test';

const URL = process.env.WOMM_E2E_LIVE_URL;
const TOKEN = process.env.WOMM_E2E_LIVE_TOKEN;
const DEMO = process.env.WOMM_E2E_DEMO === '1' && !!URL && !!TOKEN;
const OUT = process.env.WOMM_DEMO_DIR ?? path.resolve('test-results/demo');

test.skip(!DEMO, 'set WOMM_E2E_DEMO=1, WOMM_E2E_LIVE_URL and WOMM_E2E_LIVE_TOKEN to record the demo');

interface Recording {
  page: Page;
  stop: () => Promise<void>;
}

/** Open a page and stream its screencast frames (JPEG, device pixels) plus timestamps to disk. */
async function record(browser: Browser, name: string, width: number, height: number, scale: number): Promise<Recording> {
  const context = await browser.newContext({ baseURL: URL, viewport: { width, height }, deviceScaleFactor: scale });
  await context.addInitScript((t) => localStorage.setItem('womm.token', t), TOKEN!);
  const page = await context.newPage();
  const dir = path.join(OUT, name);
  fs.mkdirSync(dir, { recursive: true });
  const index: { file: string; t: number }[] = [];
  const cdp = await context.newCDPSession(page);
  cdp.on('Page.screencastFrame', (f) => {
    const file = `f${String(index.length).padStart(6, '0')}.jpg`;
    fs.writeFileSync(path.join(dir, file), Buffer.from(f.data, 'base64'));
    index.push({ file, t: f.metadata.timestamp ?? Date.now() / 1000 });
    void cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }).catch(() => {});
  });
  await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 95, maxWidth: width * scale, maxHeight: height * scale, everyNthFrame: 1 });
  return {
    page,
    stop: async () => {
      await page.waitForTimeout(600);
      await cdp.send('Page.stopScreencast');
      fs.writeFileSync(path.join(dir, 'frames.json'), JSON.stringify({ width: width * scale, height: height * scale, frames: index }));
      await context.close();
    },
  };
}

/** Caption bar and spotlight live outside the app's DOM tree, above everything. */
async function installOverlay(page: Page): Promise<void> {
  await page.evaluate(() => {
    const spot = document.createElement('div');
    spot.id = 'demo-spot';
    Object.assign(spot.style, {
      position: 'fixed', zIndex: '99998', pointerEvents: 'none', borderRadius: '14px', opacity: '0',
      boxShadow: '0 0 0 200vmax rgba(10,16,17,.55), 0 0 0 3px #00C4CC',
      transition: 'left .5s cubic-bezier(.2,0,0,1), top .5s cubic-bezier(.2,0,0,1), width .5s cubic-bezier(.2,0,0,1), height .5s cubic-bezier(.2,0,0,1), opacity .35s ease',
    });
    const cap = document.createElement('div');
    cap.id = 'demo-caption';
    Object.assign(cap.style, {
      position: 'fixed', left: '50%', bottom: '28px', transform: 'translateX(-50%)', zIndex: '99999',
      maxWidth: 'min(920px, 92vw)', padding: '12px 22px', borderRadius: '10px', background: 'rgba(15,15,15,.92)',
      color: '#fff', font: "600 17px 'Manrope', sans-serif", lineHeight: '1.45', textAlign: 'center',
      pointerEvents: 'none', boxShadow: '0 8px 30px rgba(0,0,0,.3)', opacity: '0', transition: 'opacity .3s ease',
    });
    for (const el of [spot, cap]) el.setAttribute('aria-hidden', 'true');
    document.body.append(spot, cap);
  });
}

async function caption(page: Page, text: string, holdMs = 2600): Promise<void> {
  await page.evaluate((t) => {
    const cap = document.getElementById('demo-caption')!;
    cap.textContent = t;
    cap.style.opacity = t ? '1' : '0';
  }, text);
  await page.waitForTimeout(holdMs);
}

/** Smooth-scroll a component into view (below the sticky header) and move the spotlight onto it. */
async function focus(page: Page, target: Locator, pad = 10): Promise<void> {
  await target.first().evaluate((el) => {
    const header = document.querySelector('header')?.getBoundingClientRect().bottom ?? 0;
    const r = el.getBoundingClientRect();
    const room = window.innerHeight - header;
    const top = window.scrollY + r.top - header - Math.max(16, (room - r.height) / 2);
    window.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
  }, undefined, { timeout: 10_000 });
  await page.waitForTimeout(700);
  const box = await target.first().boundingBox();
  if (!box) return;
  await page.evaluate(({ x, y, w, h, p }) => {
    const s = document.getElementById('demo-spot')!;
    Object.assign(s.style, { left: `${x - p}px`, top: `${y - p}px`, width: `${w + 2 * p}px`, height: `${h + 2 * p}px`, opacity: '1' });
  }, { x: box.x, y: box.y, w: box.width, h: box.height, p: pad });
  await page.waitForTimeout(550);
}

async function unfocus(page: Page): Promise<void> {
  await page.evaluate(() => (document.getElementById('demo-spot')!.style.opacity = '0'));
  await page.waitForTimeout(350);
}

/** The card whose heading label contains `label`. */
const card = (page: Page, label: string) => page.locator('main div[style*="border-radius: 12px"]').filter({ has: page.getByText(label) }).last();

async function goTo(page: Page, title: string): Promise<void> {
  await unfocus(page);
  const menu = page.getByRole('button', { name: 'Open navigation' });
  if (await menu.isVisible()) {
    await menu.click();
    await page.waitForTimeout(500);
  }
  await page.locator('aside nav button', { hasText: title }).click();
  await page.waitForTimeout(500);
}

test('desktop tour', async ({ browser }) => {
  test.setTimeout(10 * 60_000);
  const { page, stop } = await record(browser, 'desktop', 1440, 900, 2);
  await page.goto('/');
  await expect(page.locator('header')).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  await installOverlay(page);
  await caption(page, 'WOMM — a multi-agent Regulatory Impact Assessment of the EU AI Act. A real run with real Claude output.', 3600);

  // Overview.
  await focus(page, page.locator('main > div > div').filter({ has: page.getByText('Status', { exact: true }) }).first());
  await caption(page, 'The latest run at a glance: status, wall-clock time, tokens, cost and grounding.', 3400);
  await focus(page, card(page, 'Agent latency'));
  await caption(page, 'How long each agent took. The three experts run in parallel.', 3000);
  await focus(page, card(page, 'Needs your review'));
  await caption(page, 'What an analyst should look at: disagreements, open questions, failed experts.', 3200);

  // Pipeline replay.
  await goTo(page, 'Run pipeline');
  await page.getByRole('button', { name: '8×' }).click();
  await focus(page, card(page, 'Agent graph'));
  await caption(page, 'The pipeline: diff → Impact Planner → router (Jev) → three experts → board → citation check → synthesis → dossier.', 3600);
  await page.getByRole('button', { name: 'Replay' }).click();
  await caption(page, 'Replaying the real 6-minute run at 8× from its recorded events.', 6000);
  await caption(page, 'The planner turns the diff into impact hypotheses; the router scores each expert.', 8000);
  await caption(page, 'Legal, Fiscal and Stakeholder experts run in parallel as isolated Claude calls.', 8000);
  await page.locator('[data-node="legal"]').click();
  await focus(page, card(page, 'Shared impact board'));
  await caption(page, 'Findings land on one shared impact board; every quote is checked word for word against the source.', 9000);
  await focus(page, card(page, 'Agent graph'));
  await expect(page.locator('[data-node="dossier"]')).toContainText('Done', { timeout: 60_000 });
  await caption(page, 'Synthesis merges the findings into impacts, chains, disagreements and open questions.', 3200);
  await focus(page, card(page, 'Selected node'));
  await caption(page, 'Every node records its backend, model, prompt hash, tokens and latency.', 3400);

  // Dossier.
  await goTo(page, 'Run detail');
  await focus(page, card(page, 'Impact dossier'));
  await caption(page, 'The Impact Dossier for this run.', 2600);
  const firstImpact = page.locator('main button[aria-expanded]').first();
  await firstImpact.click();
  await page.waitForTimeout(400);
  await focus(page, page.locator('main [id^="imp-"]').first());
  await caption(page, 'Each impact is merged from several experts. Every finding traces back: change → actor → mechanism → quote → source.', 4200);
  await unfocus(page);
  await page.getByRole('button', { name: /View in source/ }).first().click();
  await page.waitForTimeout(500);
  await caption(page, 'The quote, highlighted in the original legal text.', 3600);
  await page.keyboard.press('Escape');
  await page.waitForTimeout(400);
  for (const [tab, text] of [
    [/Disagreements/, 'Disagreements between experts are kept, not averaged away.'],
    [/Open questions/, 'Open questions for an analyst, including evidence that could not be verified.'],
  ] as const) {
    await page.getByRole('tab', { name: tab }).click();
    await page.waitForTimeout(300);
    await focus(page, page.locator('main [role="tablist"] ~ div').first());
    await caption(page, text, 3200);
  }

  // Agents and topology.
  await goTo(page, 'Agents');
  await focus(page, page.locator('main [data-agent]').first().locator('xpath=..'));
  await caption(page, 'One card per agent: prompt file and hash, model, latency, tokens, cost.', 3200);
  await goTo(page, 'Topology');
  await page.locator('[data-topo="synthesis"]').click();
  await focus(page, card(page, 'Process topology'));
  await caption(page, 'How data flows between agents. Dashed boxes are isolated `claude -p` subprocesses.', 3600);

  // Ask.
  await goTo(page, 'Ask WOMM');
  await caption(page, 'Ask WOMM answers only from this run’s dossier and cites impact ids.', 2600);
  await page.getByLabel('Question').click();
  await page.getByLabel('Question').pressSequentially('Who carries the penalty risk?', { delay: 55 });
  await page.getByLabel('Question').press('Enter');
  await caption(page, 'Asking Claude…', 800);
  await expect(page.getByText('Reading the dossier…')).toBeHidden({ timeout: 180_000 });
  await focus(page, page.locator('main [aria-live="polite"] > div').last());
  await caption(page, 'A real answer, with citations back into the dossier.', 5000);

  // Settings and theme.
  await goTo(page, 'Settings');
  await caption(page, 'Settings: the content-addressed system version, a backend per role, the router mode and the isolation self-check.', 3800);
  await page.getByTitle('Switch theme').click();
  await goTo(page, 'Overview');
  await caption(page, 'Dark theme. Every screen passes an automated WCAG 2.1 AA check.', 3600);
  await caption(page, '', 400);
  await stop();
});

test('phone tour', async ({ browser }) => {
  test.setTimeout(5 * 60_000);
  const { page, stop } = await record(browser, 'phone', 390, 844, 3);
  await page.goto('/');
  await expect(page.locator('header')).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  await installOverlay(page);
  await caption(page, 'The same console on a phone.', 2600);
  await page.getByRole('button', { name: 'Open navigation' }).click();
  await caption(page, 'Navigation moves into a drawer.', 2200);
  await page.locator('aside nav button', { hasText: 'Run pipeline' }).click();
  await page.waitForTimeout(500);
  await focus(page, page.locator('[data-node="diff"]').locator('xpath=ancestor::div[@role="region"]'), 4);
  await caption(page, 'The pipeline runs top to bottom.', 3000);
  await goTo(page, 'Topology');
  await focus(page, card(page, 'Process topology'), 4);
  await caption(page, 'So does the topology.', 3000);
  await goTo(page, 'Run detail');
  await page.locator('main button[aria-expanded]').first().click();
  await page.waitForTimeout(400);
  await focus(page, page.locator('main [id^="imp-"]').first(), 4);
  await caption(page, 'The dossier stacks into one column.', 3200);
  await caption(page, '', 400);
  await stop();
});
