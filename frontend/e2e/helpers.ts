import { expect, type Page } from "@playwright/test";
import path from "node:path";

/** Seeded demo accounts (see backend/app/seed/demo.py; password overridable with DEMO_PASSWORD). */
export const DEMO_PASSWORD = process.env.DEMO_PASSWORD ?? "IntelliDemo2026!";
export const ANALYST = "analyst@meridian.example";
export const REVIEWER = "reviewer@meridian.example";
export const OTHER_ORG_ADMIN = "admin@northwind.example";

export const SYNTHETIC = path.resolve(__dirname, "../../data/synthetic");
export const caseFile = (key: string, kind: "financial_statements" | "bank_statement" | "gst_returns") =>
  path.join(SYNTHETIC, key, `${key}_${kind}_FY2023-24.${kind === "financial_statements" ? "pdf" : "csv"}`);

export async function login(page: Page, email: string, password = DEMO_PASSWORD) {
  await page.goto("/login");
  await page.getByLabel("Work e-mail").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { name: /Welcome/ })).toBeVisible();
}

export const unique = (prefix: string) => `${prefix} ${Date.now().toString(36).toUpperCase()}`;
