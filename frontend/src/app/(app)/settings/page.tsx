"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PlugZap, UserPlus } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { AuditTimeline } from "@/components/assessment/audit-tab";
import { Dialog, Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/overlay";
import { Badge, Button, Callout, Card, CardBody, CardHeader, ErrorState, Field, Input, KV, LoadingBlock, PageHeader, Select, Table, Td, Th } from "@/components/ui/primitives";
import { api, errorMessage } from "@/lib/api";
import { date, label } from "@/lib/format";
import { useMe } from "@/lib/session";
import type { AuditEntry, User } from "@/lib/types";

type Integrations = Record<string, Record<string, unknown> & { status?: string; provider?: string; note?: string | null }>;

function OrgTab({ isAdmin }: { isAdmin: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["org"], queryFn: () => api.get<{ id: number; name: string; slug: string; is_demo: boolean; member_count: number; created_at: string }>("/organization") });
  const [name, setName] = useState<string | null>(null);
  const save = useMutation({ mutationFn: () => api.patch("/organization", { name }), onSuccess: () => { toast.success("Organisation updated"); setName(null); qc.invalidateQueries({ queryKey: ["org"] }); qc.invalidateQueries({ queryKey: ["me"] }); }, onError: (e) => toast.error(errorMessage(e)) });
  if (q.isLoading) return <LoadingBlock />;
  if (!q.data) return <ErrorState error={q.error} />;
  return (
    <Card><CardHeader title="Organisation" description="All borrowers, documents and reports are isolated to this organisation, enforced by the API and by PostgreSQL row-level security." />
      <CardBody className="space-y-5">
        <KV items={[["Slug", q.data.slug], ["Members", q.data.member_count], ["Created", date(q.data.created_at)], ["Type", q.data.is_demo ? "Demonstration (synthetic data)" : "Production tenant"]]} />
        {isAdmin && <div className="flex max-w-lg items-end gap-2 border-t pt-4">
          <Field label="Organisation name" htmlFor="org-name"><Input id="org-name" value={name ?? q.data.name} onChange={(e) => setName(e.target.value)} /></Field>
          <Button disabled={!name || name === q.data.name || name.trim().length < 2} loading={save.isPending} onClick={() => save.mutate()}>Save</Button>
        </div>}
      </CardBody></Card>
  );
}

function UsersTab({ isAdmin, meId }: { isAdmin: boolean; meId: number }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["users"], queryFn: () => api.get<{ items: User[] }>("/users") });
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ email: "", full_name: "", password: "", role: "analyst", title: "" });
  const create = useMutation({
    mutationFn: () => api.post("/users", { ...form, title: form.title || undefined }),
    onSuccess: () => { toast.success("User added"); setOpen(false); setForm({ email: "", full_name: "", password: "", role: "analyst", title: "" }); qc.invalidateQueries({ queryKey: ["users"] }); },
  });
  const update = useMutation({ mutationFn: (p: { id: number; body: Record<string, unknown> }) => api.patch(`/users/${p.id}`, p.body), onSuccess: () => { toast.success("User updated"); qc.invalidateQueries({ queryKey: ["users"] }); }, onError: (e) => toast.error(errorMessage(e)) });
  return (
    <Card>
      <CardHeader title="Users" description="Assign each person the least privilege they need." action={isAdmin && <Button size="sm" onClick={() => setOpen(true)}><UserPlus />Add user</Button>} />
      {q.isLoading ? <LoadingBlock /> : (
        <Table>
          <thead><tr><Th>Name</Th><Th>E-mail</Th><Th>Role</Th><Th>Status</Th><Th>Last sign-in</Th></tr></thead>
          <tbody>{q.data?.items.map((u) => (
            <tr key={u.id}>
              <Td className="font-medium">{u.full_name}{u.id === meId && <Badge className="ml-2">you</Badge>}<div className="text-xs text-muted-foreground">{u.title}</div></Td>
              <Td>{u.email}</Td>
              <Td>{isAdmin && u.id !== meId ? <Select aria-label={`Role for ${u.full_name}`} className="h-8 w-32" value={u.role ?? "analyst"} onChange={(e) => update.mutate({ id: u.id, body: { role: e.target.value } })}>
                <option value="admin">Admin</option><option value="analyst">Analyst</option><option value="reviewer">Reviewer</option></Select> : <Badge tone="primary">{label(u.role)}</Badge>}</Td>
              <Td>{isAdmin && u.id !== meId ? <Button size="sm" variant="ghost" onClick={() => update.mutate({ id: u.id, body: { is_active: !u.is_active } })}>{u.is_active ? <Badge tone="success">Active — deactivate</Badge> : <Badge tone="danger">Inactive — reactivate</Badge>}</Button>
                : <Badge tone={u.is_active ? "success" : "danger"}>{u.is_active ? "Active" : "Inactive"}</Badge>}</Td>
              <Td className="text-muted-foreground">{date(u.last_login_at, true)}</Td>
            </tr>
          ))}</tbody>
        </Table>
      )}
      <Dialog open={open} onOpenChange={setOpen} title="Add user" description="The user signs in with this e-mail and password. Share the password through a secure channel."
        footer={<><Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button><Button loading={create.isPending} onClick={() => create.mutate()}>Add user</Button></>}>
        <div className="space-y-3">
          {create.error && <Callout tone="danger">{errorMessage(create.error)}</Callout>}
          <Field label="Full name" htmlFor="u-name"><Input id="u-name" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></Field>
          <Field label="E-mail" htmlFor="u-email"><Input id="u-email" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
          <Field label="Initial password" htmlFor="u-pw" hint="At least 10 characters with letters and numbers."><Input id="u-pw" type="password" autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Role" htmlFor="u-role"><Select id="u-role" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}><option value="analyst">Analyst</option><option value="reviewer">Reviewer</option><option value="admin">Admin</option></Select></Field>
            <Field label="Title" htmlFor="u-title"><Input id="u-title" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field>
          </div>
        </div>
      </Dialog>
    </Card>
  );
}

function RolesTab() {
  const q = useQuery({ queryKey: ["roles"], queryFn: () => api.get<{ roles: { role: string; description: string; permissions: string[] }[] }>("/roles") });
  return (
    <div className="grid gap-4 md:grid-cols-3">
      {q.data?.roles.map((r) => (
        <Card key={r.role}><CardHeader title={label(r.role)} description={r.description} />
          <CardBody><ul className="flex flex-wrap gap-1.5">{r.permissions.map((p) => <li key={p}><Badge>{p}</Badge></li>)}</ul></CardBody></Card>
      ))}
    </div>
  );
}

type Check = { status: string; detail: string | null; at: string; latency_ms?: number } | null;
type IntegrationEntry = Record<string, unknown> & { label?: string; status?: string; provider?: string; configured?: boolean; active?: boolean;
  note?: string | null; checks?: { last: Check; last_success: Check; last_failure: Check } | null };

const PROVIDER_ROWS: [string, boolean][] = [["llm", true], ["embeddings", true], ["tavily", true], ["brave", true], ["serpapi", true],
  ["ocr", false], ["jobs", false], ["rate_limiter", false], ["databricks", false]];

function statusBadge(st: string) {
  const tone = ["verified", "configured", "local", "available", "active"].includes(st) ? "success" : st === "error" ? "danger" : st === "not_configured" ? "warning" : "neutral";
  return <Badge tone={tone}>{label(st)}</Badge>;
}

function IntegrationsTab({ isAdmin }: { isAdmin: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["integrations"], queryFn: () => api.get<Record<string, IntegrationEntry>>("/system/integrations") });
  const test = useMutation({
    mutationFn: (n: string) => api.post<{ status: string; detail: string }>(`/system/integrations/${n}/test`),
    onSuccess: (r) => { toast.message(`Connection test: ${label(r.status)}`, { description: r.detail }); qc.invalidateQueries({ queryKey: ["integrations"] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (q.isLoading) return <LoadingBlock />;
  if (!q.data) return <ErrorState error={q.error} />;
  return (
    <Card>
      <CardHeader title="API configuration status" description="Keys live only in server environment variables / the secret manager and are never sent to the browser. 'Configured' means a key is present; only a successful connection test counts as verified. Failure details are redacted." />
      <Table>
        <thead><tr><Th>Integration</Th><Th>Provider</Th><Th>Configuration</Th><Th>Last connection test</Th><Th>Last success</Th><Th>Last failure</Th>{isAdmin && <Th className="text-right">Test</Th>}</tr></thead>
        <tbody>{PROVIDER_ROWS.map(([k, testable]) => {
          const it = q.data[k] ?? {};
          const st = String(it.status ?? (k === "ocr" ? (it.active_engine ? "available" : "unavailable") : k === "jobs" || k === "rate_limiter" ? "active" : "unknown"));
          const c = it.checks;
          return (
            <tr key={k} data-testid={`integration-${k}`}>
              <Td className="font-medium">{String(it.label ?? k)}{it.active ? <Badge tone="primary" className="ml-2">in use</Badge> : null}</Td>
              <Td className="text-sm">{String(it.provider ?? it.backend ?? it.active_engine ?? it.configured_provider ?? "—")}{it.model ? <div className="text-xs text-muted-foreground">{String(it.model)}</div> : null}</Td>
              <Td>{statusBadge(st)}{it.note ? <div className="mt-1 max-w-xs text-xs text-muted-foreground">{String(it.note)}</div> : null}</Td>
              <Td className="text-xs">{c?.last ? <>{statusBadge(c.last.status)}<div className="text-muted-foreground">{date(c.last.at, true)}</div></> : <span className="text-muted-foreground">{testable ? "Never tested" : "—"}</span>}</Td>
              <Td className="text-xs text-muted-foreground">{c?.last_success ? date(c.last_success.at, true) : "—"}</Td>
              <Td className="max-w-xs text-xs text-muted-foreground">{c?.last_failure ? <><div>{date(c.last_failure.at, true)}</div><div className="text-red-700">{c.last_failure.detail}</div></> : "—"}</Td>
              {isAdmin && <Td className="text-right">{testable && <Button size="sm" variant="outline" loading={test.isPending && test.variables === k} onClick={() => test.mutate(k)}><PlugZap />Test</Button>}</Td>}
            </tr>
          );
        })}</tbody>
      </Table>
    </Card>
  );
}

type ModelSummary = { primary: { name: string; status: string; role: string }; challenger: { available: boolean; detail?: string; version?: string; algorithm?: string;
  created_at?: string; status?: string; approved_for_decisions?: boolean; evaluation_status?: string; dataset?: { type: string; rows: number; borrowers: number; positive_rate: number; generator?: string };
  metrics_test?: Record<string, { roc_auc?: number; pr_auc?: number; brier?: number; f1?: number }>; feature_importance?: { feature: string; mean_abs_shap: number }[] } };

function ChallengerPanel() {
  const q = useQuery({ queryKey: ["models"], queryFn: () => api.get<ModelSummary>("/system/models") });
  const ch = q.data?.challenger;
  if (!ch) return null;
  return (
    <Card>
      <CardHeader title="Models" description={`Primary: ${q.data?.primary.name} (${q.data?.primary.role}).`} />
      <CardBody className="space-y-3 text-sm">
        {!ch.available ? <Callout>Challenger model not installed: {ch.detail}</Callout> : <>
          <div className="flex flex-wrap gap-2"><Badge tone="violet">{ch.version}</Badge><Badge tone="warning">Training data: {label(ch.dataset?.type)}</Badge>
            <Badge tone={ch.approved_for_decisions ? "success" : "danger"}>{ch.approved_for_decisions ? "Approved for decisions" : "Not approved for decisions"}</Badge></div>
          <p className="text-muted-foreground">{ch.algorithm}. {ch.evaluation_status}</p>
          <p className="text-xs text-muted-foreground">Dataset: {ch.dataset?.rows} snapshots / {ch.dataset?.borrowers} borrowers, positive rate {((ch.dataset?.positive_rate ?? 0) * 100).toFixed(1)}% ({ch.dataset?.generator}).</p>
          <Table><thead><tr><Th>Held-out synthetic test</Th><Th className="text-right">ROC-AUC</Th><Th className="text-right">PR-AUC</Th><Th className="text-right">Brier</Th><Th className="text-right">F1</Th></tr></thead>
            <tbody>{Object.entries(ch.metrics_test ?? {}).filter(([, m]) => m.roc_auc != null).map(([k, m]) => (
              <tr key={k}><Td>{label(k)}</Td><Td className="num text-right">{m.roc_auc?.toFixed(3)}</Td><Td className="num text-right">{m.pr_auc?.toFixed(3)}</Td><Td className="num text-right">{m.brier?.toFixed(4)}</Td><Td className="num text-right">{m.f1?.toFixed(3)}</Td></tr>))}</tbody></Table>
        </>}
      </CardBody>
    </Card>
  );
}

function AuditLogTab() {
  const [action, setAction] = useState("");
  const q = useQuery({ queryKey: ["audit-logs", action], queryFn: () => api.get<{ items: AuditEntry[]; total: number }>(`/audit-logs?limit=200${action ? `&action=${encodeURIComponent(action)}` : ""}`) });
  return (
    <Card>
      <CardHeader title={`Audit log${q.data ? ` (${q.data.total})` : ""}`} description="Organisation-wide, append-only."
        action={<Select aria-label="Filter actions" className="h-8 w-48" value={action} onChange={(e) => setAction(e.target.value)}>
          <option value="">All actions</option>{["auth", "borrower", "assessment", "document", "reconciliation", "review", "cam", "user", "metric", "policy"].map((a) => <option key={a} value={a}>{label(a)}</option>)}</Select>} />
      {q.isLoading ? <LoadingBlock /> : <AuditTimeline items={q.data?.items ?? []} />}
    </Card>
  );
}

function PolicyTab() {
  const q = useQuery({ queryKey: ["policy"], queryFn: () => api.get<{ active_version: number | null; config: { label: string; disclaimer: string; weights: Record<string, number>; rating_bands: { min: number; label: string }[]; decision: Record<string, unknown> }; versions: { version: number; name: string; is_active: boolean; change_note: string; created_at: string }[] }>("/policy") });
  if (q.isLoading) return <LoadingBlock />;
  if (!q.data) return <ErrorState error={q.error} />;
  const c = q.data.config;
  return (
    <div className="space-y-4">
      <Callout tone="warning" title="Prototype policy">{c.disclaimer}</Callout>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card><CardHeader title="Dimension weights" /><CardBody><ul className="space-y-1.5 text-sm">{Object.entries(c.weights).map(([k, w]) => <li key={k} className="flex justify-between"><span>{label(k)}</span><span className="num font-medium">{w}%</span></li>)}</ul></CardBody></Card>
        <Card><CardHeader title="Rating bands" /><CardBody><ul className="space-y-1.5 text-sm">{c.rating_bands.map((b) => <li key={b.label} className="flex justify-between"><span>{b.label}</span><span className="num">≥ {b.min}</span></li>)}</ul></CardBody></Card>
        <Card><CardHeader title="Decision parameters" /><CardBody><KV className="sm:grid-cols-1" items={[["Illustrative base rate", `${c.decision.base_rate}%`], ["Minimum DSCR", `${c.decision.min_dscr}x`], ["Approval score ≥", String(c.decision.approve_min_score)], ["Conditional score ≥", String(c.decision.conditional_min_score)], ["Min. confidence for a decision", String(c.decision.min_confidence_for_decision)]]} /></CardBody></Card>
      </div>
      <ChallengerPanel />
      <Card><CardHeader title="Policy versions" />
        <Table><thead><tr><Th>Version</Th><Th>Name</Th><Th>Change note</Th><Th>Created</Th></tr></thead>
          <tbody>{q.data.versions.map((v) => <tr key={v.version}><Td>v{v.version}{v.is_active && <Badge tone="success" className="ml-2">active</Badge>}</Td><Td>{v.name}</Td><Td className="text-xs">{v.change_note}</Td><Td className="text-muted-foreground">{date(v.created_at)}</Td></tr>)}</tbody></Table>
      </Card>
    </div>
  );
}

export default function SettingsPage() {
  const { data: me } = useMe();
  const isAdmin = me?.role === "admin";
  return (
    <>
      <PageHeader title="Settings & administration" description={isAdmin ? "Manage users, roles and integrations for your organisation." : "View your organisation's configuration. Changes require an administrator."} />
      <Tabs defaultValue="organisation">
        <TabsList>{[["organisation", "Organisation"], ["users", "Users"], ["roles", "Role permissions"], ["integrations", "API configuration"], ["audit", "Audit log"], ["policy", "Scoring policy"]].map(([k, l]) => <TabsTrigger key={k} value={k}>{l}</TabsTrigger>)}</TabsList>
        <TabsContent value="organisation"><OrgTab isAdmin={isAdmin} /></TabsContent>
        <TabsContent value="users"><UsersTab isAdmin={isAdmin} meId={me?.user.id ?? 0} /></TabsContent>
        <TabsContent value="roles"><RolesTab /></TabsContent>
        <TabsContent value="integrations"><IntegrationsTab isAdmin={isAdmin} /></TabsContent>
        <TabsContent value="audit"><AuditLogTab /></TabsContent>
        <TabsContent value="policy"><PolicyTab /></TabsContent>
      </Tabs>
    </>
  );
}
