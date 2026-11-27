"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Globe, Plus } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { SeverityBadge } from "@/components/status";
import { Badge, Button, Callout, Card, CardBody, CardHeader, EmptyState, Field, Input, LoadingBlock, Select, Textarea } from "@/components/ui/primitives";
import { api, errorMessage } from "@/lib/api";
import { date, label } from "@/lib/format";
import { useCan } from "@/lib/session";
import type { Finding, Observation, ResearchQuery } from "@/lib/types";

const SUBCATS: Record<string, string[]> = {
  factory_inspection: ["factory_utilization", "inventory_verification", "plant_condition", "workforce", "safety_compliance"],
  management_assessment: ["management_quality", "promoter_integrity", "succession", "transparency"],
  business_environment: ["customer_concentration", "supplier_dependence", "market_position", "regulatory_environment"],
};

function ObservationForm({ appId }: { appId: number }) {
  const qc = useQueryClient();
  const [v, setV] = useState({ category: "factory_inspection", sub_category: "factory_utilization", title: "", notes: "", rating: "" });
  const [preview, setPreview] = useState<{ severity: string; risk_impact: string; interpretation: string } | null>(null);
  const body = () => ({ ...v, rating: v.rating ? Number(v.rating) : undefined });
  const interp = useMutation({ mutationFn: () => api.post<typeof preview>(`/assessments/${appId}/observations/interpret`, body()), onSuccess: setPreview, onError: (e) => toast.error(errorMessage(e)) });
  const save = useMutation({
    mutationFn: () => api.post(`/assessments/${appId}/observations`, body()),
    onSuccess: () => { toast.success("Observation recorded"); setV({ ...v, title: "", notes: "", rating: "" }); setPreview(null); qc.invalidateQueries({ queryKey: ["observations", appId] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const valid = v.title.trim().length >= 3 && v.notes.trim().length >= 3;
  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Category" htmlFor="obs-cat"><Select id="obs-cat" value={v.category} onChange={(e) => setV({ ...v, category: e.target.value, sub_category: SUBCATS[e.target.value][0] })}>{Object.keys(SUBCATS).map((c) => <option key={c} value={c}>{label(c)}</option>)}</Select></Field>
        <Field label="Topic" htmlFor="obs-sub"><Select id="obs-sub" value={v.sub_category} onChange={(e) => setV({ ...v, sub_category: e.target.value })}>{SUBCATS[v.category].map((c) => <option key={c} value={c}>{label(c)}</option>)}</Select></Field>
        <Field label="Rating (1–5)" htmlFor="obs-rating"><Select id="obs-rating" value={v.rating} onChange={(e) => setV({ ...v, rating: e.target.value })}><option value="">Not rated</option>{[1, 2, 3, 4, 5].map((n) => <option key={n}>{n}</option>)}</Select></Field>
      </div>
      <Field label="Title" htmlFor="obs-title"><Input id="obs-title" value={v.title} onChange={(e) => setV({ ...v, title: e.target.value })} placeholder="e.g. Plant visit on 12 Nov" /></Field>
      <Field label="Observation notes" htmlFor="obs-notes"><Textarea id="obs-notes" rows={3} value={v.notes} onChange={(e) => setV({ ...v, notes: e.target.value })} placeholder="e.g. Factory operating at 40% capacity; one line idle." /></Field>
      {preview && <Callout tone={preview.risk_impact === "negative" ? "warning" : "info"} title={`Interpretation: ${label(preview.severity)} severity, ${preview.risk_impact} impact`}>{preview.interpretation}</Callout>}
      <div className="flex justify-end gap-2">
        <Button variant="outline" disabled={!valid} loading={interp.isPending} onClick={() => interp.mutate()}>Interpret</Button>
        <Button disabled={!valid} loading={save.isPending} onClick={() => save.mutate()}><Plus />Save observation</Button>
      </div>
    </div>
  );
}

export function DiligenceTab({ appId }: { appId: number }) {
  const qc = useQueryClient();
  const canRun = useCan("analysis:run");
  const canReview = useCan("comments:write");
  const research = useQuery({ queryKey: ["research", appId], queryFn: () => api.get<{ latest_run: number | null; queries: ResearchQuery[]; findings: Finding[] }>(`/assessments/${appId}/research`) });
  const obs = useQuery({ queryKey: ["observations", appId], queryFn: () => api.get<{ items: Observation[] }>(`/assessments/${appId}/observations`) });
  const run = useMutation({
    mutationFn: () => api.post<{ provider: string; findings: number; statuses: string[] }>(`/assessments/${appId}/research`),
    onSuccess: (r) => { toast.message(r.statuses.includes("not_configured") ? "No search provider configured — no external research was performed." : `Research complete: ${r.findings} finding(s) via ${r.provider}`); qc.invalidateQueries({ queryKey: ["research", appId] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const review = useMutation({ mutationFn: (p: { id: number; s: string }) => api.patch(`/observations/${p.id}`, { reviewer_status: p.s }), onSuccess: () => qc.invalidateQueries({ queryKey: ["observations", appId] }) });
  const notConfigured = research.data?.queries.some((q) => q.status === "not_configured");
  return (
    <div className="grid gap-6 xl:grid-cols-2">
      <Card>
        <CardHeader title="External research" description="Queries run through the configured search provider. Sources, publication and retrieval timestamps are preserved."
          action={canRun && <Button size="sm" onClick={() => run.mutate()} loading={run.isPending}><Globe />{research.data?.latest_run ? "Re-run research" : "Run research"}</Button>} />
        <CardBody className="space-y-4">
          {research.isLoading && <LoadingBlock />}
          {notConfigured && <Callout tone="warning" title="Search provider not configured">No external research was performed and no information is presented as verified. Configure SEARCH_PROVIDER and SEARCH_API_KEY (Tavily, Brave or SerpAPI) to enable it.</Callout>}
          {research.data && !research.data.latest_run && <EmptyState title="Research has not been run" />}
          {research.data?.findings.map((f) => (
            <div key={f.id} className="rounded-md border p-3 text-sm">
              <div className="flex flex-wrap items-center gap-2"><span className="font-medium">{f.title}</span><SeverityBadge severity={f.sentiment === "positive" ? "positive" : f.severity} />{f.is_synthetic && <Badge tone="warning">synthetic fixture</Badge>}<Badge>{label(f.verification_status)}</Badge></div>
              <p className="mt-1 text-xs text-muted-foreground">{f.summary}</p>
              <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-muted-foreground">
                <span>{f.source_name}</span>{f.source_url && <a className="inline-flex items-center gap-1 text-primary hover:underline" href={f.source_url} target="_blank" rel="noopener noreferrer nofollow">source <ExternalLink className="size-3" /></a>}
                <span>published {f.published_at ? date(f.published_at) : "unknown"}</span><span>retrieved {date(f.retrieved_at, true)}</span><span>{label(f.category)}</span>
              </div>
            </div>
          ))}
          {research.data?.latest_run && (
            <details className="text-xs"><summary className="cursor-pointer text-muted-foreground">Queries in run #{research.data.latest_run} ({research.data.queries.length})</summary>
              <ul className="mt-2 space-y-1">{research.data.queries.map((q) => <li key={q.id} className="flex justify-between gap-2"><span className="truncate">{q.query_text}</span><Badge>{label(q.status)} · {q.result_count}</Badge></li>)}</ul></details>
          )}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Primary due-diligence observations" description="Site visits and management meetings, interpreted into risk signals (analyst input)." />
        <CardBody className="space-y-5">
          {canRun && <ObservationForm appId={appId} />}
          <div className="space-y-3 border-t pt-4">
            {obs.data?.items.length === 0 && <p className="text-sm text-muted-foreground">No observations recorded.</p>}
            {obs.data?.items.map((o) => (
              <div key={o.id} className="rounded-md border p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2"><span className="font-medium">{o.title}</span><SeverityBadge severity={o.severity} /><Badge>{label(o.sub_category)}</Badge>{o.is_synthetic && <Badge tone="warning">synthetic</Badge>}<span className="text-xs text-muted-foreground">{date(o.observed_on)}</span></div>
                <p className="mt-1 text-xs">{o.notes}</p>
                <p className="mt-1 text-xs text-muted-foreground">{o.interpretation}</p>
                <div className="mt-2 flex items-center gap-2 text-xs"><span className="text-muted-foreground">Reviewer status:</span><Badge tone={o.reviewer_status === "verified" ? "success" : o.reviewer_status === "disputed" ? "danger" : "neutral"}>{label(o.reviewer_status)}</Badge>
                  {canReview && o.reviewer_status === "pending" && <><Button size="sm" variant="ghost" onClick={() => review.mutate({ id: o.id, s: "verified" })}>Mark verified</Button><Button size="sm" variant="ghost" onClick={() => review.mutate({ id: o.id, s: "disputed" })}>Dispute</Button></>}</div>
              </div>
            ))}
          </div>
        </CardBody>
      </Card>
    </div>
  );
}
