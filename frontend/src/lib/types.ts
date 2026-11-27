export type Role = "admin" | "analyst" | "reviewer";

export interface User { id: number; email: string; full_name: string; title: string | null; is_active: boolean; role: Role | null; last_login_at: string | null }
export interface Me { user: User; role: Role; organization: { id: number; name: string; slug: string; is_demo: boolean }; permissions: string[] }

export interface Borrower {
  id: number; name: string; cin: string | null; pan: string | null; gstin: string | null; industry: string; business_type: string;
  incorporation_date: string | null; registered_address: string | null; headquarters: string | null; annual_turnover: number | null;
  website: string | null; contact_person: string | null; contact_email: string | null; contact_phone: string | null;
  business_description: string | null; is_synthetic: boolean; created_at: string; updated_at: string;
  assessment_count?: number; last_activity?: string | null;
  directors?: { id: number; name: string; din: string | null; designation: string | null }[];
  promoters?: { id: number; name: string; shareholding_pct: number | null; experience_years: number | null }[];
  assessments?: Assessment[];
}

export interface WorkflowStep { index: number; name: string; done: boolean }
export interface Assessment {
  id: number; reference: string; company_id: number; company_name: string; company_is_synthetic: boolean; loan_type: string;
  requested_amount: number; tenure_months: number; purpose: string; repayment_frequency: string; moratorium_months: number;
  existing_borrowings: Record<string, unknown>[]; collateral: { type: string; description?: string; value: number }[];
  application_date: string; status: string; current_step: number; created_by_id: number | null; created_at: string; updated_at: string;
  risk_score?: number | null; risk_category?: string | null; confidence_level?: string | null; recommendation_outcome?: string | null;
  recommended_amount?: number | null;
  workflow?: { steps: WorkflowStep[]; documents: { total: number; queued: number; processing: number; completed: number; failed: number };
    reconciliation_run: number | null; assessment_version: number | null; recommendation: { id: number; outcome: string } | null;
    latest_cam: CamReport | null; review: { decision: string; at: string } | null };
}

export interface Job { id: number; job_type: string; status: string; progress: number; message: string | null; error: string | null; attempts: number }
export interface Doc {
  id: number; application_id: number; category: string; suggested_category: string | null; classification_confidence: number | null;
  original_filename: string; file_kind: string; file_size: number; page_count: number | null; ocr_used: boolean; ocr_confidence: number | null;
  processing_status: string; extraction_status: string; processing_progress: number; error_message: string | null; warnings: string[];
  detected_unit: string | null; detected_periods: string[]; is_synthetic: boolean; created_at: string; processed_at: string | null;
  metric_count: number | null; average_confidence: number | null; latest_job?: Job | null; previewable?: boolean;
}
export interface Metric {
  id: number; document_id: number; metric_key: string; label: string; period_label: string | null; value: number | null;
  effective_value: number | null; text_value: string | null; raw_text: string | null; unit: string | null; page_number: number | null;
  confidence: number; extraction_method: string; source_excerpt: string | null; flags: string[]; verification_status: string;
  corrected_value: number | null; correction_note: string | null; document_name?: string; document_category?: string;
}
export interface Statement { id: number; period_label: string; period_end: string | null; line_items: Record<string, { value: number; metric_id: number; document_id: number; page_number: number | null; confidence: number; verified: boolean; label: string }>; conflicts: Record<string, unknown>[]; source_document_ids: number[] }
export interface Ratio { key: string; name: string; category: string; formula: string; value: number | null; unit: string; status: string; interpretation: string; period?: string | null; missing?: string[] }

export interface Recon {
  id: number; run_number: number; check_type: string; metric: string; period: string | null; source_a_label: string | null; source_a_value: number | null;
  source_b_label: string | null; source_b_value: number | null; difference: number | null; difference_pct: number | null; severity: string; status: string;
  description: string; risk_implication: string | null; confidence: number; suggested_investigation: string | null;
  source_documents: { id: number; name: string | null }[]; details: Record<string, unknown>; review_status: string; comment_count?: number;
}

export interface Factor { key: string; name: string; dimension: string | null; score: number | null; status: string; display_value: string; explanation: string; weight: number; evidence: Record<string, unknown>[] }
export interface Dimension { key: string; name: string; weight: number; score: number; weighted_score: number; data_status: string; factors: Factor[]; explanation: string }
export interface FiveC { name: string; score: number | null; assessment: string; strengths: string[]; risks: string[]; not_assessed: string[] }
export interface RedFlag { severity: string; title: string; detail: string; source: string }
export interface RiskAssessment {
  id: number; version: number; overall_score: number; risk_category: string; confidence_score: number; confidence_level: string;
  data_completeness: number; missing_information: { type: string; item: string; importance: string; message: string }[];
  red_flags: RedFlag[]; five_cs: Record<string, FiveC>; risk_categories: Record<string, { name: string; score: number | null; assessed_factors: number; total_factors: number }>;
  confidence_breakdown: { score: number; level: string; components: Record<string, { score: number; weight: number; detail: string }> };
  ratios: Ratio[]; summary: string; model_label: string; dimensions: Dimension[]; created_at: string;
  challenger?: Challenger | null;
}
export interface Challenger {
  status: "ok" | "unavailable" | "error"; detail?: string; note?: string; model_version?: string; algorithm?: string;
  dataset_type?: string; evaluation_status?: string; approved_for_decisions?: boolean; probability?: number; probability_label?: string;
  decision_threshold?: number; above_threshold?: boolean; missing_features?: string[];
  top_contributions?: { feature: string; description: string; value: number | null; shap_log_odds: number; direction: string }[];
  test_metrics?: Record<string, { roc_auc?: number; pr_auc?: number; brier?: number }>;
}
export interface Recommendation {
  id: number; version: number; outcome: string; outcome_label: string; risk_category: string; requested_amount: number; recommended_amount: number;
  amount_constraints: { key: string; name: string; value: number | null; binding: boolean; formula: string; explanation?: string }[];
  base_rate: number | null; risk_premium: number | null; rate_adjustments: { name: string; value: number; reason?: string }[]; indicative_rate: number | null;
  requested_tenure_months: number; recommended_tenure_months: number | null; tenure_rationale: string | null;
  repayment_capacity: Record<string, unknown> & { cash_available_for_debt_service?: number | null; existing_annual_debt_service?: number | null; proposed_dscr?: number | null; min_dscr?: number; indicative_emi?: number | null; basis?: string | null; existing_service_notes?: string[] };
  collateral_requirements: { type: string; description: string; status: string }[]; repayment_considerations: string[]; conditions_precedent: string[];
  positive_factors: { factor: string; detail?: string }[]; negative_factors: { factor: string; detail?: string }[]; critical_warnings: string[];
  explanation: { headline?: string; why_outcome?: string; why_amount?: string; why_rate?: string; disclaimer?: string; key_positives?: string[]; key_negatives?: string[] };
  created_at: string; disclaimer: string;
}
export interface Decision { id: number; decision: string; reviewer_name: string | null; final_amount: number | null; final_rate: number | null; final_tenure_months: number | null; comments: string; overrides_recommendation: boolean; created_at: string }
export interface Comment { id: number; section: string; entity_type: string | null; entity_id: number | null; body: string; author_name: string | null; created_at: string }
export interface CamReport { id: number; version: number; status: string; error: string | null; has_pdf: boolean; has_docx: boolean; created_at: string; content?: Record<string, unknown> }
export interface Citation { ref: string; type: string; text: string; document_id?: number; document_name?: string | null; page_number?: number | null; period?: string; value?: number; reconciliation_id?: number }
export interface ChatMessage { id: number; role: "user" | "assistant"; content: string; citations: Citation[]; method: string | null; grounded: boolean; created_at: string }
export interface AuditEntry { id: number; action: string; summary: string; entity_type: string | null; entity_id: number | null; application_id: number | null; user_name: string | null; ip_address: string | null; created_at: string; details: Record<string, unknown> }
export interface Observation { id: number; category: string; sub_category: string; title: string; notes: string; rating: number | null; severity: string; risk_impact: string; interpretation: string; reviewer_status: string; observed_on: string; is_synthetic: boolean }
export interface Finding { id: number; title: string; source_name: string; source_url: string | null; source_tier: string; published_at: string | null; retrieved_at: string; category: string; severity: string; sentiment: string; summary: string; verification_status: string; is_synthetic: boolean }
export interface ResearchQuery { id: number; query_text: string; category: string; provider: string; status: string; result_count: number; message: string | null; executed_at: string | null }
