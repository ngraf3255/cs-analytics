import { expect, test } from 'playwright/test';
import { expectUI } from 'lintapple/playwright';
import { stubApi } from './stub.js';

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
