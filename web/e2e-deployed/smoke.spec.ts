import { expect, test } from "@playwright/test";

test("the deployed site loads, reaches its API through the tunnel, and refuses a bad login", async ({ page }) => {
  const problems: string[] = [];
  page.on("console", (m) => { if (m.type() === "error") problems.push(m.text()); });
  await page.goto("/login/");
  await expect(page.getByRole("heading", { name: "Alexandria" })).toBeVisible();
  await page.getByLabel("Username").fill("nobody");
  await page.getByLabel("Password").fill("definitely not a password");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByTestId("error")).toHaveText("invalid credentials");      // answered by the real backend
  expect(problems.filter((p) => /CORS|CSP|Content Security Policy|Refused/i.test(p))).toEqual([]);
});

test("protected pages send visitors to sign in", async ({ page }) => {
  await page.goto("/pings/");
  await expect(page).toHaveURL(/\/login\//);
});
