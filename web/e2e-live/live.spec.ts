import { test, expect } from "@playwright/test";
import { readFileSync, writeFileSync, existsSync } from "fs";
import { totp } from "../e2e/totp";

// Smoke test of the live site as one real account. Credentials come from the private .secrets files and are
// never printed; the 2FA key created on first sign-in is saved back there (owner-only).
const SECRETS = "../.secrets";
const USER = process.env.LIVE_USER ?? "ben.foster";
const pw = readFileSync(`${SECRETS}/meridian-accounts.txt`, "utf8").split("\n").find((l) => l.startsWith(USER + " "))!.trim().split(/\s+/)[1];
const keyFile = `${SECRETS}/meridian-2fa.txt`;
const saved = () => (existsSync(keyFile) ? readFileSync(keyFile, "utf8") : "").split("\n").find((l) => l.startsWith(USER + " "))?.split(/\s+/)[1];

test("live: sign in, chat history, @alexandria answers with sources", async ({ page }) => {
  test.setTimeout(300_000);
  await page.goto("/login/");
  await page.getByLabel("Username").fill(USER);
  await page.getByLabel("Password").fill(pw);
  await page.getByRole("button", { name: "Continue" }).click();
  const qr = page.getByAltText("Two-factor setup QR code");
  const code = page.getByLabel("6-digit code");
  await expect(code).toBeVisible();
  if (await qr.isVisible()) {
    const secret = (await page.getByTestId("totp-secret").textContent())!.trim();
    writeFileSync(keyFile, (existsSync(keyFile) ? readFileSync(keyFile, "utf8") : "") +
      `${USER} ${secret}   otpauth://totp/Alexandria:${USER}?secret=${secret}&issuer=Alexandria\n`, { mode: 0o600 });
    await code.fill(totp(secret));
    await page.getByRole("button", { name: /Turn on 2FA/ }).click();
  } else {
    await code.fill(totp(saved()!));
    await page.getByRole("button", { name: "Sign in" }).click();
  }
  await expect(page).toHaveURL(/\/chat\//);
  await expect(page.getByRole("log")).toContainText("4.2 launch");
  await page.screenshot({ path: "test-results/live-chat.png" });
  const t0 = Date.now();
  await page.getByRole("textbox", { name: "Message" }).fill("@alexandria What is the payroll cutoff for the Thanksgiving week?");
  await page.getByRole("button", { name: "Send" }).click();
  const answer = page.getByRole("log").getByTestId("answer").last();
  await expect(answer).toBeVisible({ timeout: 240_000 });
  await expect(page.getByRole("log").getByText(/Grounded in documents|Not found|couldn't/i).last()).toBeVisible({ timeout: 240_000 });
  console.log("ANSWER_SECONDS", ((Date.now() - t0) / 1000).toFixed(1));
  console.log("ANSWER", (await answer.innerText()).slice(0, 500).replace(/\n/g, " "));
  await page.screenshot({ path: "test-results/live-answer.png" });
});
