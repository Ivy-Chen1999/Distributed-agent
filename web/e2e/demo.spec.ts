// Demo video, built like a motion-graphics piece rather than a screen recording:
//   1. `capture` takes lossless full-page screenshots (2x device pixels) of the console showing
//      the newest finished run on a real WOMM server, plus each component's position;
//   2. `render` loads web/demo/stage.html, which places those shots on a scripted timeline
//      (camera pans and zooms onto components, crossfades, captions), seeks it frame by frame
//      and pipes lossless frames to ffmpeg. No real-time recording, so no judder.
// Skipped unless WOMM_E2E_DEMO=1, WOMM_E2E_LIVE_URL and WOMM_E2E_LIVE_TOKEN are set:
//   WOMM_E2E_DEMO=1 WOMM_E2E_LIVE_URL=http://localhost:8000 WOMM_E2E_LIVE_TOKEN=… \
//     WOMM_DEMO_DIR=../runs/demo npx playwright test
// The token goes straight into localStorage, so it never appears on screen.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test, type Browser, type Locator, type Page } from '@playwright/test';

const URL = process.env.WOMM_E2E_LIVE_URL;
const TOKEN = process.env.WOMM_E2E_LIVE_TOKEN;
const DEMO = process.env.WOMM_E2E_DEMO === '1' && !!URL && !!TOKEN;
const OUT = path.resolve(process.env.WOMM_DEMO_DIR ?? 'test-results/demo');
const ASSETS = path.join(OUT, 'assets');
const STAGE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../demo/stage.html');
const FPS = Number(process.env.WOMM_DEMO_FPS ?? 30);

test.skip(!DEMO, 'set WOMM_E2E_DEMO=1, WOMM_E2E_LIVE_URL and WOMM_E2E_LIVE_TOKEN to build the demo');
test.describe.configure({ mode: 'serial' });

type Box = [number, number, number, number];
interface Shot {
  file: string;
  w: number;
  h: number;
  boxes: Record<string, Box>;
}
const shots: Record<string, Shot> = {};

async function open(browser: Browser, width: number, height: number, opts: { clock?: boolean } = {}): Promise<Page> {
  const context = await browser.newContext({ baseURL: URL, viewport: { width, height }, deviceScaleFactor: 2 });
  await context.addInitScript((t) => localStorage.setItem('womm.token', t), TOKEN!);
  const page = await context.newPage();
  if (opts.clock) await page.clock.install();
  await page.goto('/');
  await expect(page.locator('header')).toBeVisible();
  await expect(page.getByText(/run_[0-9a-f]{8}/).first()).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  return page;
}

/** Page-coordinate box of a component (independent of scroll). */
async function boxOf(target: Locator): Promise<Box> {
  const b = await target.first().evaluate((el) => {
    const r = el.getBoundingClientRect();
    return [r.left + window.scrollX, r.top + window.scrollY, r.width, r.height];
  }, undefined, { timeout: 10_000 });
  return b.map((v) => Math.round(v)) as Box;
}

async function shoot(page: Page, name: string, boxes: Record<string, Locator> = {}, fullPage = true): Promise<void> {
  await page.waitForTimeout(450); // entry animations
  const out: Record<string, Box> = {};
  for (const [k, loc] of Object.entries(boxes)) out[k] = await boxOf(loc);
  const file = `${name}.png`;
  await page.screenshot({ path: path.join(ASSETS, file), fullPage, animations: 'disabled', caret: 'hide' });
  const size = await page.evaluate((fp) => (fp ? { w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight } : { w: innerWidth, h: innerHeight }), fullPage);
  shots[name] = { file, ...size, boxes: out };
}

const card = (page: Page, label: string) => page.locator('main div[style*="border-radius: 12px"]').filter({ has: page.getByText(label) }).last();
const nav = (page: Page, title: string) => page.locator('aside nav button', { hasText: title }).click();

test('capture', async ({ browser }) => {
  test.setTimeout(10 * 60_000);
  fs.mkdirSync(ASSETS, { recursive: true });

  const page = await open(browser, 1600, 900);
  await shoot(page, 'overview', {
    tiles: page.locator('main > div > div').filter({ has: page.getByText('Status', { exact: true }) }).first(),
    latency: card(page, 'Agent latency'),
    review: card(page, 'Needs your review'),
    runs: card(page, 'Recent runs'),
  });

  await nav(page, 'Run pipeline');
  await shoot(page, 'pipeline', { graph: card(page, 'Agent graph'), board: card(page, 'Shared impact board'), router: card(page, 'Router decisions'), node: card(page, 'Selected node') });

  await nav(page, 'Run detail');
  await shoot(page, 'detail', { head: card(page, 'Impact dossier') });
  await page.locator('main button[aria-expanded]').first().click();
  await shoot(page, 'detail-open', { impact: page.locator('main [id^="imp-"]').first(), quote: page.getByRole('button', { name: /View in source/ }).first() });
  await page.getByRole('button', { name: /View in source/ }).first().click();
  await page.waitForTimeout(400);
  await shoot(page, 'source', { panel: page.getByRole('dialog', { name: 'Source' }), mark: page.getByRole('dialog', { name: 'Source' }).locator('mark') }, false);
  await page.keyboard.press('Escape');
  for (const [tab, name] of [[/Disagreements/, 'disagreements'], [/Open questions/, 'questions']] as const) {
    await page.getByRole('tab', { name: tab }).click();
    await shoot(page, name, { body: page.locator('main [role="tablist"] ~ div').first() });
  }

  await nav(page, 'Agents');
  await shoot(page, 'agents', { cards: page.locator('main [data-agent]').first().locator('xpath=..') });
  await nav(page, 'Topology');
  await page.locator('[data-topo="synthesis"]').click();
  await shoot(page, 'topology', { graph: card(page, 'Process topology') });

  await nav(page, 'Ask WOMM');
  await page.getByLabel('Question').fill('Who carries the penalty risk?');
  await shoot(page, 'ask-before', { input: page.getByLabel('Question') }, false);
  await page.getByLabel('Question').press('Enter');
  await expect(page.getByText('Reading the dossier…')).toBeHidden({ timeout: 180_000 });
  await shoot(page, 'ask-after', { answer: page.locator('main [aria-live="polite"] > div').last() });

  await nav(page, 'Settings');
  await shoot(page, 'settings', {});
  await page.getByTitle('Switch theme').click();
  await nav(page, 'Overview');
  await shoot(page, 'overview-dark', {}, false);
  await page.context().close();

  // Pipeline replay under a paused clock: 16 evenly spaced moments of the real run at 8x.
  const rp = await open(browser, 1600, 900, { clock: true });
  await nav(rp, 'Run pipeline');
  await rp.clock.pauseAt(await rp.evaluate(() => Date.now() + 50));
  await rp.getByRole('button', { name: '8×' }).click();
  await rp.getByRole('button', { name: 'Replay' }).click();
  for (let i = 0; i < 16; i++) {
    await shoot(rp, `replay-${String(i).padStart(2, '0')}`, i === 0 ? { graph: card(rp, 'Agent graph'), board: card(rp, 'Shared impact board') } : {}, false);
    await rp.clock.runFor(3200); // 3.2 s of page time = 25.6 s of run time at 8x
  }
  await rp.context().close();

  // Phones, shown side by side inside the landscape frame.
  const ph = await open(browser, 390, 844);
  await shoot(ph, 'phone-overview', {}, false);
  for (const [title, name] of [['Run pipeline', 'phone-pipeline'], ['Topology', 'phone-topology']] as const) {
    await ph.getByRole('button', { name: 'Open navigation' }).click();
    await ph.locator('aside nav button', { hasText: title }).click();
    await shoot(ph, name, {}, false);
  }
  await ph.context().close();

  fs.writeFileSync(path.join(ASSETS, 'shots.js'), `window.SHOTS = ${JSON.stringify(shots, null, 1)};\n`);
});

test('render', async ({ browser }) => {
  test.setTimeout(30 * 60_000);
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  await page.goto(`file://${STAGE}?assets=${encodeURIComponent(ASSETS)}`);
  await page.waitForFunction(() => (window as unknown as { __ready?: boolean }).__ready === true, undefined, { timeout: 30_000 });
  const duration = await page.evaluate(() => (window as unknown as { __duration: number }).__duration);

  const mp4 = path.join(OUT, 'womm-demo.mp4');
  const ff = spawn('ffmpeg', [
    '-v', 'error', '-y', '-f', 'image2pipe', '-framerate', String(FPS), '-c:v', 'png', '-i', '-',
    // CRF 26 + stillimage: text stays visually identical to CRF 18 at well under half the size.
    '-c:v', 'libx264', '-preset', 'veryslow', '-crf', '26', '-tune', 'stillimage', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', mp4,
  ], { stdio: ['pipe', 'inherit', 'inherit'] });
  const done = new Promise<number>((resolve) => ff.on('close', (code) => resolve(code ?? 1)));

  const frames = Math.ceil(duration * FPS);
  for (let i = 0; i < frames; i++) {
    await page.evaluate((t) => (window as unknown as { __seek: (t: number) => Promise<void> }).__seek(t), i / FPS);
    const png = await page.screenshot({ type: 'png' });
    if (!ff.stdin.write(png)) await new Promise((r) => ff.stdin.once('drain', r));
  }
  ff.stdin.end();
  expect(await done).toBe(0);
  await context.close();
});
