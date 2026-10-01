// Screen tour in both themes: design tokens, reference screenshots, no console or page errors,
// the ligature regression, and tablet widths without horizontal page scroll.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { SCENARIOS, SCREENS, TH, expect, finishedRun, go, openConsole, rgb, test, type ScreenTitle } from './helpers';
import type { Page, TestInfo } from '@playwright/test';

const SHOTS = path.join(path.dirname(fileURLToPath(import.meta.url)), 'screenshots');
const SLUG: Record<ScreenTitle, string> = {
  Overview: 'overview', 'Run pipeline': 'pipeline', 'Run detail': 'detail', Agents: 'agents', Topology: 'topology', 'Ask WOMM': 'ask', Settings: 'settings',
};

/** Attach to the report; refresh the committed reference set only on request (or when missing). */
async function shoot(page: Page, info: TestInfo, name: string): Promise<void> {
  const file = info.outputPath(`${name}.png`);
  await page.screenshot({ path: file, fullPage: true, animations: 'disabled' });
  await info.attach(name, { path: file, contentType: 'image/png' });
  const ref = path.join(SHOTS, `${name}.png`);
  if (process.env.WOMM_E2E_UPDATE_SCREENSHOTS === '1' || !fs.existsSync(ref)) {
    fs.mkdirSync(SHOTS, { recursive: true });
    fs.copyFileSync(file, ref);
  }
}

async function prepare(page: Page, screen: ScreenTitle): Promise<void> {
  await go(page, screen);
  if (screen === 'Run detail') {
    await page.locator('main button[aria-expanded]').first().click();
    await expect(page.getByText('View in source').first()).toBeVisible();
  }
  if (screen === 'Ask WOMM') {
    await page.getByRole('button', { name: 'Who carries the penalty risk?' }).click();
    await expect(page.getByText('Reading the dossier…')).toBeHidden({ timeout: 10_000 });
  }
  if (screen === 'Run pipeline') await expect(page.locator('[data-node="dossier"]')).toContainText('Done');
  // Let fonts and entry animations settle before the screenshot.
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(450);
}

for (const theme of ['light', 'dark'] as const) {
  test(`${theme} theme: tokens, screenshots and a clean console on every screen`, async ({ page, errors }, info) => {
    await finishedRun(page.request, SCENARIOS.sme);
    await openConsole(page, { theme });
    const tokens = TH[theme];
    const root = page.locator('[data-screen-label="WOMM Console"]');
    await expect(root).toHaveAttribute('data-theme', theme);
    const vars = await root.evaluate((el) => {
      const cs = getComputedStyle(el);
      return Object.fromEntries(['bg', 'card', 'soft', 'line', 'ink', 'n1', 'n2', 'accInk', 'accSoft', 'side', 'hl'].map((k) => [k, cs.getPropertyValue(`--${k}`).trim()]));
    });
    expect(vars).toEqual(tokens);

    for (const screen of SCREENS) {
      await prepare(page, screen);
      await expect(page.locator('body')).toHaveCSS('background-color', rgb(tokens.bg));
      await expect(root).toHaveCSS('background-color', rgb(tokens.bg));
      await expect(root).toHaveCSS('color', rgb(tokens.ink));
      await expect(page.locator('header')).toHaveCSS('background-color', rgb(tokens.card));
      await expect(page.locator('aside')).toHaveCSS('background-color', rgb(tokens.side));
      // Cards use the card colour and the line colour.
      const cardEl = page.locator('main div[style*="border-radius: 12px"][style*="background: var(--card)"]').first();
      await expect(cardEl).toHaveCSS('background-color', rgb(tokens.card));
      await expect(cardEl).toHaveCSS('border-top-color', rgb(tokens.line));
      await shoot(page, info, `${theme}-${SLUG[screen]}`);
    }

    // The source panel, with its highlight colour.
    await go(page, 'Run detail');
    await page.getByRole('button', { name: /View in source/ }).first().click();
    const dialog = page.getByRole('dialog', { name: 'Source' });
    await expect(dialog).toHaveCSS('background-color', rgb(tokens.card));
    await expect(dialog.locator('mark')).toHaveCSS('background-color', rgb(tokens.hl));
    await page.waitForTimeout(350);
    await shoot(page, info, `${theme}-source-panel`);
    await page.keyboard.press('Escape');

    errors.expectClean();
  });
}

test('"(c)" renders as three characters, not a copyright sign (ligatures off)', async ({ page }) => {
  await finishedRun(page.request, SCENARIOS.sme);
  await openConsole(page);
  // The global rule turns the features off everywhere, including elements whose inline `font:`
  // shorthand resets them (regression: nav buttons, labels and chips still had ligatures).
  for (const sel of ['body', 'main', 'header', 'aside nav button', 'main button', 'header button']) {
    const el = page.locator(sel).first();
    await expect(el).toHaveCSS('font-variant-ligatures', 'none');
    for (const f of ['liga', 'calt', 'dlig']) await expect(el).toHaveCSS('font-feature-settings', new RegExp(`"${f}" 0`));
  }
  const measure = (inlineFont: boolean) =>
    page.evaluate(async (inline) => {
      await document.fonts.ready;
      const host = document.querySelector('main')!;
      const width = (text: string, features?: string) => {
        const s = document.createElement('span');
        s.textContent = text;
        Object.assign(s.style, { whiteSpace: 'pre', position: 'absolute', display: 'inline-block', left: '0', top: '0' });
        if (inline) s.style.font = "700 48px 'Manrope',sans-serif";
        else Object.assign(s.style, { fontSize: '48px', fontWeight: '700' });
        if (features) {
          s.style.setProperty('font-feature-settings', features, 'important');
          s.style.setProperty('font-variant-ligatures', 'normal', 'important');
        }
        host.appendChild(s);
        const r = s.getBoundingClientRect().width;
        s.remove();
        return r;
      };
      return { text: width('(c)'), parts: width('(') + width('c') + width(')'), copyright: width('©'), withLigatures: width('(c)', 'normal') };
    }, inlineFont);
  for (const inline of [false, true]) {
    const w = await measure(inline);
    test.info().annotations.push({ type: `ligature widths (inline font: ${inline})`, description: JSON.stringify(w) });
    // No ligature: "(c)" is about as wide as its three glyphs (kerning aside), much wider than "©".
    expect(Math.abs(w.text - w.parts) / w.parts, JSON.stringify(w)).toBeLessThan(0.08);
    expect(w.text, JSON.stringify(w)).toBeGreaterThan(w.copyright * 1.3);
    // The font does have the ligature, so this check would catch it coming back.
    expect(w.withLigatures, JSON.stringify(w)).toBeLessThan(w.text * 0.8);
  }
  // And "(c)" in real content (legal point numbering in a source text) is kept as typed.
  await go(page, 'Run detail');
  const card = page.locator('main button[aria-expanded]').first();
  await card.click();
  await page.getByRole('button', { name: /View in source/ }).first().click();
  const text = (await page.getByRole('dialog', { name: 'Source' }).textContent()) ?? '';
  expect(text).not.toContain('©');
});

for (const width of [1024, 820, 600, 390]) {
  test(`no horizontal page scroll at ${width}px on any screen`, async ({ page }) => {
    await finishedRun(page.request, SCENARIOS.sme);
    await page.setViewportSize({ width, height: 900 });
    await openConsole(page);
    for (const screen of SCREENS) {
      await prepare(page, screen);
      const m = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
      expect(m.sw, `${screen} at ${width}px`).toBeLessThanOrEqual(m.cw);
      // Nothing sticks out of the viewport except inside intentionally scrollable panels.
      const offenders = await page.evaluate(() => {
        const vw = document.documentElement.clientWidth;
        const scrollable = (el: Element | null): boolean => {
          for (let e = el; e && e !== document.body; e = e.parentElement) {
            const o = getComputedStyle(e).overflowX;
            if (o === 'auto' || o === 'scroll' || o === 'hidden') return true;
          }
          return false;
        };
        return [...document.querySelectorAll('main *')]
          .filter((el) => el.getBoundingClientRect().right > vw + 1 && !scrollable(el.parentElement))
          .map((el) => `${el.tagName}.${(el.textContent ?? '').slice(0, 40)}`);
      });
      expect(offenders, `${screen} at ${width}px`).toEqual([]);
      // No box's content spills past its own edge (e.g. a stat value wider than its card).
      const spills = await page.evaluate(() =>
        [...document.querySelectorAll('main *')]
          .filter((el) => {
            // Content inside an intentionally scrollable panel may be wider than its box.
            for (let e = el.parentElement; e && e !== document.body; e = e.parentElement) {
              const o = getComputedStyle(e).overflowX;
              if (o === 'auto' || o === 'scroll') return false;
            }
            const cs = getComputedStyle(el);
            if (cs.overflowX !== 'visible' || cs.display === 'inline' || el instanceof SVGElement) return false;
            return el.scrollWidth > el.clientWidth + 1 && el.clientWidth > 0;
          })
          .map((el) => `${el.tagName}.${(el.textContent ?? '').slice(0, 40)} (${el.scrollWidth}>${el.clientWidth})`),
      );
      expect(spills, `${screen} at ${width}px`).toEqual([]);
    }
  });
}
