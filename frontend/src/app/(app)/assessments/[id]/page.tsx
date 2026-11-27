"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { Suspense, use, useEffect } from "react";

import { AssistantTab } from "@/components/assessment/assistant-tab";
import { AuditTab } from "@/components/assessment/audit-tab";
import { CamTab } from "@/components/assessment/cam-tab";
import { DiligenceTab } from "@/components/assessment/diligence-tab";
import { DocumentsTab, useDocuments } from "@/components/assessment/documents-tab";
import { FinancialsTab } from "@/components/assessment/financials-tab";
import { RecommendationTab } from "@/components/assessment/recommendation-tab";
import { ReconciliationTab } from "@/components/assessment/reconciliation-tab";
import { RiskTab } from "@/components/assessment/risk-tab";
import { OutcomeBadge, StatusBadge, SyntheticBadge } from "@/components/status";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/overlay";
import { Callout, Card, CardBody, CardHeader, ErrorState, KV, LoadingBlock, PageHeader } from "@/components/ui/primitives";
import { WorkflowStepper } from "@/components/workflow";
import { api } from "@/lib/api";
import { date, inr, label, LOAN_TYPES } from "@/lib/format";
import type { Assessment } from "@/lib/types";

const TABS = [
  ["overview", "Overview"], ["documents", "Documents"], ["financials", "Financial analysis"], ["reconciliation", "Reconciliation"],
  ["risk", "Credit risk"], ["recommendation", "Recommendation & review"], ["diligence", "Due diligence"], ["assistant", "AI assistant"],
  ["cam", "CAM report"], ["audit", "Audit trail"],
] as const;
const STEP_TAB: Record<number, string> = { 1: "overview", 2: "overview", 3: "documents", 4: "documents", 5: "financials", 6: "reconciliation", 7: "risk", 8: "recommendation", 9: "cam" };

function NextAction({ a, go }: { a: Assessment; go: (t: string) => void }) {
  const wf = a.workflow;
  if (!wf) return null;
  const firstOpen = wf.steps.find((s) => !s.done);
  if (!firstOpen) return <Callout tone="success" title="All workflow steps complete">The CAM has been generated and a review decision recorded.</Callout>;
  const hints: Record<number, string> = {
    3: "Upload the borrower's financial statements, GST returns and bank statements.",
    4: "Documents are being processed in the background. Results appear automatically.",
    5: "Review the extracted values and correct any misread figures.",
    6: "Run the cross-source reconciliation checks.",
    7: "Generate the credit-risk assessment and recommendation.",
    8: "A reviewer records the approval decision with a justification.",
    9: "Generate the Credit Appraisal Memo (PDF and DOCX).",
  };
  return (
    <Callout title={`Next: step ${firstOpen.index} — ${firstOpen.name}`}>
      {hints[firstOpen.index] ?? ""} <button className="font-medium underline" onClick={() => go(STEP_TAB[firstOpen.index])}>Go to {label(STEP_TAB[firstOpen.index])}</button>
    </Callout>
  );
}

function Workspace({ id }: { id: number }) {
  const pathname = usePathname();
  const params = useSearchParams();
  const qc = useQueryClient();
  const tab = params.get("tab") ?? "overview";
  const q = useQuery({ queryKey: ["assessment", id], queryFn: () => api.get<Assessment>(`/assessments/${id}`) });
  const docs = useDocuments(id);
  // When background processing finishes, refresh workflow + analysis views.
  const pending = docs.data?.items.filter((d) => ["queued", "processing"].includes(d.processing_status)).length ?? 0;
  useEffect(() => {
    if (pending === 0 && docs.data) for (const k of ["assessment", "financials", "metrics"]) qc.invalidateQueries({ queryKey: [k, id] });
  }, [pending, docs.data, qc, id]);

  // Shallow URL update (no server round-trip); Next.js keeps useSearchParams in sync with history.replaceState.
  const go = (t: string) => window.history.replaceState(null, "", `${pathname}?tab=${t}`);
  if (q.isLoading) return <LoadingBlock />;
  if (q.error || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const a = q.data;
  const closed = ["approved", "rejected"].includes(a.status);
  const docNames = Object.fromEntries((docs.data?.items ?? []).map((d) => [d.id, d.original_filename]));
  const currentStep = a.workflow?.steps.find((s) => !s.done)?.index;

  return (
    <>
      <PageHeader eyebrow={<><Link href="/assessments" className="hover:underline">Assessments</Link> / {a.reference}</>}
        title={<span className="flex flex-wrap items-center gap-3"><Link href={`/borrowers/${a.company_id}`} className="hover:underline">{a.company_name}</Link>{a.company_is_synthetic && <SyntheticBadge />}</span>}
        description={`${LOAN_TYPES[a.loan_type]} · ${inr(a.requested_amount)} requested · ${a.tenure_months} months · applied ${date(a.application_date)}`}
        actions={<div className="flex items-center gap-2"><StatusBadge status={a.status} />{a.workflow?.recommendation && <OutcomeBadge outcome={a.workflow.recommendation.outcome} />}</div>} />
      {a.workflow && <WorkflowStepper steps={a.workflow.steps} current={currentStep} onSelect={(i) => go(STEP_TAB[i])} />}
      <Tabs value={tab} onValueChange={go} className="mt-6">
        <TabsList>{TABS.map(([k, l]) => <TabsTrigger key={k} value={k}>{l}</TabsTrigger>)}</TabsList>
        <TabsContent value="overview" className="space-y-6">
          <NextAction a={a} go={go} />
          {a.company_is_synthetic && <Callout tone="warning" title="Synthetic demonstration case">This borrower and its documents are fictitious. Results illustrate the workflow and must not be read as information about any real business.</Callout>}
          <div className="grid gap-6 lg:grid-cols-2">
            <Card><CardHeader title="Loan request" /><CardBody>
              <KV items={[["Facility", LOAN_TYPES[a.loan_type]], ["Requested amount", inr(a.requested_amount)], ["Tenure", `${a.tenure_months} months`],
                ["Repayment", label(a.repayment_frequency)], ["Moratorium", `${a.moratorium_months} months`], ["Application date", date(a.application_date)]]} />
              <p className="mt-4 border-t pt-3 text-sm"><span className="text-muted-foreground">Purpose: </span>{a.purpose}</p>
            </CardBody></Card>
            <Card><CardHeader title="Collateral & existing borrowings (declared)" /><CardBody className="space-y-3 text-sm">
              {a.collateral.length === 0 ? <p className="text-muted-foreground">No collateral declared (unsecured).</p> :
                a.collateral.map((c, i) => <div key={i} className="flex justify-between"><span>{label(c.type)}{c.description ? ` — ${c.description}` : ""}</span><span className="num font-medium">{inr(c.value)}</span></div>)}
              <div className="border-t pt-3">
                {a.existing_borrowings.length === 0 ? <p className="text-muted-foreground">No existing borrowings declared.</p> :
                  a.existing_borrowings.map((b, i) => <div key={i} className="flex justify-between"><span>{String(b.lender)} · {label(String(b.facility_type))}</span><span className="num">{inr(b.outstanding_amount as number | null)}{b.interest_rate != null ? ` @ ${b.interest_rate}%` : ""}</span></div>)}
              </div>
            </CardBody></Card>
          </div>
          {a.workflow && <Card><CardHeader title="Processing summary" /><CardBody>
            <KV items={[["Documents", `${a.workflow.documents.total} uploaded · ${a.workflow.documents.completed} processed${a.workflow.documents.failed ? ` · ${a.workflow.documents.failed} failed` : ""}`],
              ["Reconciliation", a.workflow.reconciliation_run ? `Run #${a.workflow.reconciliation_run}` : "Not run"],
              ["Credit assessment", a.workflow.assessment_version ? `Version ${a.workflow.assessment_version}` : "Not generated"],
              ["Human review", a.workflow.review ? `${label(a.workflow.review.decision)} on ${date(a.workflow.review.at)}` : "Pending"],
              ["Latest CAM", a.workflow.latest_cam ? `v${a.workflow.latest_cam.version} · ${label(a.workflow.latest_cam.status)}` : "Not generated"]]} />
          </CardBody></Card>}
        </TabsContent>
        <TabsContent value="documents"><DocumentsTab appId={id} closed={closed} /></TabsContent>
        <TabsContent value="financials"><FinancialsTab appId={id} docNames={docNames} closed={closed} /></TabsContent>
        <TabsContent value="reconciliation"><ReconciliationTab appId={id} closed={closed} /></TabsContent>
        <TabsContent value="risk"><RiskTab appId={id} closed={closed} /></TabsContent>
        <TabsContent value="recommendation"><RecommendationTab app={a} /></TabsContent>
        <TabsContent value="diligence"><DiligenceTab appId={id} /></TabsContent>
        <TabsContent value="assistant"><AssistantTab appId={id} /></TabsContent>
        <TabsContent value="cam"><CamTab appId={id} hasAssessment={!!a.workflow?.assessment_version} /></TabsContent>
        <TabsContent value="audit"><AuditTab appId={id} /></TabsContent>
      </Tabs>
    </>
  );
}

export default function AssessmentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <Suspense fallback={<LoadingBlock />}><Workspace id={Number(id)} /></Suspense>;
}
