"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Building2, Plus, Search } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useDeferredValue, useState } from "react";

import { BorrowerDialog } from "@/components/borrower-form";
import { SyntheticBadge } from "@/components/status";
import { Button, Card, EmptyState, ErrorState, Input, LoadingBlock, PageHeader, Select, Table, Td, Th } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { date, inr } from "@/lib/format";
import { useCan } from "@/lib/session";
import type { Borrower } from "@/lib/types";

export default function BorrowersPage() {
  const [q, setQ] = useState("");
  const [industry, setIndustry] = useState("");
  const [open, setOpen] = useState(false);
  const router = useRouter();
  const canWrite = useCan("borrowers:write");
  const dq = useDeferredValue(q);
  const params = new URLSearchParams({ ...(dq ? { q: dq } : {}), ...(industry ? { industry } : {}) }).toString();
  const list = useQuery({ queryKey: ["borrowers", params], queryFn: () => api.get<{ items: Borrower[]; total: number; industries: string[] }>(`/borrowers?${params}`), placeholderData: keepPreviousData });

  return (
    <>
      <PageHeader title="Borrowers" description="MSME borrowers registered in your organisation."
        actions={canWrite && <Button onClick={() => setOpen(true)}><Plus />Add borrower</Button>} />
      <Card>
        <div className="flex flex-wrap items-center gap-3 border-b p-4">
          <div className="relative min-w-[260px] flex-1">
            <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by name, PAN, GSTIN, CIN or industry" className="pl-9" aria-label="Search borrowers" />
          </div>
          <Select value={industry} onChange={(e) => setIndustry(e.target.value)} className="w-52" aria-label="Filter by industry">
            <option value="">All industries</option>
            {list.data?.industries.map((i) => <option key={i}>{i}</option>)}
          </Select>
          <span className="text-sm text-muted-foreground">{list.data ? `${list.data.total} borrower(s)` : ""}</span>
        </div>
        {list.isLoading ? <LoadingBlock /> : list.error ? <div className="p-5"><ErrorState error={list.error} onRetry={() => list.refetch()} /></div> :
          !list.data?.items.length ? (
            <div className="p-5"><EmptyState icon={<Building2 />} title={q || industry ? "No borrowers match your filters" : "No borrowers yet"}
              description={q || industry ? "Try a different search term." : "Add your first MSME borrower to begin a credit assessment."}
              action={canWrite && !q && <Button size="sm" onClick={() => setOpen(true)}>Add borrower</Button>} /></div>
          ) : (
            <Table>
              <thead><tr><Th>Borrower</Th><Th>Industry</Th><Th>PAN</Th><Th>GSTIN</Th><Th className="text-right">Declared turnover</Th><Th className="text-right">Assessments</Th><Th>Last activity</Th></tr></thead>
              <tbody>
                {list.data.items.map((b) => (
                  <tr key={b.id} className="cursor-pointer hover:bg-muted/40" onClick={() => router.push(`/borrowers/${b.id}`)}>
                    <Td><Link href={`/borrowers/${b.id}`} className="font-medium text-primary hover:underline" onClick={(e) => e.stopPropagation()}>{b.name}</Link>
                      <div className="mt-0.5 flex gap-2 text-xs text-muted-foreground">{b.business_type}{b.is_synthetic && <SyntheticBadge />}</div></Td>
                    <Td>{b.industry}</Td>
                    <Td className="font-mono text-xs">{b.pan ?? "—"}</Td>
                    <Td className="font-mono text-xs">{b.gstin ?? "—"}</Td>
                    <Td className="num text-right">{inr(b.annual_turnover)}</Td>
                    <Td className="num text-right">{b.assessment_count ?? 0}</Td>
                    <Td className="text-muted-foreground">{date(b.last_activity ?? b.updated_at)}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
      </Card>
      <BorrowerDialog open={open} onOpenChange={setOpen} onCreated={(b) => router.push(`/borrowers/${b.id}`)} />
    </>
  );
}
