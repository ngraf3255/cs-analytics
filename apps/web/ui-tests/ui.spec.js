import { readFileSync } from 'node:fs';
import { expect, test } from 'playwright/test';
import { expectUI } from 'lintapple/playwright';

const fixture = name => JSON.parse(readFileSync(new URL(`../src/test/fixtures/${name}`, import.meta.url), 'utf8'));
const OPTIONS = { maps: ['de_ancient', 'de_mirage', 'cs_rush_001'], weapons: ['ak47', 'm4a1_silencer', 'awp', 'glock'], opening_kill_sides: ['ct', 't'] };

/** Stub the API (VITE_API_BASE_URL) so the checks never depend on the live backend. */
async function stubApi(page, { signedIn = false } = {}) {
  if (signedIn) await page.addInitScript(() => localStorage.setItem('csa_has_session', '1'));
  await page.route(/^https:\/\/api-site\.csgooner\.com\//, route => {
    const url = new URL(route.request().url());
    const json = body => route.fulfill({ json: body });
    switch (url.pathname) {
      case '/options': return json(OPTIONS);
      case '/steam/status': return json({ enabled: true, steam: true, upload: true, guest: true });
      case '/me': return signedIn ? json(fixture('me.json')) : json({ account: null });
      case '/predict': return json({ predicted_winner: 't', probabilities: { ct: 0.31, t: 0.69 } });
      case '/matches': return json(fixture('matches.json'));
      case '/matches/summary': return json(fixture('summary_personal.json'));
      case '/steam/sync': return json({ ...fixture('me.json').sync, jobs: [] });
      case '/matches/upload': return json({ jobs: [] });
      default: return route.fulfill({ status: 404, json: { detail: 'not_found' } });
    }
  });
}

test.describe('lintapple UI policy', () => {
  test('guest home', async ({ page }, testInfo) => {
    await stubApi(page);
    await page.goto('/');
    await expect(page.locator('.hero')).toBeVisible();
    await expect(page.locator('#matches')).toBeVisible();
    await expectUI(page, testInfo);
  });

  test('guest home after Predict, model info open', async ({ page }, testInfo) => {
    await stubApi(page);
    await page.goto('/');
    await page.getByRole('button', { name: /^Predict/ }).click();
    await expect(page.locator('.winner-name')).toBeVisible();
    await page.locator('.model-info-pop summary').click();
    await expect(page.locator('.model-info-pop[open]')).toBeVisible();
    await expectUI(page, testInfo);
  });

  test('/stats', async ({ page }, testInfo) => {
    await stubApi(page);
    await page.goto('/stats');
    await expect(page.locator('main h2').first()).toBeVisible();
    await expectUI(page, testInfo);
  });

  test('/about', async ({ page }, testInfo) => {
    await stubApi(page);
    await page.goto('/about');
    await expect(page.locator('main h2').first()).toBeVisible();
    await expectUI(page, testInfo);
  });

  test('404', async ({ page }, testInfo) => {
    await stubApi(page);
    await page.goto('/logout');
    await expect(page.getByRole('region', { name: 'Not found' })).toBeVisible();
    await expectUI(page, testInfo);
  });

  test('signed-in home (stub session)', async ({ page }, testInfo) => {
    await stubApi(page, { signedIn: true });
    await page.goto('/');
    await expect(page.locator('.match-item').first()).toBeVisible();
    await expectUI(page, testInfo);
  });
});
