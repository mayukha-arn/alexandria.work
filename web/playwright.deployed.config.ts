import { defineConfig } from "@playwright/test";
// Checks a DEPLOYED site (no local servers): DEPLOYED_URL=https://... npx playwright test -c playwright.deployed.config.ts
export default defineConfig({ testDir: "e2e-deployed", workers: 1, timeout: 60_000, use: { baseURL: process.env.DEPLOYED_URL, headless: true, actionTimeout: 20_000 } });
