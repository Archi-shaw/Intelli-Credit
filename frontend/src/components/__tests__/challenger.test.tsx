import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ChallengerCard } from "@/components/assessment/challenger-card";
import type { Challenger } from "@/lib/types";

const ok: Challenger = {
  status: "ok", model_version: "challenger-lgbm-20261127-12af7e39", dataset_type: "synthetic", approved_for_decisions: false,
  note: "Experimental challenger trained on synthetic labels.", evaluation_status: "Evaluated on synthetic data only.",
  probability: 0.027, probability_label: "Estimated 12-month default probability", decision_threshold: 0.2533,
  missing_features: ["receivable_days"],
  top_contributions: [
    { feature: "net_profit_margin", description: "PAT / revenue", value: 2.73, shap_log_odds: 0.412, direction: "raises risk" },
    { feature: "receivable_days", description: "Receivables / revenue × 365", value: null, shap_log_odds: -0.05, direction: "lowers risk" },
  ],
  test_metrics: { lightgbm_calibrated: { roc_auc: 0.83, pr_auc: 0.32, brier: 0.0569 }, policy_scorecard: { roc_auc: 0.814 } },
};

describe("ChallengerCard", () => {
  it("is labelled experimental, synthetic and not used for decisions", () => {
    render(<ChallengerCard c={ok} />);
    expect(screen.getByText("Challenger model (experimental)")).toBeInTheDocument();
    expect(screen.getByText("Not used for decisions")).toBeInTheDocument();
    expect(screen.getByText("Training data: Synthetic")).toBeInTheDocument();
    expect(screen.getByText(/not evidence of real-world performance/)).toBeInTheDocument();
    expect(screen.getByTestId("challenger-probability")).toHaveTextContent("2.7%");
  });
  it("shows missing inputs as missing, never as zero", () => {
    render(<ChallengerCard c={ok} />);
    expect(screen.getByText("missing")).toBeInTheDocument();
    expect(screen.getByText(/Missing inputs \(left missing, never zero-filled\): receivable_days/)).toBeInTheDocument();
  });
  it("reports an unavailable model without affecting the scorecard", () => {
    render(<ChallengerCard c={{ status: "unavailable", detail: "No challenger artifact found." }} />);
    expect(screen.getByText("Challenger model unavailable")).toBeInTheDocument();
    expect(screen.getByText(/The policy scorecard above is unaffected/)).toBeInTheDocument();
    expect(screen.queryByTestId("challenger-probability")).not.toBeInTheDocument();
  });
});
