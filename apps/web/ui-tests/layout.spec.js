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

// GD #67: pair/tag/ⓘ geometry at phone and desktop widths.
for (const width of [500, 1280]) {
  test(`share/stats pair, example tag, ⓘ pin at ${width}`, async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== '1280', 'runs its own viewports');
    const box = async (loc) => { const b = await loc.boundingBox(); return { x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height), r: Math.round(b.x + b.width), b: Math.round(b.y + b.height) }; };
    const out = {};
    // Guest: Example tag fully inside the first stats row; ⓘ pinned; note under the icon.
    await stubApi(page, { signedIn: false });
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/');
    const tag = await box(page.locator('.example-tag'));
    const cell = await box(page.locator('.teaser-report .report-header > div').first());
    const dl = await box(page.locator('.teaser-report .report-header'));
    out.tag = tag; out.row = { top: cell.y, bottom: cell.b, right: dl.r };
    expect(tag.y).toBeGreaterThan(cell.y);
    expect(tag.b).toBeLessThanOrEqual(cell.b);
    expect(tag.r).toBeLessThan(dl.r);
    await page.getByRole('button', { name: /^Predict/ }).click();
    const info = page.locator('.why-row .model-info-pop > summary');
    await expect(info).toBeVisible();
    await page.waitForTimeout(600); // readout entrance settles
    const closed = await box(info);
    await page.locator('.why-round > summary').click();
    const opened = await box(info);
    out.info = { closedX: closed.x, openX: opened.x, closedY: closed.y, openY: opened.y };
    expect(opened.x).toBe(closed.x);
    expect(opened.y).toBe(closed.y);
    await info.click();
    const note = await box(page.locator('.why-row .tl-legend'));
    out.note = { x: note.x, r: note.r, iconX: opened.x };
    expect(note.r).toBeLessThanOrEqual(width - 12);
    if (opened.x + note.w <= width - 12) expect(note.x).toBe(opened.x);
    // Signed in: Share and Stats one size, same bottoms.
    await stubApi(page, { signedIn: true });
    await page.goto('/');
    await expect(page.locator('.match-item').first()).toBeVisible();
    const share = await box(page.locator('.home-tile-actions .share-button'));
    const stats = await box(page.locator('.home-tile-actions a', { hasText: 'Stats' }));
    out.share = share; out.stats = stats;
    expect(share.h).toBe(stats.h);
    expect(share.w).toBe(stats.w);
    expect(share.b).toBe(stats.b);
    testInfo.annotations.push({ type: `${width}px`, description: JSON.stringify(out) });
    console.log(width, JSON.stringify(out));
  });
}
