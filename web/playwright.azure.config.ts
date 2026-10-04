import { defineConfig } from "@playwright/test";

// The same browser suite, but against a backend that is ALREADY running somewhere else (e.g. a throwaway copy on
// the Azure VM, reached through `ssh -L 8123:127.0.0.1:8123 ...`). Only the static site is started locally.
//   ssh -f -N -L 8123:127.0.0.1:8123 vm ; npx playwright test -c playwright.azure.config.ts
export default defineConfig({
  testDir: "e2e",
  workers: 1,
  fullyParallel: false,
  timeout: 420_000,                         // the model is slower on a CPU VM
  expect: { timeout: 20_000 },
  reporter: [["list"]],
  use: { baseURL: "http://localhost:3100", headless: true, actionTimeout: 30_000, trace: "retain-on-failure", screenshot: "only-on-failure" },
  webServer: [{ command: "python3 -m http.server 3100 --directory out", url: "http://localhost:3100/login/", timeout: 30_000, reuseExistingServer: false }],
});
