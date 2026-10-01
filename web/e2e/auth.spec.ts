// Bearer-token prompt: missing, wrong and correct tokens; persistence across reloads.
import { TOKEN, expect, screenTitle, test } from './helpers';

test.describe('auth', () => {
  test('no token shows the prompt and nothing else loads', async ({ page }) => {
    const calls: string[] = [];
    page.on('request', (r) => r.url().includes('/runs') && calls.push(r.url()));
    await page.goto('/');
    const dialog = page.getByRole('dialog', { name: 'Connect to the WOMM API' });
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText('Connect to the API');
    await expect(dialog).toContainText('WOMM_API_TOKEN');
    await expect(dialog.getByLabel('API token')).toBeFocused();
    await expect(page.locator('header button', { hasText: /^Run$/ })).toBeDisabled();
    expect(calls).toEqual([]);
  });

  test('a wrong token is rejected with a 401 and the prompt asks again', async ({ page }) => {
    await page.goto('/');
    const dialog = page.getByRole('dialog', { name: 'Connect to the WOMM API' });
    await dialog.getByLabel('API token').fill('definitely-not-the-token');
    const rejected = page.waitForResponse((r) => r.status() === 401);
    await dialog.getByRole('button', { name: 'Connect' }).click();
    await rejected;
    await expect(dialog).toContainText('Token rejected');
    await expect(dialog).toContainText('The API answered 401');
    expect(await page.evaluate(() => localStorage.getItem('womm.token'))).toBeNull();
  });

  test('the correct token opens the console and survives a reload', async ({ page }) => {
    await page.goto('/');
    const dialog = page.getByRole('dialog', { name: 'Connect to the WOMM API' });
    // Empty input does nothing.
    await dialog.getByRole('button', { name: 'Connect' }).click();
    await expect(dialog).toBeVisible();
    await dialog.getByLabel('API token').fill(TOKEN);
    await dialog.getByLabel('API token').press('Enter');
    await expect(dialog).toBeHidden();
    await expect(screenTitle(page)).toHaveText('Overview');
    expect(await page.evaluate(() => localStorage.getItem('womm.token'))).toBe(TOKEN);

    await page.reload();
    await expect(page.locator('header')).toBeVisible();
    await expect(page.getByRole('dialog', { name: 'Connect to the WOMM API' })).toHaveCount(0);
    await expect(page.locator('aside')).toContainText('WOMM');
  });
});
