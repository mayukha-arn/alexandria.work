import { defineConfig } from "@playwright/test";

// Browser tests run against a throwaway backend (seeded organisation, real local model) and the
// exported static site. Build the site first with NEXT_PUBLIC_API_URL=http://127.0.0.1:8123.
export default defineConfig({
  testDir: "e2e",
  workers: 1,
  fullyParallel: false,
  timeout: 240_000,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  use: { baseURL: "http://localhost:3100", headless: true, actionTimeout: 20_000, trace: "retain-on-failure", screenshot: "only-on-failure" },
  webServer: [
    { command: "../.venv/bin/python e2e/serve_backend.py", url: "http://127.0.0.1:8123/health", timeout: 120_000, reuseExistingServer: false },
    { command: "python3 -m http.server 3100 --directory out", url: "http://localhost:3100/login/", timeout: 30_000, reuseExistingServer: false },
  ],
});
