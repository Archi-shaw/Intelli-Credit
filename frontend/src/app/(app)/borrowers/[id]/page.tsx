"use client";

import { useQuery } from "@tanstack/react-query";
import { FilePlus2, Pencil } from "lucide-react";
import Link from "next/link";
import { use, useState } from "react";

import { BorrowerDialog } from "@/components/borrower-form";
import { StatementTable } from "@/components/financials";
import { OutcomeBadge, StatusBadge, SyntheticBadge } from "@/components/status";
import { Button, Callout, Card, CardBody, CardHeader, EmptyState, ErrorState, KV, LoadingBlock, PageHeader, Table, Td, Th } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { date, inr, LOAN_TYPES } from "@/lib/format";
import { useCan } from "@/lib/session";
import type { Borrower, Statement } from "@/lib/types";

export default function BorrowerPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [edit, setEdit] = useState(false);
  const canWrite = useCan("borrowers:write");
  const b = useQuery({ queryKey: ["borrower", Number(id)], queryFn: () => api.get<Borrower>(`/borrowers/${id}`) });
  const fin = useQuery({ queryKey: ["borrower-fin", Number(id)], queryFn: () => api.get<{ periods: (Statement & { application_reference: string })[] }>(`/borrowers/${id}/financials`) });

  if (b.isLoading) return <LoadingBlock />;
  if (b.error || !b.data) return <ErrorState error={b.error} onRetry={() => b.refetch()} />;
  const c = b.data;
  return (
    <>
      <PageHeader eyebrow={<Link href="/borrowers" className="hover:underline">Borrowers</Link>} title={<span className="flex items-center gap-3">{c.name}{c.is_synthetic && <SyntheticBadge />}</span>}
        description={`${c.business_type} · ${c.industry}`}
        actions={canWrite && <>
          <Button variant="outline" onClick={() => setEdit(true)}><Pencil />Edit</Button>
          <Button asChild><Link href={`/assessments/new?borrower=${c.id}`}><FilePlus2 />New assessment</Link></Button>
        </>} />
      {c.is_synthetic && <Callout tone="warning" className="mb-6" title="Synthetic demonstration borrower">All information about this borrower is fictitious and generated for demonstration. It does not describe a real business or person.</Callout>}
      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="Profile" />
          <CardBody>
            <KV items={[["PAN", c.pan], ["GSTIN", c.gstin], ["CIN", c.cin], ["Incorporated", date(c.incorporation_date)], ["Headquarters", c.headquarters],
              ["Declared turnover", inr(c.annual_turnover)], ["Contact", c.contact_person], ["Registered address", c.registered_address]]} />
            {c.business_description && <p className="mt-5 border-t pt-4 text-sm text-muted-foreground">{c.business_description}</p>}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Directors & promoters" />
          <CardBody className="space-y-4 text-sm">
            {!c.directors?.length && !c.promoters?.length && <p className="text-muted-foreground">Not provided.</p>}
            {c.directors?.map((d) => <div key={`d${d.id}`}><div className="font-medium">{d.name}</div><div className="text-xs text-muted-foreground">{d.designation ?? "Director"}{d.din ? ` · DIN ${d.din}` : ""}</div></div>)}
            {c.promoters?.map((p) => <div key={`p${p.id}`}><div className="font-medium">{p.name}</div><div className="text-xs text-muted-foreground">Promoter{p.shareholding_pct != null ? ` · ${p.shareholding_pct}% holding` : ""}{p.experience_years ? ` · ${p.experience_years} yrs experience` : ""}</div></div>)}
          </CardBody>
        </Card>
      </div>
      <Card className="mt-6">
        <CardHeader title="Credit assessments" />
        {!c.assessments?.length ? <div className="p-5"><EmptyState title="No assessments for this borrower" action={canWrite && <Button asChild size="sm"><Link href={`/assessments/new?borrower=${c.id}`}>Start assessment</Link></Button>} /></div> : (
          <Table>
            <thead><tr><Th>Reference</Th><Th>Facility</Th><Th className="text-right">Requested</Th><Th>Tenure</Th><Th>Status</Th><Th>Created</Th></tr></thead>
            <tbody>{c.assessments.map((a) => (
              <tr key={a.id} className="hover:bg-muted/40"><Td><Link className="font-medium text-primary hover:underline" href={`/assessments/${a.id}`}>{a.reference}</Link></Td>
                <Td>{LOAN_TYPES[a.loan_type]}</Td><Td className="num text-right">{inr(a.requested_amount)}</Td><Td>{a.tenure_months} months</Td>
                <Td><StatusBadge status={a.status} />{a.recommendation_outcome && <OutcomeBadge outcome={a.recommendation_outcome} />}</Td><Td>{date(a.created_at)}</Td></tr>
            ))}</tbody>
          </Table>
        )}
      </Card>
      <Card className="mt-6">
        <CardHeader title="Financial information" description="Consolidated from documents processed in this borrower's assessments (₹ lakh)." />
        <CardBody>{fin.data?.periods.length ? <StatementTable periods={fin.data.periods} /> : <EmptyState title="No extracted financial statements yet" description="Upload financial statements in an assessment to populate this view." />}</CardBody>
      </Card>
      <BorrowerDialog open={edit} onOpenChange={setEdit} existing={c} />
    </>
  );
}
