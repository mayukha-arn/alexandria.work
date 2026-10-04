import { expect, test, type Page } from "@playwright/test";
import jsQR from "jsqr";
import { PNG } from "pngjs";
import { totp } from "./totp";

const PW = "correct horse battery";

async function startLogin(page: Page) {
  await page.goto("/login/");
  await page.getByLabel("Username").fill("qa");
  await page.getByLabel("Password").fill(PW);
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByAltText("Two-factor setup QR code")).toBeVisible();
}

/** Read the QR code the way a phone does: from the pixels on screen. */
async function scan(page: Page): Promise<string> {
  const png = PNG.sync.read(await page.getByAltText("Two-factor setup QR code").screenshot());
  const found = jsQR(new Uint8ClampedArray(png.data), png.width, png.height);
  if (!found) throw new Error("the QR code could not be read from the screen");
  return found.data;
}

const manualKey = async (page: Page) => (await page.getByTestId("totp-secret").textContent())!.trim();

test.describe.serial("the 2FA setup QR code", () => {
  test("scans to a valid authenticator link that matches the manual key", async ({ page }) => {
    await startLogin(page);
    const uri = new URL(await scan(page));
    expect(uri.protocol).toBe("otpauth:");
    expect(uri.hostname).toBe("totp");
    expect(decodeURIComponent(uri.pathname)).toBe("/Alexandria:qa");
    expect(uri.searchParams.get("issuer")).toBe("Alexandria");
    expect(uri.searchParams.get("secret")).toBe(await manualKey(page));
  });

  test("the same QR code is still valid after leaving and signing in again before turning 2FA on", async ({ page }) => {
    await startLogin(page);
    const first = new URL(await scan(page)).searchParams.get("secret");     // you scan this with your phone ...
    await page.getByRole("button", { name: /Use a different account/ }).click();
    await startLogin(page);                                                  // ... then come back (reload, back button, re-login)
    const second = new URL(await scan(page)).searchParams.get("secret");
    expect(second).toBe(first);                                              // so the code your phone shows still works
  });

  test("a wrong code shows help, and a new QR code can be requested on purpose", async ({ page }) => {
    await startLogin(page);
    const before = new URL(await scan(page)).searchParams.get("secret");
    await page.getByLabel("6-digit code").fill("000000");
    await page.getByRole("button", { name: /Turn on 2FA/ }).click();
    await expect(page.getByText("Code not accepted?")).toBeVisible();
    await expect(page.getByText(/phone's date and time are set to automatic/)).toBeVisible();
    await page.getByRole("button", { name: "show a new QR code" }).click();
    await expect.poll(async () => new URL(await scan(page)).searchParams.get("secret")).not.toBe(before);
    expect(new URL(await scan(page)).searchParams.get("secret")).toBe(await manualKey(page));   // QR and manual key agree
  });

  test("the QR code is large and contrasty enough to scan from a screen", async ({ page }) => {
    await startLogin(page);
    const box = (await page.getByAltText("Two-factor setup QR code").boundingBox())!;
    expect(box.width).toBeGreaterThanOrEqual(280);
  });

  test("the code a phone would show after scanning completes enrollment", async ({ page }) => {
    await startLogin(page);
    const secret = new URL(await scan(page)).searchParams.get("secret")!;    // only what the QR contained
    await page.getByLabel("6-digit code").fill(totp(secret));
    await page.getByRole("button", { name: /Turn on 2FA/ }).click();
    await expect(page).toHaveURL(/\/chat\//);
  });
});
