"use client";

import { useQuery } from "@tanstack/react-query";

import { Card, CardHeader, EmptyState, LoadingBlock } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { date } from "@/lib/format";
import type { AuditEntry } from "@/lib/types";

export function AuditTimeline({ items }: { items: AuditEntry[] }) {
  if (!items.length) return <div className="p-5"><EmptyState title="No audit events" /></div>;
  return (
    <ol className="relative space-y-0 px-5 py-4">
      {items.map((e) => (
        <li key={e.id} className="relative border-l pb-4 pl-5 last:pb-0">
          <span className="absolute -left-[5px] top-1.5 size-2.5 rounded-full border-2 border-card bg-primary" />
          <div className="flex flex-wrap items-baseline gap-x-3 text-sm"><span className="font-medium">{e.summary}</span><code className="text-[11px] text-muted-foreground">{e.action}</code></div>
          <div className="text-xs text-muted-foreground">{date(e.created_at, true)} · {e.user_name ?? "System"}{e.ip_address ? ` · ${e.ip_address}` : ""}</div>
        </li>
      ))}
    </ol>
  );
}

export function AuditTab({ appId }: { appId: number }) {
  const q = useQuery({ queryKey: ["audit", appId], queryFn: () => api.get<{ items: AuditEntry[] }>(`/assessments/${appId}/audit`) });
  return (
    <Card>
      <CardHeader title="Audit trail" description="Append-only record of every action on this assessment (the database rejects edits and deletions)." />
      {q.isLoading ? <LoadingBlock /> : <AuditTimeline items={q.data?.items ?? []} />}
    </Card>
  );
}
