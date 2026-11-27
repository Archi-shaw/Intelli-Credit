import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatementTable } from "@/components/financials";
import { ConfidenceBadge, OutcomeBadge, StatusBadge } from "@/components/status";
import { EmptyState } from "@/components/ui/primitives";
import { WorkflowStepper } from "@/components/workflow";
import type { Statement } from "@/lib/types";

const stmt = (period: string, items: Record<string, number>): Statement => ({
  id: 1, period_label: period, period_end: null, conflicts: [], source_document_ids: [1],
  line_items: Object.fromEntries(Object.entries(items).map(([k, v]) => [k, { value: v, metric_id: 1, document_id: 1, page_number: 2, confidence: 0.9, verified: false, label: k }])),
});

describe("components", () => {
  it("shows unextracted statement lines as missing rather than zero", () => {
    render(<StatementTable periods={[stmt("FY2022-23", { revenue_from_operations: 2.185e8 }), stmt("FY2023-24", { revenue_from_operations: 2.4604e8, pat: 2.178e7 })]}
      rows={[["revenue_from_operations", "Revenue from operations"], ["pat", "Profit after tax"], ["ebitda", "EBITDA"]]} docNames={{ 1: "fs.pdf" }} />);
    expect(screen.getByText("2,460.40")).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0); // FY2022-23 PAT is missing
    expect(screen.queryByText("0.00")).not.toBeInTheDocument();
    expect(screen.getByText(/Not found in the documents.*EBITDA/)).toBeInTheDocument();
    expect(screen.getAllByText(/fs\.pdf, p\.2/).length).toBeGreaterThan(0);
  });
  it("labels statuses, outcomes and confidence distinctly", () => {
    render(<><StatusBadge status="awaiting_review" /><OutcomeBadge outcome="conditional_approval" /><ConfidenceBadge level="Medium" score={66} /></>);
    expect(screen.getByText("Awaiting review")).toBeInTheDocument();
    expect(screen.getByText("Recommend conditional approval")).toBeInTheDocument();
    expect(screen.getByText("Confidence: Medium (66/100)")).toBeInTheDocument();
  });
  it("renders the nine-step workflow with completion state", () => {
    const steps = ["Select borrower", "Loan request", "Upload documents"].map((n, i) => ({ index: i + 1, name: n, done: i < 2 }));
    render(<WorkflowStepper steps={steps} current={3} />);
    expect(screen.getByText("Upload documents").closest("[aria-current]")).toHaveAttribute("aria-current", "step");
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
  });
  it("renders empty states with an action", () => {
    render(<EmptyState title="No borrowers yet" description="Add your first borrower." action={<button>Add borrower</button>} />);
    expect(screen.getByText("No borrowers yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add borrower" })).toBeInTheDocument();
  });
});
