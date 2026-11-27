"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Pencil, X } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { BS_ROWS, CF_ROWS, GstBankChart, PL_ROWS, RatioTable, StatementTable, TrendChart } from "@/components/financials";
import { Badge, Button, Callout, Card, CardBody, CardHeader, EmptyState, ErrorState, Input, LoadingBlock, Stat, Table, Td, Th } from "@/components/ui/primitives";
import { api, errorMessage } from "@/lib/api";
import { inr, lakh, label } from "@/lib/format";
import { useCan } from "@/lib/session";
import type { Metric, Ratio, Statement } from "@/lib/types";

interface Fin {
  periods: Statement[]; trend: Record<string, number | string | null>[]; ratios: (Ratio & { period: string | null })[];
  bank_monthly: { month: string; credits: number; debits: number; business_credits: number; count: number }[];
  gst_monthly: { month: string; GSTR3B?: number | null; GSTR1?: number | null }[];
  counts: { bank_transactions: number; gst_records: number; periods: number };
}

function MetricReview({ appId, closed }: { appId: number; closed: boolean }) {
  const qc = useQueryClient();
  const canWrite = useCan("analysis:run");
  const q = useQuery({ queryKey: ["metrics", appId], queryFn: () => api.get<{ items: Metric[] }>(`/assessments/${appId}/metrics`) });
  const [editing, setEditing] = useState<number | null>(null);
  const [value, setValue] = useState("");
  const [note, setNote] = useState("");
  const m = useMutation({
    mutationFn: (p: { id: number; body: Record<string, unknown> }) => api.patch<Metric>(`/metrics/${p.id}`, p.body),
    onSuccess: () => { toast.success("Figure updated — statements re-consolidated"); setEditing(null); for (const k of ["metrics", "financials"]) qc.invalidateQueries({ queryKey: [k, appId] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const rows = useMemo(() => (q.data?.items ?? []).filter((x) => x.document_category === "financial_statements" || x.document_category === "annual_reports" || x.document_category === "sanction_letters" || x.document_category === "credit_rating_reports"), [q.data]);
  if (q.isLoading) return <LoadingBlock />;
  if (!rows.length) return <p className="text-sm text-muted-foreground">No statement figures extracted yet.</p>;
  return (
    <Table>
      <thead><tr><Th>Metric</Th><Th>Period</Th><Th className="text-right">Extracted</Th><Th className="text-right">Effective</Th><Th>Source</Th><Th>Confidence</Th><Th>Status</Th>{canWrite && !closed && <Th className="text-right">Review</Th>}</tr></thead>
      <tbody>{rows.map((r) => (
        <tr key={r.id}>
          <Td className="font-medium">{r.label}<div className="text-[11px] text-muted-foreground">{r.metric_key}</div></Td>
          <Td>{r.period_label ?? <Badge tone="warning">unknown</Badge>}</Td>
          <Td className="num text-right">{r.text_value ?? lakh(r.value)}</Td>
          <Td className="num text-right">{editing === r.id ? <Input aria-label="Corrected value in rupees" value={value} onChange={(e) => setValue(e.target.value)} className="h-8 w-36 text-right" /> : r.text_value ?? lakh(r.effective_value)}</Td>
          <Td className="max-w-[200px] text-xs text-muted-foreground"><div className="truncate">{r.document_name}</div>p.{r.page_number ?? "?"} · <span title={r.source_excerpt ?? ""}>“{(r.source_excerpt ?? "").slice(0, 40)}…”</span></Td>
          <Td><Badge tone={r.confidence >= 0.8 ? "success" : r.confidence >= 0.6 ? "warning" : "danger"}>{(r.confidence * 100).toFixed(0)}%</Badge></Td>
          <Td><Badge tone={r.verification_status === "verified" ? "success" : r.verification_status === "rejected" ? "danger" : "neutral"}>{r.corrected_value != null ? "corrected" : label(r.verification_status)}</Badge></Td>
          {canWrite && !closed && <Td className="text-right">
            {editing === r.id ? (
              <div className="flex items-center justify-end gap-1">
                <Input aria-label="Correction note" placeholder="Reason" value={note} onChange={(e) => setNote(e.target.value)} className="h-8 w-36" />
                <Button size="icon" variant="ghost" aria-label="Save correction" onClick={() => m.mutate({ id: r.id, body: { verification_status: "corrected", corrected_value: Number(value), correction_note: note || undefined } })}><Check /></Button>
                <Button size="icon" variant="ghost" aria-label="Cancel" onClick={() => setEditing(null)}><X /></Button>
              </div>
            ) : (
              <div className="flex justify-end gap-1">
                {r.verification_status !== "verified" && <Button size="sm" variant="ghost" onClick={() => m.mutate({ id: r.id, body: { verification_status: "verified" } })}>Verify</Button>}
                <Button size="icon" variant="ghost" aria-label={`Correct ${r.label}`} onClick={() => { setEditing(r.id); setValue(String(r.effective_value ?? "")); setNote(""); }}><Pencil /></Button>
              </div>
            )}
          </Td>}
        </tr>
      ))}</tbody>
    </Table>
  );
}

export function FinancialsTab({ appId, docNames, closed }: { appId: number; docNames: Record<number, string>; closed: boolean }) {
  const q = useQuery({ queryKey: ["financials", appId], queryFn: () => api.get<Fin>(`/assessments/${appId}/financials`) });
  if (q.isLoading) return <LoadingBlock />;
  if (q.error || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const f = q.data;
  const latest = f.periods[f.periods.length - 1];
  const li = (k: string) => latest?.line_items[k]?.value ?? null;
  const bankTotal = f.bank_monthly.reduce((s, m) => s + m.business_credits, 0);
  const gstTotal = f.gst_monthly.reduce((s, m) => s + (m.GSTR3B ?? m.GSTR1 ?? 0), 0);
  const conflicts = f.periods.flatMap((p) => p.conflicts.map((c) => ({ ...c, period: p.period_label })));
  if (!f.periods.length && !f.counts.bank_transactions && !f.counts.gst_records)
    return <EmptyState title="No financial data extracted yet" description="Upload and process financial statements, GST returns and bank statements first." />;

  return (
    <div className="space-y-6">
      {latest && (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-6">
          <Stat label={`Revenue (${latest.period_label})`} value={inr(li("revenue_from_operations"))} />
          <Stat label="Profit after tax" value={inr(li("pat"))} tone={(li("pat") ?? 0) < 0 ? "danger" : "default"} />
          <Stat label="Net worth" value={inr(li("shareholders_equity"))} tone={(li("shareholders_equity") ?? 0) < 0 ? "danger" : "default"} />
          <Stat label="Operating cash flow" value={inr(li("operating_cash_flow"))} tone={(li("operating_cash_flow") ?? 0) < 0 ? "warning" : "default"} />
          <Stat label="Bank business credits" value={f.counts.bank_transactions ? inr(bankTotal) : "Not provided"} hint={`${f.counts.bank_transactions} transactions`} />
          <Stat label="GST taxable value" value={f.counts.gst_records ? inr(gstTotal) : "Not provided"} hint={`${f.counts.gst_records} return rows`} />
        </div>
      )}
      {conflicts.length > 0 && <Callout tone="warning" title={`${conflicts.length} conflicting value(s) across sources`}>The highest-confidence value is used; verify the alternatives in the extracted-values review below.</Callout>}
      <div className="grid gap-6 xl:grid-cols-2">
        <Card><CardHeader title="Revenue & profitability trend" description="Only periods actually extracted are plotted (₹)." /><CardBody><TrendChart trend={f.trend} /></CardBody></Card>
        <Card><CardHeader title="GST turnover vs bank business credits" description="Monthly, from GSTR-3B rows and categorised bank credits." /><CardBody><GstBankChart gst={f.gst_monthly} bank={f.bank_monthly} /></CardBody></Card>
      </div>
      {f.periods.length > 0 && (
        <Card>
          <CardHeader title="Normalised financial statements" description="All figures converted from the reported unit (lakh/crore) and shown in ₹ lakh. Missing items stay missing." />
          <CardBody className="space-y-8">
            <StatementTable periods={f.periods} rows={PL_ROWS} title="Profit & loss" docNames={docNames} />
            <StatementTable periods={f.periods} rows={BS_ROWS} title="Balance sheet" docNames={docNames} />
            <StatementTable periods={f.periods} rows={CF_ROWS} title="Cash flow" docNames={docNames} />
          </CardBody>
        </Card>
      )}
      <Card>
        <CardHeader title="Financial ratios" description={f.ratios.length ? `Computed for ${f.ratios[0]?.period ?? "latest period"} with safe denominators. Generated with the latest credit assessment.` : "Generated with the credit assessment (step 7)."} />
        <RatioTable ratios={f.ratios} />
      </Card>
      <Card>
        <CardHeader title="Review extracted values" description="Verify or correct figures before assessment. Corrections are audited and flow into the consolidated statements." />
        <MetricReview appId={appId} closed={closed} />
      </Card>
    </div>
  );
}
