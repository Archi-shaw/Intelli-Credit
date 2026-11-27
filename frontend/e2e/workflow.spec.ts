import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import { ANALYST, caseFile, login, OTHER_ORG_ADMIN, REVIEWER, unique } from "./helpers";

test.describe.configure({ mode: "serial" });

let assessmentUrl = "";
let assessmentId = 0;
const borrowerName = unique("E2E Textile Traders");

test("analyst runs the complete assessment workflow and downloads the CAM", async ({ page }, testInfo) => {
  await login(page, ANALYST);

  // Borrower
  await page.getByRole("link", { name: "Borrowers" }).click();
  await page.getByRole("button", { name: "Add borrower" }).click();
  await page.getByLabel("Legal name").fill(borrowerName);
  await page.getByLabel("Industry / sector").fill("Textiles");
  await page.getByLabel("PAN").fill("12345");
  await page.getByRole("button", { name: "Create borrower" }).click();
  await expect(page.getByText("PAN must look like ABCDE1234F")).toBeVisible();
  await page.getByLabel("PAN").fill("");
  await page.getByRole("button", { name: "Create borrower" }).click();
  await expect(page).toHaveURL(/\/borrowers\/\d+$/);
  await expect(page.getByRole("heading", { name: borrowerName })).toBeVisible();

  // Steps 1-2: loan request
  await page.getByRole("link", { name: "New assessment" }).last().click();
  await expect(page.getByText("Step 2 · Loan request")).toBeVisible();
  await page.getByLabel("Facility type").selectOption("working_capital");
  await page.getByLabel("Requested amount (₹)").fill("15000000");
  await page.getByLabel("Tenure (months)").fill("12");
  await page.getByLabel("Purpose of the facility").fill("Working capital for seasonal fabric procurement (E2E test)");
  await page.getByRole("button", { name: "Add collateral" }).click();
  await page.getByLabel("Collateral type").selectOption("inventory");
  await page.getByLabel("Collateral value").fill("14000000");
  await page.getByRole("button", { name: /Create assessment/ }).click();
  await expect(page).toHaveURL(/\/assessments\/\d+\?tab=documents/);
  assessmentUrl = page.url().split("?")[0];
  assessmentId = Number(assessmentUrl.split("/").pop());

  // Step 3: invalid file rejected, then real uploads (auto-classified)
  const bogus = testInfo.outputPath("fake.pdf");
  fs.writeFileSync(bogus, "this is not really a PDF");
  await page.getByTestId("file-input").setInputFiles(bogus);
  await expect(page.getByText(/File content does not match its '.pdf' extension/)).toBeVisible();
  await page.getByTestId("file-input").setInputFiles([caseFile("inconsistent", "financial_statements"), caseFile("inconsistent", "bank_statement"), caseFile("inconsistent", "gst_returns")]);

  // Step 4: asynchronous processing — the list polls until every document completes
  await expect(page.locator('[data-testid^="doc-row-"]')).toHaveCount(3);
  await expect.poll(async () => {
    const r = await page.request.get(`/api/v1/assessments/${assessmentId}/documents`);
    return (await r.json()).items.map((d: { processing_status: string }) => d.processing_status).join(",");
  }, { timeout: 90_000 }).toBe("completed,completed,completed");
  const docs = (await (await page.request.get(`/api/v1/assessments/${assessmentId}/documents`)).json()).items;
  expect(docs.map((d: { category: string }) => d.category).sort()).toEqual(["bank_statements", "financial_statements", "gst_returns"]);
  await expect(page.getByText("Extracted").first()).toBeVisible();

  // Document detail with page-referenced values
  await page.getByRole("button", { name: /inconsistent_financial_statements/ }).first().click();
  await expect(page.getByText(/Extracted financial values \(\d+\)/)).toBeVisible();
  await expect(page.getByRole("dialog").getByText("Revenue from operations").first()).toBeVisible();
  await page.keyboard.press("Escape");

  // Step 5: financial analysis
  await page.getByRole("tab", { name: "Financial analysis" }).click();
  await expect(page.getByText("Revenue (FY2023-24)")).toBeVisible();
  await expect(page.getByText("₹9.85 Cr").first()).toBeVisible();
  await expect(page.getByText("GST turnover vs bank business credits")).toBeVisible();

  // Step 6: reconciliation
  await page.getByRole("tab", { name: "Reconciliation" }).click();
  await page.getByRole("button", { name: "Run reconciliation", exact: true }).click();
  const gst = page.getByTestId("recon-gst_vs_bank");
  await expect(gst).toBeVisible();
  await expect(gst).toContainText("Potential discrepancy");
  await expect(gst).toContainText("Difference:");
  await expect(page.getByTestId("recon-circular_transactions")).toContainText("Unusual pattern");
  await expect(page.getByText(/fraud/i)).toHaveCount(0);
  await gst.getByRole("combobox").selectOption("under_review");
  await gst.getByLabel("Comment on finding").fill("Requested statements for other bank accounts (E2E).");
  await gst.getByRole("button", { name: "Add note" }).click();
  await expect(gst.getByText("Requested statements for other bank accounts (E2E).")).toBeVisible();

  // Step 7: credit risk
  await page.getByRole("tab", { name: "Credit risk" }).click();
  await page.getByRole("button", { name: "Generate credit assessment", exact: true }).click();
  await expect(page.getByTestId("risk-score")).toBeVisible();
  await expect(page.getByText("Confidence indicator (data quality)")).toBeVisible();
  await expect(page.getByText("Nine scoring dimensions")).toBeVisible();
  await expect(page.getByText("Five Cs of Credit")).toBeVisible();

  // Step 8: explainable recommendation
  await page.getByRole("tab", { name: "Recommendation & review" }).click();
  await expect(page.getByText("Decision support — not an approval")).toBeVisible();
  const rec = (await (await page.request.get(`/api/v1/assessments/${assessmentId}/recommendation`)).json()).recommendation;
  expect(rec.recommended_amount).toBeLessThanOrEqual(rec.requested_amount);
  await expect(page.getByTestId("requested-amount")).toHaveText("₹1.50 Cr");
  await expect(page.getByText("You prepared this assessment").or(page.getByText("Only reviewers and administrators"))).toBeVisible();

  // AI assistant with citations
  await page.getByRole("tab", { name: "AI assistant" }).click();
  await page.getByRole("button", { name: "What was the revenue in FY 2023-24?" }).click();
  await expect(page.getByText(/9\.85 crore/)).toBeVisible();
  await expect(page.getByLabel("Sources").first()).toContainText("inconsistent_financial_statements_FY2023-24.pdf, page 1");
  await expect(page.getByText("Source-supported").first()).toBeVisible();

  // Step 9: CAM generation + download
  await page.getByRole("tab", { name: "CAM report" }).click();
  await page.getByRole("button", { name: "Generate CAM", exact: true }).click();
  const dl = page.getByTestId("download-pdf");
  await expect(dl).toBeVisible({ timeout: 60_000 });
  const [download] = await Promise.all([page.waitForEvent("download"), dl.click()]);
  const pdfPath = testInfo.outputPath("cam.pdf");
  await download.saveAs(pdfPath);
  expect(fs.readFileSync(pdfPath).subarray(0, 4).toString()).toBe("%PDF");
  const [docx] = await Promise.all([page.waitForEvent("download"), page.getByTestId("download-docx").click()]);
  expect(docx.suggestedFilename()).toMatch(/\.docx$/);

  // The PDF's figures match the database records (text extracted with PyMuPDF from the backend venv).
  const py = process.env.PYTHON_BIN ?? path.resolve(__dirname, "../../backend/.venv/bin/python");
  const text = execFileSync(py, ["-c", "import fitz,sys; print(''.join(p.get_text() for p in fitz.open(sys.argv[1])))", pdfPath]).toString();
  const amt = rec.recommended_amount as number;
  const expectedAmt = amt >= 1e7 ? `₹${(amt / 1e7).toFixed(2)} Cr` : amt >= 1e5 ? `₹${(amt / 1e5).toFixed(2)} L` : `₹${amt.toFixed(0)}`;
  expect(text).toContain(borrowerName);
  expect(text).toContain(expectedAmt);
  expect(text).toContain("985.20"); // revenue FY2023-24 in ₹ lakh, as extracted
  expect(text).toContain("Pending authorised human review");

  // Audit trail
  await page.getByRole("tab", { name: "Audit trail" }).click();
  for (const action of ["assessment.created", "document.uploaded", "document.processed", "reconciliation.run", "assessment.generated", "cam.generated", "cam.downloaded"]) {
    await expect(page.getByText(action, { exact: true }).first()).toBeVisible();
  }
});

test("reviewer records an approval decision with justification", async ({ page }) => {
  test.skip(!assessmentId, "depends on the workflow test");
  await login(page, REVIEWER);
  await page.goto(`${assessmentUrl}?tab=recommendation`);
  await page.getByLabel("Decision").selectOption("approved");
  await page.getByLabel("Justification").fill("Approved subject to verification of GST vs bank variance and stock audit (E2E).");
  await page.getByRole("button", { name: "Record decision" }).click();
  await expect(page.getByText("Review decision recorded")).toBeVisible();
  await expect(page.getByText("Approved", { exact: true }).first()).toBeVisible();
  const final = await (await page.request.get(`/api/v1/assessments/${assessmentId}/final`)).json();
  expect(final.final_status).toBe("approved");
  expect(final.review_decisions[0].final_amount).toBeLessThanOrEqual(final.application.requested_amount);
  await page.getByRole("tab", { name: "Audit trail" }).click();
  await expect(page.getByText("review.approved", { exact: true })).toBeVisible();
});

test("another organisation cannot open the assessment", async ({ page }) => {
  test.skip(!assessmentId, "depends on the workflow test");
  await login(page, OTHER_ORG_ADMIN);
  await page.goto(`${assessmentUrl}?tab=overview`);
  await expect(page.getByText("Credit application not found.")).toBeVisible();
  expect((await page.request.get(`/api/v1/assessments/${assessmentId}/cam`)).status()).toBe(404);
  await page.goto("/borrowers");
  await page.getByLabel("Search borrowers").fill(borrowerName);
  await expect(page.getByText("No borrowers match your filters")).toBeVisible();
});
