"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileDown, FileText, RefreshCw } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge, Button, Callout, Card, CardBody, CardHeader, EmptyState, LoadingBlock, statusTone, Table, Td, Th } from "@/components/ui/primitives";
import { api, errorMessage, fileUrl } from "@/lib/api";
import { date, inr, label } from "@/lib/format";
import { useJobRunner } from "@/lib/jobs";
import type { CamReport } from "@/lib/types";

export function CamTab({ appId, hasAssessment }: { appId: number; hasAssessment: boolean }) {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<number | null>(null);
  const hist = useQuery({
    queryKey: ["cam", appId], queryFn: () => api.get<{ items: CamReport[] }>(`/assessments/${appId}/cam`),
    refetchInterval: (q) => (q.state.data?.items.some((r) => r.status === "generating") ? 1500 : false),
  });
  useJobRunner(appId, !!hist.data?.items.some((r) => r.status === "generating"), hist.dataUpdatedAt, [["cam", appId]]);
  const current = selected ?? hist.data?.items.find((r) => r.status === "completed")?.id ?? null;
  const currentReport = hist.data?.items.find((r) => r.id === current);
  const ready = currentReport?.status === "completed";
  // Content only exists once generation has completed; keying on status refetches when it flips.
  const preview = useQuery({ queryKey: ["cam-preview", current, currentReport?.status], queryFn: () => api.get<CamReport>(`/cam/${current}`), enabled: !!current && ready });
  const gen = useMutation({
    mutationFn: () => api.post<{ report: CamReport }>(`/assessments/${appId}/cam`),
    onSuccess: (r) => { toast.success(`CAM v${r.report.version} generation started`); setSelected(r.report.id); qc.invalidateQueries({ queryKey: ["cam", appId] }); qc.invalidateQueries({ queryKey: ["assessment", appId] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const c = (ready && preview.data?.content && "meta" in preview.data.content ? preview.data.content : undefined) as undefined | { meta: { synthetic_notice: string | null }; recommendation: { outcome_label: string; recommended_amount: number; requested_amount: number; indicative_rate: number | null } | null; risk: { overall_score: number; risk_category: string; confidence_level: string } | null; reconciliation: { status: string }[]; documents: unknown[]; review: { pending: boolean } };
  const items = hist.data?.items ?? [];
  const latest = items[0];
  return (
    <div className="space-y-6">
      {!hasAssessment && <Callout tone="warning" title="No credit assessment yet">A CAM can be generated now, but risk and recommendation sections will be marked as not generated. Generate the credit assessment first for a complete memo.</Callout>}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">The CAM renders persisted records only: extracted facts, computed ratios, model outputs, policy assumptions and analyst notes are labelled separately.</p>
        <Button onClick={() => gen.mutate()} loading={gen.isPending || latest?.status === "generating"}>{items.length ? <RefreshCw /> : <FileText />}{items.length ? "Generate new version" : "Generate CAM"}</Button>
      </div>
      {hist.isLoading ? <LoadingBlock /> : items.length === 0 ? <EmptyState icon={<FileText />} title="No CAM generated yet" description="Generate a Credit Appraisal Memo as PDF and DOCX for the reviewer." /> : (
        <div className="grid gap-6 xl:grid-cols-[1fr_360px]">
          <Card>
            <CardHeader title={currentReport ? `Preview — CAM v${currentReport.version}` : "Preview"}
              action={current && ready && <div className="flex gap-2">
                <Button size="sm" asChild><a href={fileUrl(`/cam/${current}/pdf`)} data-testid="download-pdf"><Download />PDF</a></Button>
                <Button size="sm" variant="outline" asChild><a href={fileUrl(`/cam/${current}/docx`)} data-testid="download-docx"><FileDown />DOCX</a></Button>
              </div>} />
            <CardBody className="space-y-4">
              {c && (
                <div className="grid gap-3 sm:grid-cols-4">
                  <div className="rounded-md border p-3"><div className="text-xs text-muted-foreground">Recommendation</div><div className="mt-1 text-sm font-semibold">{c.recommendation?.outcome_label ?? "Not generated"}</div></div>
                  <div className="rounded-md border p-3"><div className="text-xs text-muted-foreground">Recommended / requested</div><div className="num mt-1 text-sm font-semibold">{c.recommendation ? `${inr(c.recommendation.recommended_amount)} / ${inr(c.recommendation.requested_amount)}` : "—"}</div></div>
                  <div className="rounded-md border p-3"><div className="text-xs text-muted-foreground">Risk</div><div className="mt-1 text-sm font-semibold">{c.risk ? `${c.risk.overall_score.toFixed(1)} · ${c.risk.risk_category}` : "Not assessed"}</div></div>
                  <div className="rounded-md border p-3"><div className="text-xs text-muted-foreground">Review status</div><div className="mt-1 text-sm font-semibold">{c.review?.pending === false ? "Reviewed" : "Pending human review"}</div></div>
                </div>
              )}
              {c?.meta?.synthetic_notice && <Callout tone="warning">{c.meta.synthetic_notice}</Callout>}
              {currentReport?.status === "generating" && <LoadingBlock label={`Generating CAM v${currentReport.version} in the background…`} />}
              {currentReport?.status === "failed" && <Callout tone="danger" title="CAM generation failed">{currentReport.error ?? "Unknown error"} — try generating a new version.</Callout>}
              {current && ready && <iframe title="CAM PDF preview" src={fileUrl(`/cam/${current}/pdf?inline=true`)} className="h-[720px] w-full rounded-md border bg-muted" />}
            </CardBody>
          </Card>
          <Card className="h-fit">
            <CardHeader title="Generation history" />
            <Table>
              <thead><tr><Th>Version</Th><Th>Status</Th><Th>Generated</Th></tr></thead>
              <tbody>{items.map((r) => (
                <tr key={r.id} className={`cursor-pointer hover:bg-muted/40 ${r.id === current ? "bg-accent/40" : ""}`} onClick={() => setSelected(r.id)}>
                  <Td className="font-medium">v{r.version}</Td><Td><Badge tone={statusTone(r.status)}>{label(r.status)}</Badge>{r.error && <p className="text-xs text-red-700">{r.error}</p>}</Td><Td className="text-xs text-muted-foreground">{date(r.created_at, true)}</Td>
                </tr>))}</tbody>
            </Table>
          </Card>
        </div>
      )}
    </div>
  );
}
