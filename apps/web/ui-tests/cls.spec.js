import { expect, test } from 'playwright/test';
import { stubApi } from './stub.js';

// Font swap / late mounts must not move content: CLS < 0.01 on /, /stats and /about (guest),
// and the hero h1 stays on the type scale (≤ 64px).
const PATHS = ['/', '/stats', '/about'];
for (const width of [500, 1280]) {
  for (const path of PATHS) {
    test(`CLS < 0.01 on ${path} at ${width}`, async ({ page }, testInfo) => {
      test.skip(testInfo.project.name !== '1280', 'runs its own viewports');
      await page.addInitScript(() => {
        window.__cls = 0; window.__shifts = [];
        new PerformanceObserver((list) => {
          for (const e of list.getEntries()) {
            if (e.hadRecentInput) continue;
            window.__cls += e.value;
            window.__shifts.push({ t: Math.round(e.startTime), v: e.value, nodes: e.sources.map((s) => s.node?.nodeName + '.' + (s.node?.className || '')) });
          }
        }).observe({ type: 'layout-shift', buffered: true });
      });
      await stubApi(page, { signedIn: false });
      await page.setViewportSize({ width, height: 900 });
      await page.goto(path);
      await page.waitForLoadState('networkidle');
      await page.waitForTimeout(1500);
      const r = await page.evaluate(() => ({ cls: window.__cls, shifts: window.__shifts }));
      console.log(`CLS ${path} ${width}`, r.cls.toFixed(4), JSON.stringify(r.shifts));
      expect(r.cls).toBeLessThan(0.01);
      if (path === '/') {
        const size = await page.locator('.hero h1').evaluate((e) => parseFloat(getComputedStyle(e).fontSize));
        expect(size).toBeLessThanOrEqual(64);
      }
    });
  }
}

// Slow web fonts (live-like): with font-display:optional + metric fallbacks nothing reflows late.
for (const width of [500, 1280]) {
  test(`CLS < 0.01 on / at ${width} with fonts delayed 700ms`, async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== '1280', 'runs its own viewports');
    await page.addInitScript(() => {
      window.__cls = 0;
      new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__cls += e.value; }).observe({ type: 'layout-shift', buffered: true });
    });
    await page.route(/\/fonts\/.*\.woff2$/, async (route) => { await new Promise((r) => setTimeout(r, 700)); await route.continue(); });
    await stubApi(page, { signedIn: false });
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/');
    await page.waitForTimeout(2000);
    const cls = await page.evaluate(() => window.__cls);
    console.log(`CLS slow-fonts / ${width}`, cls.toFixed(4));
    expect(cls).toBeLessThan(0.01);
  });
}
