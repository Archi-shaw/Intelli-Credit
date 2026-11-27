"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Scale } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { OutcomeBadge } from "@/components/status";
import { Badge, Button, Callout, Card, CardBody, CardHeader, EmptyState, ErrorState, Field, Input, KV, LoadingBlock, Select, Table, Td, Textarea, Th } from "@/components/ui/primitives";
import { api, errorMessage } from "@/lib/api";
import { date, inr, label, pct, ratio } from "@/lib/format";
import { useCan, useMe } from "@/lib/session";
import type { Assessment, Comment, Decision, Recommendation } from "@/lib/types";

function ReviewPanel({ app, rec }: { app: Assessment; rec: Recommendation }) {
  const qc = useQueryClient();
  const { data: me } = useMe();
  const canDecide = useCan("review:decide");
  const decisions = useQuery({ queryKey: ["decisions", app.id], queryFn: () => api.get<{ items: Decision[] }>(`/assessments/${app.id}/decisions`) });
  const [decision, setDecision] = useState("approved");
  const [comments, setComments] = useState("");
  const [amount, setAmount] = useState("");
  const [rate, setRate] = useState("");
  const [tenure, setTenure] = useState("");
  const m = useMutation({
    mutationFn: () => api.post(`/assessments/${app.id}/decision`, {
      decision, comments, ...(decision === "approved" && amount ? { final_amount: Number(amount) } : {}),
      ...(decision === "approved" && rate ? { final_rate: Number(rate) } : {}), ...(decision === "approved" && tenure ? { final_tenure_months: Number(tenure) } : {}),
    }),
    onSuccess: () => { toast.success("Review decision recorded"); for (const k of ["decisions", "assessment", "audit"]) qc.invalidateQueries({ queryKey: [k, app.id] }); setComments(""); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const closed = ["approved", "rejected"].includes(app.status);
  const isPreparer = me?.user.id === app.created_by_id;
  return (
    <Card>
      <CardHeader title="Human review & approval" description="Recorded decisions are immutable and audited. The reviewer must not be the preparer." />
      <CardBody className="space-y-4">
        {decisions.data?.items.map((d) => (
          <div key={d.id} className="rounded-md border p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2"><Badge tone={d.decision === "approved" ? "success" : d.decision === "rejected" ? "danger" : "warning"}>{label(d.decision)}</Badge>
              <span className="font-medium">{d.reviewer_name}</span><span className="text-xs text-muted-foreground">{date(d.created_at, true)}</span>
              {d.overrides_recommendation && <Badge tone="violet">overrides model recommendation</Badge>}</div>
            {d.decision === "approved" && <p className="num mt-1 text-xs">Final terms: {inr(d.final_amount)} at {pct(d.final_rate, 2)} for {d.final_tenure_months ?? "—"} months</p>}
            <p className="mt-1 text-muted-foreground">{d.comments}</p>
          </div>
        ))}
        {!decisions.data?.items.length && <p className="text-sm text-muted-foreground">No review decision recorded — the recommendation is pending authorised human review.</p>}
        {canDecide && !closed && (isPreparer ? <Callout tone="warning">You prepared this assessment, so another reviewer must record the decision (segregation of duties).</Callout> : (
          <form className="space-y-3 border-t pt-4" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
            <div className="grid gap-3 sm:grid-cols-4">
              <Field label="Decision" htmlFor="decision"><Select id="decision" value={decision} onChange={(e) => setDecision(e.target.value)}>
                <option value="approved">Approve</option><option value="rejected">Reject</option><option value="returned">Return for more work</option></Select></Field>
              {decision === "approved" && <>
                <Field label="Final amount (₹)" htmlFor="final_amount" hint={`Default: ${inr(rec.recommended_amount)}; max ${inr(rec.requested_amount)}`}><Input id="final_amount" inputMode="numeric" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder={String(rec.recommended_amount)} /></Field>
                <Field label="Rate (%)" htmlFor="final_rate"><Input id="final_rate" inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} placeholder={rec.indicative_rate?.toString() ?? ""} /></Field>
                <Field label="Tenure (months)" htmlFor="final_tenure"><Input id="final_tenure" inputMode="numeric" value={tenure} onChange={(e) => setTenure(e.target.value)} placeholder={rec.recommended_tenure_months?.toString() ?? ""} /></Field>
              </>}
            </div>
            <Field label="Justification" htmlFor="justification" required hint="At least 10 characters. Recorded verbatim in the audit trail and the CAM.">
              <Textarea id="justification" rows={3} value={comments} onChange={(e) => setComments(e.target.value)} /></Field>
            <div className="flex justify-end"><Button type="submit" disabled={comments.trim().length < 10} loading={m.isPending}>Record decision</Button></div>
          </form>
        ))}
        {!canDecide && !closed && <p className="text-xs text-muted-foreground">Only reviewers and administrators can record decisions.</p>}
      </CardBody>
    </Card>
  );
}

function Notes({ appId }: { appId: number }) {
  const qc = useQueryClient();
  const [body, setBody] = useState("");
  const canComment = useCan("comments:write");
  const q = useQuery({ queryKey: ["comments", appId, "all"], queryFn: () => api.get<{ items: Comment[] }>(`/assessments/${appId}/comments`) });
  const add = useMutation({ mutationFn: () => api.post(`/assessments/${appId}/comments`, { body, section: "recommendation" }), onSuccess: () => { setBody(""); qc.invalidateQueries({ queryKey: ["comments", appId] }); }, onError: (e) => toast.error(errorMessage(e)) });
  const general = (q.data?.items ?? []).filter((c) => !c.entity_type);
  return (
    <Card>
      <CardHeader title="Analyst notes" description="Opinions recorded here are labelled as analyst input in the CAM." />
      <CardBody className="space-y-3">
        {general.map((c) => <div key={c.id} className="rounded-md bg-muted/50 px-3 py-2 text-sm"><div className="text-xs text-muted-foreground">{c.author_name} · {label(c.section)} · {date(c.created_at, true)}</div>{c.body}</div>)}
        {!general.length && <p className="text-sm text-muted-foreground">No notes yet.</p>}
        {canComment && <div className="flex gap-2"><Textarea rows={2} value={body} onChange={(e) => setBody(e.target.value)} placeholder="Add a note for the reviewer…" aria-label="Analyst note" />
          <Button className="self-end" size="sm" disabled={!body.trim()} loading={add.isPending} onClick={() => add.mutate()}>Add note</Button></div>}
      </CardBody>
    </Card>
  );
}

export function RecommendationTab({ app }: { app: Assessment }) {
  const q = useQuery({ queryKey: ["recommendation", app.id], queryFn: () => api.get<{ recommendation: Recommendation | null }>(`/assessments/${app.id}/recommendation`) });
  if (q.isLoading) return <LoadingBlock />;
  if (q.error) return <ErrorState error={q.error} />;
  const r = q.data?.recommendation;
  if (!r) return <EmptyState icon={<Scale />} title="No recommendation yet" description="Generate the credit assessment (Risk tab) to produce a policy-driven recommendation." />;
  const rc = r.repayment_capacity;
  return (
    <div className="space-y-6">
      <Callout tone="warning" title="Decision support — not an approval">{r.disclaimer} Amounts, rates and tenure are computed deterministically from policy rules; rates are illustrative.</Callout>
      <Card>
        <CardBody className="space-y-4">
          <div className="flex flex-wrap items-center gap-3"><OutcomeBadge outcome={r.outcome} /><Badge>{r.risk_category}</Badge><span className="text-xs text-muted-foreground">Recommendation v{r.version} · {date(r.created_at, true)}</span></div>
          <p className="text-sm font-medium">{r.explanation.headline}</p>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
            {[["Requested amount", inr(r.requested_amount)], ["Recommended amount", inr(r.recommended_amount)], ["Indicative rate", r.indicative_rate != null ? pct(r.indicative_rate, 2) : "Not quoted"],
              ["Recommended tenure", r.recommended_tenure_months ? `${r.recommended_tenure_months} months` : "—"], ["Proposed DSCR", ratio(rc.proposed_dscr ?? null, "x")]].map(([k, v]) => (
              <div key={k} className="rounded-md border bg-muted/30 p-3"><div className="text-xs text-muted-foreground">{k}</div><div className="num mt-1 text-lg font-semibold" data-testid={k.toLowerCase().replace(/ /g, "-")}>{v}</div></div>
            ))}
          </div>
        </CardBody>
      </Card>
      <div className="grid gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader title="Recommended amount — constraints" description={r.explanation.why_amount} />
          <Table><thead><tr><Th>Constraint</Th><Th className="text-right">Limit</Th><Th>Formula</Th></tr></thead>
            <tbody>{r.amount_constraints.map((c) => <tr key={c.key + c.name} className={c.binding ? "bg-amber-50" : ""}><Td className="font-medium">{c.name}{c.binding && <Badge tone="warning" className="ml-2">binding</Badge>}</Td><Td className="num text-right">{inr(c.value, { missing: "n/a" })}</Td><Td className="text-xs text-muted-foreground">{c.formula}</Td></tr>)}</tbody></Table>
        </Card>
        <Card>
          <CardHeader title="Indicative interest rate" description={r.explanation.why_rate} />
          {r.indicative_rate != null ? (
            <Table><tbody>
              <tr><Td>Illustrative base rate (policy assumption)</Td><Td className="num text-right">{pct(r.base_rate, 2)}</Td></tr>
              <tr><Td>Risk premium — {r.risk_category}</Td><Td className="num text-right">+{pct(r.risk_premium, 2)}</Td></tr>
              {r.rate_adjustments.map((a) => <tr key={a.name}><Td>{a.name}</Td><Td className="num text-right">{a.value > 0 ? "+" : ""}{a.value.toFixed(2)}%</Td></tr>)}
              <tr className="font-semibold"><Td>Indicative rate</Td><Td className="num text-right">{pct(r.indicative_rate, 2)}</Td></tr>
            </tbody></Table>
          ) : <CardBody><p className="text-sm text-muted-foreground">No rate quoted — outside the configured pricing appetite.</p></CardBody>}
        </Card>
        <Card>
          <CardHeader title="Repayment capacity" description={`Basis: ${rc.basis ?? "not determinable"}`} />
          <CardBody>
            <KV items={[["Cash available for debt service", inr(rc.cash_available_for_debt_service ?? null)], ["Existing annual debt service", inr(rc.existing_annual_debt_service ?? null)],
              ["Minimum DSCR (policy)", rc.min_dscr ? `${rc.min_dscr}x` : "—"], ["Proposed DSCR", ratio(rc.proposed_dscr ?? null, "x")], ["Indicative EMI", inr(rc.indicative_emi ?? null)]]} />
            {!!rc.existing_service_notes?.length && <ul className="mt-4 list-disc space-y-1 pl-4 text-xs text-muted-foreground">{rc.existing_service_notes.map((n) => <li key={n}>{n}</li>)}</ul>}
            {r.tenure_rationale && <p className="mt-4 border-t pt-3 text-xs text-muted-foreground"><span className="font-medium text-foreground">Tenure: </span>{r.tenure_rationale}</p>}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Risk factors & mitigations" />
          <CardBody className="space-y-4 text-sm">
            {r.critical_warnings.length > 0 && <Callout tone="danger" title="Critical warnings"><ul className="list-disc pl-4">{r.critical_warnings.map((w) => <li key={w}>{w}</li>)}</ul></Callout>}
            <div className="grid gap-4 sm:grid-cols-2">
              <div><h4 className="mb-1 text-xs font-semibold uppercase text-emerald-800">Positive factors</h4><ul className="space-y-1 text-xs">{r.positive_factors.map((p, i) => <li key={i}>+ {p.factor}{p.detail ? ` — ${p.detail}` : ""}</li>)}</ul></div>
              <div><h4 className="mb-1 text-xs font-semibold uppercase text-red-800">Negative factors</h4><ul className="space-y-1 text-xs">{r.negative_factors.map((p, i) => <li key={i}>− {p.factor}{p.detail ? ` — ${p.detail}` : ""}</li>)}</ul></div>
            </div>
            <div><h4 className="mb-1 text-xs font-semibold uppercase text-muted-foreground">Required conditions & mitigations</h4><ul className="list-disc space-y-1 pl-4 text-xs">{[...r.conditions_precedent, ...r.repayment_considerations].map((c) => <li key={c}>{c}</li>)}</ul></div>
            <div><h4 className="mb-1 text-xs font-semibold uppercase text-muted-foreground">Security</h4><ul className="space-y-1 text-xs">{r.collateral_requirements.map((c, i) => <li key={i}><Badge className="mr-1">{label(c.status)}</Badge>{c.description}</li>)}</ul></div>
          </CardBody>
        </Card>
      </div>
      <div className="grid gap-6 xl:grid-cols-2"><ReviewPanel app={app} rec={r} /><Notes appId={app.id} /></div>
    </div>
  );
}
