"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { FolderKanban, Plus, Search } from "lucide-react";
import Link from "next/link";
import { useDeferredValue, useState } from "react";

import { OutcomeBadge, StatusBadge } from "@/components/status";
import { Button, Card, EmptyState, ErrorState, Input, LoadingBlock, PageHeader, Select, Table, Td, Th } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { date, inr, LOAN_TYPES, STATUS_LABELS } from "@/lib/format";
import { useCan } from "@/lib/session";
import type { Assessment } from "@/lib/types";

export default function AssessmentsPage() {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const canWrite = useCan("assessments:write");
  const dq = useDeferredValue(q);
  const params = new URLSearchParams({ ...(dq ? { q: dq } : {}), ...(status ? { status } : {}) }).toString();
  const list = useQuery({ queryKey: ["assessments", params], queryFn: () => api.get<{ items: Assessment[]; total: number }>(`/assessments?${params}`), placeholderData: keepPreviousData });

  return (
    <>
      <PageHeader title="Credit assessments" description="Every assessment, its processing state and the model recommendation awaiting human review."
        actions={canWrite && <Button asChild><Link href="/assessments/new"><Plus />New assessment</Link></Button>} />
      <Card>
        <div className="flex flex-wrap items-center gap-3 border-b p-4">
          <div className="relative min-w-[260px] flex-1">
            <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by borrower or reference" className="pl-9" aria-label="Search assessments" />
          </div>
          <Select value={status} onChange={(e) => setStatus(e.target.value)} className="w-56" aria-label="Filter by status">
            <option value="">All statuses</option>
            {Object.entries(STATUS_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
        </div>
        {list.isLoading ? <LoadingBlock /> : list.error ? <div className="p-5"><ErrorState error={list.error} /></div> : !list.data?.items.length ? (
          <div className="p-5"><EmptyState icon={<FolderKanban />} title="No assessments found" action={canWrite && <Button asChild size="sm"><Link href="/assessments/new">Start an assessment</Link></Button>} /></div>
        ) : (
          <Table>
            <thead><tr><Th>Reference</Th><Th>Borrower</Th><Th>Facility</Th><Th className="text-right">Requested</Th><Th className="text-right">Recommended</Th><Th>Risk score</Th><Th>Status</Th><Th>Updated</Th></tr></thead>
            <tbody>
              {list.data.items.map((a) => (
                <tr key={a.id} className="hover:bg-muted/40">
                  <Td><Link href={`/assessments/${a.id}`} className="font-medium text-primary hover:underline">{a.reference}</Link></Td>
                  <Td className="max-w-[260px] truncate">{a.company_name}</Td>
                  <Td>{LOAN_TYPES[a.loan_type]} · {a.tenure_months}m</Td>
                  <Td className="num text-right">{inr(a.requested_amount)}</Td>
                  <Td className="num text-right">{a.recommended_amount != null ? inr(a.recommended_amount) : "—"}</Td>
                  <Td>{a.risk_score != null ? <span className="num">{a.risk_score.toFixed(1)} <span className="text-xs text-muted-foreground">{a.risk_category}</span></span> : <span className="text-muted-foreground">—</span>}</Td>
                  <Td><div className="flex flex-col items-start gap-1"><StatusBadge status={a.status} />{a.recommendation_outcome && <OutcomeBadge outcome={a.recommendation_outcome} />}</div></Td>
                  <Td className="text-muted-foreground">{date(a.updated_at)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}
