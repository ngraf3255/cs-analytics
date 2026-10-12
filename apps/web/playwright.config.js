import { defineConfig } from 'playwright/test';

// UI-rule guard (lintapple). Runs the production build with the API stubbed in the browser.
export default defineConfig({
  testDir: './ui-tests',
  testMatch: '*.spec.js',
  outputDir: './test-results/ui',
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ...(process.env.GITHUB_ACTIONS ? [['github']] : [])],
  use: { baseURL: 'http://127.0.0.1:4321', colorScheme: 'dark', reducedMotion: 'reduce', locale: 'en-US', timezoneId: 'UTC', deviceScaleFactor: 1 },
  projects: [
    { name: '500', use: { viewport: { width: 500, height: 900 } } },
    { name: '1280', use: { viewport: { width: 1280, height: 900 } } },
  ],
  webServer: { command: 'pnpm exec vite preview --host 127.0.0.1 --port 4321 --strictPort', url: 'http://127.0.0.1:4321', reuseExistingServer: !process.env.CI },
});
