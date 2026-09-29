// The webServer (playwright.config.ts) created a fresh database and started the API on it.
// Check that it really is fresh and that the console bundle is being served.
import { request, type FullConfig } from '@playwright/test';

export default async function globalSetup(config: FullConfig): Promise<void> {
  const baseURL = config.projects[0].use.baseURL!;
  const ctx = await request.newContext({ baseURL, extraHTTPHeaders: { Authorization: `Bearer ${process.env.WOMM_E2E_TOKEN}` } });
  try {
    const runs = await ctx.get('/runs?limit=1');
    if (!runs.ok()) throw new Error(`GET /runs answered ${runs.status()}`);
    const body = (await runs.json()) as { runs: unknown[] };
    if (body.runs.length) throw new Error(`database ${process.env.WOMM_E2E_DATABASE} is not empty`);
    const index = await ctx.get('/');
    if (!(await index.text()).includes('<div id="root">')) throw new Error('the API does not serve web/dist at /');
  } finally {
    await ctx.dispose();
  }
}
