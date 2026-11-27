"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Gauge, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { ChallengerCard } from "@/components/assessment/challenger-card";
import { ConfidenceBadge, SeverityBadge } from "@/components/status";
import { Badge, Button, Callout, Card, CardBody, CardHeader, EmptyState, ErrorState, LoadingBlock, Progress } from "@/components/ui/primitives";
import { api, errorMessage } from "@/lib/api";
import { date, label } from "@/lib/format";
import { useCan } from "@/lib/session";
import type { Dimension, RiskAssessment } from "@/lib/types";

const scoreTone = (s: number) => (s >= 75 ? "success" : s >= 55 ? "primary" : s >= 40 ? "warning" : "danger") as "success" | "primary" | "warning" | "danger";

function DimensionRow({ d }: { d: Dimension }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="py-3">
      <button className="flex w-full items-center gap-4 text-left" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <div className="w-56 shrink-0"><div className="text-sm font-medium">{d.name}</div><div className="text-xs text-muted-foreground">Weight {d.weight}% · {label(d.data_status)}</div></div>
        <Progress value={d.score} tone={d.data_status === "insufficient" ? "warning" : scoreTone(d.score)} className="flex-1" />
        <div className="num w-14 text-right text-sm font-semibold">{d.score.toFixed(0)}</div>
        <div className="num w-16 text-right text-xs text-muted-foreground">+{d.weighted_score.toFixed(1)}</div>
      </button>
      {open && (
        <div className="mt-3 space-y-2 rounded-md bg-muted/40 p-3 text-sm">
          <p className="text-muted-foreground">{d.explanation}</p>
          <ul className="divide-y rounded-md border bg-card">
            {d.factors.map((f) => (
              <li key={f.key} className="flex items-start gap-3 px-3 py-2">
                <div className="w-48 shrink-0"><div className="font-medium">{f.name}</div><div className="text-xs text-muted-foreground">{f.display_value}</div></div>
                <div className="flex-1 text-xs">{f.explanation}{f.evidence?.length ? <span className="ml-1 text-muted-foreground">· {f.evidence.length} evidence ref(s)</span> : null}</div>
                {f.score == null ? <Badge tone="warning">missing</Badge> : <Badge tone={f.score >= 70 ? "success" : f.score >= 50 ? "info" : "danger"}>{f.score.toFixed(0)}</Badge>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </li>
  );
}

export function RiskTab({ appId, closed }: { appId: number; closed: boolean }) {
  const qc = useQueryClient();
  const canRun = useCan("analysis:run");
  const q = useQuery({ queryKey: ["risk", appId], queryFn: () => api.get<{ assessment: RiskAssessment | null; versions: number[] }>(`/assessments/${appId}/risk-assessment`) });
  const run = useMutation({
    mutationFn: () => api.post(`/assessments/${appId}/risk-assessment`),
    onSuccess: () => { toast.success("Credit assessment generated"); for (const k of ["risk", "recommendation", "assessment", "financials"]) qc.invalidateQueries({ queryKey: [k, appId] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (q.isLoading) return <LoadingBlock />;
  if (q.error) return <ErrorState error={q.error} />;
  const a = q.data?.assessment;
  const action = canRun && !closed && <Button onClick={() => run.mutate()} loading={run.isPending}><Gauge />{a ? "Re-run assessment" : "Generate credit assessment"}</Button>;
  if (!a) return <EmptyState icon={<Gauge />} title="No credit assessment yet" description="Generates the 0–100 risk score, nine weighted dimensions, Five Cs, red flags and a policy-driven recommendation from the processed evidence." action={action} />;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">Version {a.version} · generated {date(a.created_at, true)} · {a.model_label}</p>
        {action}
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="p-5">
          <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Risk score (model output)</div>
          <div className="mt-2 flex items-baseline gap-2"><span className="num text-4xl font-semibold" data-testid="risk-score">{a.overall_score.toFixed(1)}</span><span className="text-sm text-muted-foreground">/ 100 · higher is lower risk</span></div>
          <Badge tone={scoreTone(a.overall_score) === "primary" ? "info" : scoreTone(a.overall_score)} className="mt-2">{a.risk_category}</Badge>
          <Progress value={a.overall_score} tone={scoreTone(a.overall_score)} className="mt-4" />
        </Card>
        <Card className="border-dashed p-5">
          <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Confidence indicator (data quality)</div>
          <div className="mt-2 flex items-baseline gap-2"><span className="num text-4xl font-semibold">{a.confidence_score.toFixed(0)}</span><span className="text-sm text-muted-foreground">/ 100</span></div>
          <div className="mt-2"><ConfidenceBadge level={a.confidence_level} /></div>
          <p className="mt-3 text-xs text-muted-foreground">How complete and reliable the evidence is — not how risky the borrower is. Data completeness {a.data_completeness.toFixed(0)}%.</p>
        </Card>
        <Card className="p-5">
          <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Confidence components</div>
          <ul className="mt-3 space-y-2 text-xs">
            {Object.entries(a.confidence_breakdown.components ?? {}).map(([k, c]) => (
              <li key={k}><div className="flex justify-between"><span>{label(k)}</span><span className="num">{c.score.toFixed(0)}</span></div><Progress value={c.score} className="mt-1 h-1.5" /></li>
            ))}
          </ul>
        </Card>
      </div>
      <Callout>{a.summary}</Callout>
      <div className="grid gap-6 xl:grid-cols-[1fr_380px]">
        <Card>
          <CardHeader title="Nine scoring dimensions" description="Click a dimension to see the factors, values and evidence behind it." />
          <CardBody className="py-1"><ul className="divide-y">{a.dimensions.map((d) => <DimensionRow key={d.key} d={d} />)}</ul></CardBody>
        </Card>
        <div className="space-y-6">
          <Card>
            <CardHeader title={`Red flags (${a.red_flags.length})`} />
            <CardBody className="space-y-3">
              {a.red_flags.length === 0 && <p className="text-sm text-muted-foreground">No red flags raised.</p>}
              {a.red_flags.map((f, i) => (
                <div key={i} className="text-sm"><div className="flex items-start gap-2"><ShieldAlert className="mt-0.5 size-4 shrink-0 text-red-600" /><div><div className="flex flex-wrap items-center gap-2 font-medium">{f.title}<SeverityBadge severity={f.severity} /></div>
                  <p className="text-xs text-muted-foreground">{f.detail}</p></div></div></div>
              ))}
            </CardBody>
          </Card>
          <Card>
            <CardHeader title={`Missing information (${a.missing_information.length})`} description="Missing evidence is never scored favourably." />
            <CardBody>
              <ul className="space-y-2 text-sm">{a.missing_information.map((m, i) => <li key={i} className="flex gap-2"><Badge tone={m.importance === "critical" ? "danger" : m.importance === "high" ? "warning" : "neutral"}>{m.importance}</Badge><span className="text-xs">{m.message}</span></li>)}</ul>
              {a.missing_information.length === 0 && <p className="text-sm text-muted-foreground">Nothing missing.</p>}
            </CardBody>
          </Card>
        </div>
      </div>
      <Card>
        <CardHeader title="Five Cs of Credit" />
        <div className="grid gap-px bg-border sm:grid-cols-2 lg:grid-cols-5">
          {Object.entries(a.five_cs).map(([k, c]) => (
            <div key={k} className="bg-card p-4">
              <div className="flex items-center justify-between"><span className="font-semibold">{c.name}</span>{c.score != null ? <Badge tone={c.score >= 70 ? "success" : c.score >= 50 ? "info" : "danger"}>{c.score.toFixed(0)}</Badge> : <Badge tone="warning">not assessed</Badge>}</div>
              <p className="mt-2 text-xs text-muted-foreground">{c.assessment}</p>
              {c.strengths.length > 0 && <ul className="mt-2 space-y-1 text-xs text-emerald-800">{c.strengths.slice(0, 2).map((s, i) => <li key={i}>+ {s}</li>)}</ul>}
              {c.risks.length > 0 && <ul className="mt-2 space-y-1 text-xs text-red-800">{c.risks.slice(0, 2).map((s, i) => <li key={i}>− {s}</li>)}</ul>}
            </div>
          ))}
        </div>
      </Card>
      {a.challenger && <ChallengerCard c={a.challenger} />}
      <Card>
        <CardHeader title="Risk categories" description="Twelve categories derived from the same factors." />
        <CardBody className="grid gap-x-8 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
          {Object.entries(a.risk_categories).map(([k, c]) => (
            <div key={k}><div className="flex justify-between text-xs"><span>{c.name}</span><span className="num">{c.score != null ? c.score.toFixed(0) : "not assessed"}</span></div><Progress value={c.score ?? 0} tone={c.score == null ? "warning" : scoreTone(c.score)} className="mt-1 h-1.5" /></div>
          ))}
        </CardBody>
      </Card>
    </div>
  );
}
