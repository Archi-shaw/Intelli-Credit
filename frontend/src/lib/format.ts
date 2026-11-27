/** Indian-style number and currency formatting. Missing values are shown as missing, never as zero. */

export const MISSING = "—";

export function inr(value: number | null | undefined, opts: { compact?: boolean; missing?: string } = {}): string {
  const { compact = true, missing = MISSING } = opts;
  if (value === null || value === undefined || Number.isNaN(value)) return missing;
  const v = Number(value);
  const sign = v < 0 ? "−" : "";
  const a = Math.abs(v);
  if (compact && a >= 1e7) return `${sign}₹${(a / 1e7).toLocaleString("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 2 })} Cr`;
  if (compact && a >= 1e5) return `${sign}₹${(a / 1e5).toLocaleString("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 2 })} L`;
  return `${sign}₹${a.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

/** Long form used in prose: "₹80.00 lakh", "₹2.46 crore". */
export function inrWords(value: number | null | undefined): string {
  if (value === null || value === undefined) return MISSING;
  const a = Math.abs(value);
  const sign = value < 0 ? "−" : "";
  if (a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(2)} crore`;
  if (a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(2)} lakh`;
  return `${sign}₹${a.toLocaleString("en-IN")}`;
}

export function lakh(value: number | null | undefined): string {
  if (value === null || value === undefined) return MISSING;
  return (value / 1e5).toLocaleString("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 2 });
}

export function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return MISSING;
  return `${value.toFixed(digits)}%`;
}

export function ratio(value: number | null | undefined, unit: string): string {
  if (value === null || value === undefined) return "n/a";
  if (unit === "%") return `${value.toFixed(1)}%`;
  if (unit === "days") return `${value.toFixed(0)} days`;
  return `${value.toFixed(2)}x`;
}

export function label(s: string | null | undefined): string {
  if (!s) return MISSING;
  const t = s.replace(/_/g, " ").trim();
  return t.charAt(0).toUpperCase() + t.slice(1);
}

export function date(iso: string | null | undefined, withTime = false): string {
  if (!iso) return MISSING;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return MISSING;
  return d.toLocaleString("en-IN", withTime
    ? { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }
    : { day: "2-digit", month: "short", year: "numeric" });
}

export function bytes(n: number): string {
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${n} B`;
}

export const LOAN_TYPES: Record<string, string> = {
  term_loan: "Term loan", working_capital: "Working capital", cash_credit: "Cash credit", overdraft: "Overdraft",
  equipment_finance: "Equipment finance", project_finance: "Project finance", letter_of_credit: "Letter of credit",
  bank_guarantee: "Bank guarantee",
};

export const DOC_CATEGORIES: Record<string, string> = {
  auto: "Auto-detect", financial_statements: "Financial statements", bank_statements: "Bank statements",
  gst_returns: "GST returns", income_tax_returns: "Income-tax returns", annual_reports: "Annual report",
  board_minutes: "Board minutes", credit_rating_reports: "Credit rating report", sanction_letters: "Sanction letter",
  shareholding_disclosures: "Shareholding pattern", legal_notices: "Legal notice", other: "Other",
};

export const STATUS_LABELS: Record<string, string> = {
  draft: "Draft", documents_pending: "Documents pending", processing: "Processing documents",
  under_analysis: "Under analysis", awaiting_review: "Awaiting review", recommended_approval: "Recommended approval",
  recommended_rejection: "Recommended rejection", approved: "Approved", rejected: "Rejected",
};

export const OUTCOME_LABELS: Record<string, string> = {
  approve: "Recommend approval", conditional_approval: "Recommend conditional approval", reject: "Recommend rejection",
  insufficient_information: "Insufficient information",
};
