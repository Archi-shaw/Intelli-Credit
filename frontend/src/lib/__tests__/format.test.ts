import { describe, expect, it } from "vitest";

import { date, inr, inrWords, label, lakh, MISSING, pct, ratio } from "@/lib/format";

describe("Indian currency formatting", () => {
  it("uses crore and lakh for large values", () => {
    expect(inr(2.46e8)).toBe("₹24.60 Cr");
    expect(inr(8e6)).toBe("₹80.00 L");
    expect(inr(45_000)).toBe("₹45,000");
    expect(inr(-1.084e7)).toBe("−₹1.08 Cr");
  });
  it("never turns missing values into zero", () => {
    expect(inr(null)).toBe(MISSING);
    expect(inr(undefined)).toBe(MISSING);
    expect(lakh(null)).toBe(MISSING);
    expect(pct(null)).toBe(MISSING);
    expect(ratio(null, "x")).toBe("n/a");
    expect(inr(0)).toBe("₹0");
  });
  it("formats long-form amounts used in reconciliation prose", () => {
    expect(inrWords(8e6)).toBe("₹80.00 lakh");
    expect(inrWords(1.2e6)).toBe("₹12.00 lakh");
    expect(inrWords(6.8e6)).toBe("₹68.00 lakh");
    expect(inrWords(2.46e8)).toBe("₹24.60 crore");
  });
  it("formats lakh tables, ratios and labels", () => {
    expect(lakh(246040000)).toBe("2,460.40");
    expect(ratio(1.256, "x")).toBe("1.26x");
    expect(ratio(12.34, "%")).toBe("12.3%");
    expect(ratio(45.6, "days")).toBe("46 days");
    expect(label("potential_discrepancy")).toBe("Potential discrepancy");
    expect(date("not a date")).toBe(MISSING);
  });
});
