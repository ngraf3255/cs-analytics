import { expect, test } from 'playwright/test';
import { stubApi } from './stub.js';

// One page gutter: no horizontal overflow, and every top-level section card shares the same left
// and right edges (GD #66). Runs its own viewports, so only once (in the 1280 project).
const WIDTHS = [360, 500, 508, 760, 1280];
const SECTIONS = ['.workspace', '#matches .steam-card', '#matches .matches-teaser'];

for (const signedIn of [false, true]) {
  test(`page gutter and edges (${signedIn ? 'signed in' : 'guest'})`, async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== '1280', 'runs its own viewports');
    await stubApi(page, { signedIn });
    for (const width of WIDTHS) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto('/');
      await expect(page.locator('#matches')).toBeVisible();
      if (signedIn) await expect(page.locator('.match-item').first()).toBeVisible();
      await page.waitForTimeout(200);
      const m = await page.evaluate((sections) => {
        const d = document.documentElement;
        const boxes = sections.map((sel) => {
          const els = [...document.querySelectorAll(sel)].filter((e) => e.getBoundingClientRect().width > 0);
          if (!els.length) return null;
          const rects = els.map((e) => e.getBoundingClientRect());
          return { sel, left: Math.round(Math.min(...rects.map((r) => r.left))), right: Math.round(Math.max(...rects.map((r) => r.right))) };
        }).filter(Boolean);
        const hero = document.querySelector('.hero')?.getBoundingClientRect();
        return { scrollWidth: d.scrollWidth, clientWidth: d.clientWidth, boxes, heroRight: hero ? Math.round(hero.right) : null };
      }, SECTIONS);
      testInfo.annotations.push({ type: `${width}px`, description: JSON.stringify(m) });
      expect(m.scrollWidth, `scrollWidth at ${width}`).toBe(m.clientWidth);
      if (m.heroRight !== null) expect(m.heroRight, `hero divider reaches the edge at ${width}`).toBe(m.clientWidth);
      expect(m.boxes.length).toBeGreaterThan(1);
      for (const box of m.boxes) {
        expect(box.left, `${box.sel} left at ${width}`).toBe(m.boxes[0].left);
        expect(box.right, `${box.sel} right at ${width}`).toBe(m.boxes[0].right);
      }
    }
  });
}
