"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Building2, CheckCircle2, ClipboardCheck, FileClock, FolderKanban, Plus } from "lucide-react";
import Link from "next/link";

import { StatusBadge } from "@/components/status";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorState, LoadingBlock, PageHeader, Stat, Table, Td, Th } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { inr, LOAN_TYPES, OUTCOME_LABELS, STATUS_LABELS } from "@/lib/format";
import { useCan, useMe } from "@/lib/session";
import type { Assessment } from "@/lib/types";

interface Dash {
  totals: { borrowers: number; active_assessments: number; documents_pending: number; documents_failed: number; completed_assessments: number; potential_discrepancies: number; awaiting_review: number };
  status_breakdown: Record<string, number>;
  recent_assessments: Assessment[];
  recommendation_outcomes: Record<string, number>;
}

export default function DashboardPage() {
  const { data: me } = useMe();
  const canCreate = useCan("assessments:write");
  const q = useQuery({ queryKey: ["dashboard"], queryFn: () => api.get<Dash>("/dashboard"), refetchInterval: 20_000 });

  return (
    <>
      <PageHeader title={`Welcome, ${me?.user.full_name.split(" ")[0] ?? ""}`} description="Portfolio of credit assessments in your organisation."
        actions={canCreate && <Button asChild><Link href="/assessments/new"><Plus />New assessment</Link></Button>} />
      {q.isLoading && <LoadingBlock />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-3 xl:grid-cols-6">
            <Stat label="Total borrowers" value={q.data.totals.borrowers} icon={<Building2 />} />
            <Stat label="Active assessments" value={q.data.totals.active_assessments} icon={<FolderKanban />} />
            <Stat label="Documents pending" value={q.data.totals.documents_pending} icon={<FileClock />}
              hint={q.data.totals.documents_failed ? `${q.data.totals.documents_failed} failed` : "processing queue"} tone={q.data.totals.documents_failed ? "warning" : "default"} />
            <Stat label="Completed assessments" value={q.data.totals.completed_assessments} icon={<CheckCircle2 />} hint="approved or rejected" />
            <Stat label="Potential discrepancies" value={q.data.totals.potential_discrepancies} icon={<AlertTriangle />} hint="open, latest reconciliation runs" tone={q.data.totals.potential_discrepancies ? "danger" : "default"} />
            <Stat label="Awaiting review" value={q.data.totals.awaiting_review} icon={<ClipboardCheck />} hint="recommendations pending a reviewer" />
          </div>

          <div className="grid gap-6 xl:grid-cols-[1fr_320px]">
            <Card>
              <CardHeader title="Recent assessments" action={<Link href="/assessments" className="text-xs font-medium text-primary hover:underline">View all</Link>} />
              {q.data.recent_assessments.length === 0 ? (
                <div className="p-5"><EmptyState title="No assessments yet" description="Create a borrower and start a credit assessment to see it here."
                  action={canCreate && <Button asChild size="sm"><Link href="/assessments/new">Start an assessment</Link></Button>} /></div>
              ) : (
                <Table>
                  <thead><tr><Th>Reference</Th><Th>Borrower</Th><Th>Facility</Th><Th className="text-right">Requested</Th><Th>Status</Th><Th>Risk</Th><Th>Model recommendation</Th></tr></thead>
                  <tbody>
                    {q.data.recent_assessments.map((a) => (
                      <tr key={a.id} className="hover:bg-muted/40">
                        <Td><Link href={`/assessments/${a.id}`} className="font-medium text-primary hover:underline">{a.reference}</Link></Td>
                        <Td><div className="max-w-[240px] truncate">{a.company_name}</div>{a.company_is_synthetic && <span className="text-[11px] text-amber-700">Synthetic</span>}</Td>
                        <Td>{LOAN_TYPES[a.loan_type] ?? a.loan_type}</Td>
                        <Td className="num text-right">{inr(a.requested_amount)}</Td>
                        <Td><StatusBadge status={a.status} /></Td>
                        <Td>{a.risk_score != null ? <span className="num">{a.risk_score.toFixed(1)} · <span className="text-muted-foreground">{a.risk_category}</span></span> : <span className="text-muted-foreground">Not assessed</span>}</Td>
                        <Td>{a.recommendation_outcome ? OUTCOME_LABELS[a.recommendation_outcome] : <span className="text-muted-foreground">—</span>}</Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              )}
            </Card>
            <div className="space-y-6">
              <Card>
                <CardHeader title="Pipeline by status" />
                <ul className="divide-y">
                  {Object.entries(q.data.status_breakdown).length === 0 && <li className="px-5 py-4 text-sm text-muted-foreground">No assessments.</li>}
                  {Object.entries(q.data.status_breakdown).map(([s, n]) => (
                    <li key={s} className="flex items-center justify-between px-5 py-2.5 text-sm"><span>{STATUS_LABELS[s] ?? s}</span><Badge>{n}</Badge></li>
                  ))}
                </ul>
              </Card>
              <Card>
                <CardHeader title="Latest model recommendations" description="Decision support only — pending human review" />
                <ul className="divide-y">
                  {Object.entries(q.data.recommendation_outcomes).length === 0 && <li className="px-5 py-4 text-sm text-muted-foreground">No recommendations generated.</li>}
                  {Object.entries(q.data.recommendation_outcomes).map(([o, n]) => (
                    <li key={o} className="flex items-center justify-between px-5 py-2.5 text-sm"><span>{OUTCOME_LABELS[o] ?? o}</span><Badge>{n}</Badge></li>
                  ))}
                </ul>
              </Card>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
