import { expect, test } from "@playwright/test";

import { ANALYST, login, unique } from "./helpers";

test("unauthenticated users are redirected to sign in", async ({ page }) => {
  await page.goto("/assessments");
  await expect(page).toHaveURL(/\/login\?next=%2Fassessments/);
  const r = await page.request.get("/api/v1/borrowers");
  expect(r.status()).toBe(401);
});

test("login validation, wrong password and password visibility", async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("E-mail is required")).toBeVisible();
  await page.getByLabel("Work e-mail").fill(ANALYST);
  const pw = page.getByLabel("Password", { exact: true });
  await pw.fill("definitely-wrong-1");
  await page.getByRole("button", { name: "Show password" }).click();
  await expect(pw).toHaveAttribute("type", "text");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("Invalid e-mail or password.")).toBeVisible();
});

test("registration creates an isolated organisation with empty states, then sign-out", async ({ page }) => {
  const org = unique("E2E Finance");
  const email = `owner.${Date.now()}@e2e.example`;
  await page.goto("/register");
  await page.getByLabel("Full name").fill("E2E Owner");
  await page.getByLabel("Work e-mail").fill(email);
  await page.getByLabel("Organisation").fill(org);
  await page.getByLabel("Password", { exact: true }).fill("short");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByText("At least 10 characters")).toBeVisible();
  await page.getByLabel("Password", { exact: true }).fill("E2e-Passw0rd-123");
  await page.getByLabel("Confirm password").fill("E2e-Passw0rd-123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByText(org).first()).toBeVisible();
  await expect(page.getByText("No assessments yet")).toBeVisible();
  await page.goto("/borrowers");
  await expect(page.getByText("No borrowers yet")).toBeVisible();
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/);
  expect((await page.request.get("/api/v1/auth/me")).status()).toBe(401);
});

test("analyst sees real dashboard totals", async ({ page }) => {
  await login(page, ANALYST);
  const api = await (await page.request.get("/api/v1/dashboard")).json();
  await expect(page.getByText("Total borrowers").locator("..").locator("..")).toContainText(String(api.totals.borrowers));
  await expect(page.getByText("Awaiting review").first()).toBeVisible();
});
