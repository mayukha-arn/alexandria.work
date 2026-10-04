import { defineConfig } from "@playwright/test";

// Smoke test against the deployed site: npx playwright test -c playwright.live.config.ts
export default defineConfig({
  testDir: "e2e-live",
  workers: 1,
  timeout: 300_000,
  expect: { timeout: 20_000 },
  reporter: [["list"]],
  use: { baseURL: process.env.LIVE_URL ?? "https://www.the-only-one-who-knew-this-left-in-2019.work", headless: true, actionTimeout: 30_000 },
});
