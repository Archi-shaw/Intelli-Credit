"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, FileText, GitCompare, MessageSquare } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { SeverityBadge } from "@/components/status";
import { Badge, Button, Callout, Card, EmptyState, ErrorState, LoadingBlock, Select, statusTone, Textarea } from "@/components/ui/primitives";
import { api, errorMessage } from "@/lib/api";
import { date, inrWords, label } from "@/lib/format";
import { useCan } from "@/lib/session";
import type { Comment, Recon } from "@/lib/types";

const STATUS_COPY: Record<string, string> = {
  potential_discrepancy: "Potential discrepancy", unusual_pattern: "Unusual pattern", requires_verification: "Requires verification",
  consistent: "Consistent", insufficient_data: "Insufficient data",
};

function Comments({ appId, recon }: { appId: number; recon: Recon }) {
  const qc = useQueryClient();
  const [body, setBody] = useState("");
  const canComment = useCan("comments:write");
  const q = useQuery({ queryKey: ["comments", appId, "reconciliation", recon.id], queryFn: () => api.get<{ items: Comment[] }>(`/assessments/${appId}/comments?entity_type=reconciliation&entity_id=${recon.id}`) });
  const add = useMutation({
    mutationFn: () => api.post(`/assessments/${appId}/comments`, { body, section: "reconciliation", entity_type: "reconciliation", entity_id: recon.id }),
    onSuccess: () => { setBody(""); qc.invalidateQueries({ queryKey: ["comments", appId, "reconciliation", recon.id] }); qc.invalidateQueries({ queryKey: ["recon", appId] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <div className="space-y-2">
      {q.data?.items.map((c) => <div key={c.id} className="rounded-md bg-muted/50 px-3 py-2 text-sm"><div className="text-xs text-muted-foreground">{c.author_name} · {date(c.created_at, true)}</div>{c.body}</div>)}
      {canComment && (
        <div className="flex gap-2">
          <Textarea rows={2} value={body} onChange={(e) => setBody(e.target.value)} placeholder="Add an analyst note (e.g. explanation obtained, documents requested)…" aria-label="Comment on finding" />
          <Button size="sm" className="self-end" disabled={!body.trim()} loading={add.isPending} onClick={() => add.mutate()}>Add note</Button>
        </div>
      )}
    </div>
  );
}

function Finding({ appId, r, closed }: { appId: number; r: Recon; closed: boolean }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(r.severity === "high" || r.severity === "critical");
  const canReview = useCan("comments:write");
  const review = useMutation({
    mutationFn: (s: string) => api.patch(`/assessments/${appId}/reconciliation/${r.id}`, { review_status: s }),
    onSuccess: () => { toast.success("Review status updated"); qc.invalidateQueries({ queryKey: ["recon", appId] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const flagged = !["consistent", "insufficient_data"].includes(r.status);
  return (
    <Card className={flagged ? (r.severity === "high" || r.severity === "critical" ? "border-l-4 border-l-red-500" : "border-l-4 border-l-amber-400") : ""} data-testid={`recon-${r.check_type}`}>
      <button className="flex w-full items-start gap-3 px-5 py-4 text-left" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        {open ? <ChevronDown className="mt-0.5 size-4 shrink-0" /> : <ChevronRight className="mt-0.5 size-4 shrink-0" />}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{r.metric}</span>
            <Badge tone={statusTone(r.status)}>{STATUS_COPY[r.status] ?? label(r.status)}</Badge>
            {flagged && <SeverityBadge severity={r.severity} />}
            {r.period && <span className="text-xs text-muted-foreground">{r.period}</span>}
            {!!r.comment_count && <span className="flex items-center gap-1 text-xs text-muted-foreground"><MessageSquare className="size-3" />{r.comment_count}</span>}
          </div>
          {r.source_a_value != null && r.source_b_value != null && (
            <div className="num mt-2 grid gap-1 text-sm sm:grid-cols-3">
              <div><span className="text-muted-foreground">{r.source_a_label}:</span> <span className="font-medium">{inrWords(r.source_a_value)}</span></div>
              <div><span className="text-muted-foreground">{r.source_b_label}:</span> <span className="font-medium">{inrWords(r.source_b_value)}</span></div>
              {r.difference != null && <div><span className="text-muted-foreground">Difference:</span> <span className="font-semibold">{inrWords(Math.abs(r.difference))}</span>{r.difference_pct != null && <span className="text-muted-foreground"> ({r.difference_pct > 0 ? "+" : ""}{r.difference_pct.toFixed(1)}%)</span>}</div>}
            </div>
          )}
        </div>
        <Badge tone={statusTone(r.review_status)}>Review: {label(r.review_status)}</Badge>
      </button>
      {open && (
        <div className="space-y-4 border-t px-5 py-4 text-sm">
          <p>{r.description}</p>
          {r.risk_implication && <p className="text-muted-foreground"><span className="font-medium text-foreground">Why it matters: </span>{r.risk_implication}</p>}
          {r.suggested_investigation && <p className="text-muted-foreground"><span className="font-medium text-foreground">Suggested verification: </span>{r.suggested_investigation}</p>}
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-muted-foreground">Sources:</span>
            {r.source_documents.length ? r.source_documents.map((d) => <Badge key={d.id}><FileText className="size-3" />{d.name ?? `Document ${d.id}`}</Badge>) : <span className="text-muted-foreground">—</span>}
            <span className="ml-2 text-muted-foreground">Check confidence {(r.confidence * 100).toFixed(0)}%</span>
          </div>
          {Array.isArray((r.details as { months?: unknown[] }).months) && (
            <details className="text-xs"><summary className="cursor-pointer text-muted-foreground">Monthly detail</summary>
              <pre className="mt-2 max-h-48 overflow-auto rounded bg-muted p-2">{JSON.stringify((r.details as { months: unknown[] }).months, null, 1)}</pre></details>
          )}
          {canReview && !closed && flagged && (
            <div className="flex items-center gap-2">
              <label htmlFor={`rs-${r.id}`} className="text-xs font-medium">Analyst review</label>
              <Select id={`rs-${r.id}`} className="h-8 w-48" value={r.review_status} onChange={(e) => review.mutate(e.target.value)}>
                <option value="open">Open</option><option value="under_review">Under review</option><option value="explained">Explained by borrower</option><option value="escalated">Escalated</option>
              </Select>
            </div>
          )}
          <Comments appId={appId} recon={r} />
        </div>
      )}
    </Card>
  );
}

export function ReconciliationTab({ appId, closed }: { appId: number; closed: boolean }) {
  const qc = useQueryClient();
  const canRun = useCan("analysis:run");
  const q = useQuery({ queryKey: ["recon", appId], queryFn: () => api.get<{ run_number: number | null; summary: Record<string, number>; items: Recon[] }>(`/assessments/${appId}/reconciliation`) });
  const run = useMutation({
    mutationFn: () => api.post(`/assessments/${appId}/reconciliation`),
    onSuccess: () => { toast.success("Reconciliation complete"); qc.invalidateQueries({ queryKey: ["recon", appId] }); qc.invalidateQueries({ queryKey: ["assessment", appId] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const [showAll, setShowAll] = useState(false);
  if (q.isLoading) return <LoadingBlock />;
  if (q.error || !q.data) return <ErrorState error={q.error} />;
  const items = q.data.items;
  const flagged = items.filter((i) => !["consistent", "insufficient_data"].includes(i.status));
  const visible = showAll ? items : flagged;
  return (
    <div className="space-y-5">
      <Callout title="How to read these findings">Cross-source checks compare GST returns, bank statements, financial statements and declared borrowings. Results are <b>potential discrepancies</b>, <b>unusual patterns</b> or items that <b>require verification</b> — they are prompts for due diligence, not conclusions of wrongdoing.</Callout>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-2 text-sm">
          {q.data.run_number ? Object.entries(q.data.summary).filter(([, n]) => n > 0).map(([k, n]) => <Badge key={k} tone={statusTone(k)}>{STATUS_COPY[k]}: {n}</Badge>) : <span className="text-muted-foreground">Not run yet.</span>}
          {q.data.run_number && <span className="text-xs text-muted-foreground">Run #{q.data.run_number}</span>}
        </div>
        <div className="flex gap-2">
          {items.length > flagged.length && <Button size="sm" variant="ghost" onClick={() => setShowAll((s) => !s)}>{showAll ? "Show findings only" : `Show all ${items.length} checks`}</Button>}
          {canRun && !closed && <Button onClick={() => run.mutate()} loading={run.isPending}><GitCompare />{q.data.run_number ? "Re-run reconciliation" : "Run reconciliation"}</Button>}
        </div>
      </div>
      {!q.data.run_number ? <EmptyState icon={<GitCompare />} title="Reconciliation has not been run" description="Run the twelve cross-source checks once the documents have been processed." />
        : visible.length === 0 ? <Callout tone="success" title="No potential discrepancies">All checks that could be performed were consistent within policy tolerances.</Callout>
        : <div className="space-y-3">{visible.map((r) => <Finding key={r.id} appId={appId} r={r} closed={closed} />)}</div>}
    </div>
  );
}
