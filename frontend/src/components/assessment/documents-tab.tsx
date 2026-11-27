"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, FileText, Play, RefreshCw, Trash2, UploadCloud } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { SeverityBadge, StatusBadge } from "@/components/status";
import { Dialog } from "@/components/ui/overlay";
import { Badge, Button, Callout, Card, CardHeader, EmptyState, ErrorState, LoadingBlock, Progress, Select, Table, Td, Th } from "@/components/ui/primitives";
import { api, errorMessage, fileUrl } from "@/lib/api";
import { bytes, date, DOC_CATEGORIES, inr, label } from "@/lib/format";
import { useCan } from "@/lib/session";
import type { Doc, Metric } from "@/lib/types";

const ACCEPT = ".pdf,.docx,.xlsx,.csv,.png,.jpg,.jpeg";

export function useDocuments(appId: number) {
  return useQuery({
    queryKey: ["docs", appId],
    queryFn: () => api.get<{ items: Doc[] }>(`/assessments/${appId}/documents`),
    refetchInterval: (q) => (q.state.data?.items.some((d) => ["queued", "processing"].includes(d.processing_status)) ? 1500 : false),
  });
}

function UploadPanel({ appId, closed }: { appId: number; closed: boolean }) {
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [category, setCategory] = useState("auto");
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState<string[]>([]);

  const send = async (files: FileList | File[]) => {
    for (const f of Array.from(files)) {
      setBusy((b) => [...b, f.name]);
      const form = new FormData();
      form.append("file", f);
      form.append("category", category);
      try {
        await api.upload(`/assessments/${appId}/documents`, form);
        toast.success(`${f.name} uploaded — processing started`);
      } catch (e) {
        toast.error(`${f.name}: ${errorMessage(e)}`);
      } finally {
        setBusy((b) => b.filter((n) => n !== f.name));
        qc.invalidateQueries({ queryKey: ["docs", appId] });
        qc.invalidateQueries({ queryKey: ["assessment", appId] });
      }
    }
  };

  if (closed) return <Callout>This assessment is closed. Documents can no longer be added.</Callout>;
  return (
    <Card>
      <CardHeader title="Upload financial documents" description="PDF, DOCX, XLSX, CSV, JPG or PNG up to 25 MB. File contents are verified against the extension; scanned pages are OCR'd." />
      <div className="space-y-3 p-5">
        <div className="flex flex-wrap items-center gap-3">
          <label htmlFor="doc-category" className="text-sm font-medium">Document type</label>
          <Select id="doc-category" value={category} onChange={(e) => setCategory(e.target.value)} className="w-60">
            {Object.entries(DOC_CATEGORIES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
          <span className="text-xs text-muted-foreground">Auto-detect classifies each file from its content.</span>
        </div>
        <div onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files.length) send(e.dataTransfer.files); }}
          className={`flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-6 py-10 text-center transition-colors ${drag ? "border-primary bg-accent/50" : "bg-muted/30"}`}>
          <UploadCloud className="size-8 text-muted-foreground" />
          <p className="text-sm"><span className="font-medium">Drag files here</span> or</p>
          <Button variant="outline" size="sm" onClick={() => input.current?.click()} loading={busy.length > 0}>Choose files</Button>
          <input ref={input} type="file" multiple accept={ACCEPT} className="sr-only" data-testid="file-input" aria-label="Choose documents to upload"
            onChange={(e) => { if (e.target.files?.length) send(e.target.files); e.target.value = ""; }} />
          {busy.length > 0 && <p className="text-xs text-muted-foreground">Uploading {busy.join(", ")}…</p>}
        </div>
      </div>
    </Card>
  );
}

interface Extraction {
  document: Doc; metrics: Metric[]; signals: { id: number; label: string; severity: string; excerpt: string | null; page_number: number | null }[];
  pages: { page_number: number; method: string; char_count: number; ocr_confidence: number | null; table_count: number }[];
  bank_transactions: { total: number; items: { id: number; txn_date: string; description: string; debit: number | null; credit: number | null; category: string; is_flagged: boolean }[] };
  gst_records: { id: number; return_type: string; period: string; taxable_value: number | null; itc_claimed: number | null; itc_available: number | null }[];
}

function DocumentDetail({ doc, onClose }: { doc: Doc; onClose: () => void }) {
  const q = useQuery({ queryKey: ["extraction", doc.id], queryFn: () => api.get<Extraction>(`/documents/${doc.id}/extraction`) });
  const d = q.data;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} wide title={doc.original_filename} description={`${DOC_CATEGORIES[doc.category] ?? doc.category} · ${doc.page_count ?? "?"} page(s) · uploaded ${date(doc.created_at, true)}`}
      footer={<>{doc.previewable && <Button variant="outline" asChild><a href={fileUrl(`/documents/${doc.id}/file?inline=true`)} target="_blank" rel="noopener noreferrer"><Eye />Preview original</a></Button>}
        <Button variant="outline" asChild><a href={fileUrl(`/documents/${doc.id}/file`)}>Download</a></Button></>}>
      {q.isLoading && <LoadingBlock />}
      {q.error && <ErrorState error={q.error} />}
      {d && (
        <div className="space-y-5">
          <div className="flex flex-wrap gap-2">
            <StatusBadge status={d.document.extraction_status} />
            {d.document.detected_unit && <Badge tone="info">Unit: ₹ {d.document.detected_unit}</Badge>}
            {d.document.detected_periods.map((p) => <Badge key={p}>{p}</Badge>)}
            {d.document.ocr_used && <Badge tone="warning">OCR {d.document.ocr_confidence ? `${(d.document.ocr_confidence * 100).toFixed(0)}%` : ""}</Badge>}
            {d.document.suggested_category && d.document.suggested_category !== d.document.category && <Badge tone="warning">Classifier suggests: {DOC_CATEGORIES[d.document.suggested_category]}</Badge>}
          </div>
          {d.document.warnings.length > 0 && <Callout tone="warning" title="Extraction warnings"><ul className="list-disc pl-4">{d.document.warnings.map((w) => <li key={w}>{w}</li>)}</ul></Callout>}
          {d.signals.length > 0 && (
            <div><h4 className="mb-2 text-sm font-semibold">Qualitative risk indicators</h4>
              <ul className="space-y-2">{d.signals.map((s) => <li key={s.id} className="rounded-md border p-3 text-sm"><div className="flex items-center gap-2"><SeverityBadge severity={s.severity} /><span className="font-medium">{s.label}</span><span className="text-xs text-muted-foreground">page {s.page_number ?? "?"}</span></div>
                {s.excerpt && <p className="mt-1 text-xs text-muted-foreground">“{s.excerpt.slice(0, 280)}”</p>}</li>)}</ul></div>
          )}
          {d.metrics.length > 0 && (
            <div><h4 className="mb-2 text-sm font-semibold">Extracted financial values ({d.metrics.length})</h4>
              <Table><thead><tr><Th>Metric</Th><Th>Period</Th><Th className="text-right">Value</Th><Th>Raw</Th><Th>Page</Th><Th>Confidence</Th></tr></thead>
                <tbody>{d.metrics.map((m) => (
                  <tr key={m.id}><Td>{m.label}</Td><Td>{m.period_label ?? <span className="text-amber-700">not identified</span>}</Td>
                    <Td className="num text-right">{m.text_value ?? inr(m.effective_value)}</Td><Td className="font-mono text-xs text-muted-foreground">{m.raw_text}{m.unit && m.unit !== "absolute" ? ` ${m.unit}` : ""}</Td>
                    <Td>{m.page_number ?? "—"}</Td><Td><Badge tone={m.confidence >= 0.8 ? "success" : m.confidence >= 0.6 ? "warning" : "danger"}>{(m.confidence * 100).toFixed(0)}%</Badge></Td></tr>
                ))}</tbody></Table></div>
          )}
          {d.gst_records.length > 0 && (
            <div><h4 className="mb-2 text-sm font-semibold">GST return rows ({d.gst_records.length})</h4>
              <Table><thead><tr><Th>Return</Th><Th>Period</Th><Th className="text-right">Taxable value</Th><Th className="text-right">ITC claimed</Th><Th className="text-right">ITC available</Th></tr></thead>
                <tbody>{d.gst_records.slice(0, 30).map((g) => <tr key={g.id}><Td>{g.return_type}</Td><Td>{g.period}</Td><Td className="num text-right">{inr(g.taxable_value)}</Td><Td className="num text-right">{inr(g.itc_claimed)}</Td><Td className="num text-right">{inr(g.itc_available)}</Td></tr>)}</tbody></Table></div>
          )}
          {d.bank_transactions.total > 0 && (
            <div><h4 className="mb-2 text-sm font-semibold">Bank transactions ({d.bank_transactions.total})</h4>
              <Table><thead><tr><Th>Date</Th><Th>Description</Th><Th>Category</Th><Th className="text-right">Debit</Th><Th className="text-right">Credit</Th></tr></thead>
                <tbody>{d.bank_transactions.items.slice(0, 40).map((t) => <tr key={t.id} className={t.is_flagged ? "bg-red-50/60" : ""}><Td className="whitespace-nowrap">{date(t.txn_date)}</Td><Td className="max-w-[260px] truncate text-xs">{t.description}</Td><Td className="text-xs">{label(t.category)}</Td><Td className="num text-right">{t.debit ? inr(t.debit) : ""}</Td><Td className="num text-right">{t.credit ? inr(t.credit) : ""}</Td></tr>)}</tbody></Table>
              {d.bank_transactions.total > 40 && <p className="mt-1 text-xs text-muted-foreground">Showing the first 40 transactions.</p>}</div>
          )}
          <div><h4 className="mb-2 text-sm font-semibold">Pages read</h4>
            <div className="flex flex-wrap gap-2">{d.pages.map((p) => <Badge key={p.page_number}>p.{p.page_number} · {p.method}{p.table_count ? ` · ${p.table_count} table(s)` : ""}</Badge>)}</div></div>
        </div>
      )}
    </Dialog>
  );
}

export function DocumentsTab({ appId, closed }: { appId: number; closed: boolean }) {
  const qc = useQueryClient();
  const docs = useDocuments(appId);
  const canWrite = useCan("documents:write");
  const [detail, setDetail] = useState<Doc | null>(null);
  const start = useMutation({
    mutationFn: () => api.post<{ queued_jobs: number[] }>(`/assessments/${appId}/start`),
    onSuccess: (r) => { toast.success(r.queued_jobs.length ? `${r.queued_jobs.length} document(s) queued for processing` : "All documents already processed or in progress"); qc.invalidateQueries({ queryKey: ["docs", appId] }); qc.invalidateQueries({ queryKey: ["assessment", appId] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const reprocess = useMutation({ mutationFn: (id: number) => api.post(`/documents/${id}/reprocess`), onSuccess: () => qc.invalidateQueries({ queryKey: ["docs", appId] }), onError: (e) => toast.error(errorMessage(e)) });
  const remove = useMutation({
    mutationFn: (id: number) => api.delete(`/documents/${id}`),
    onSuccess: () => { toast.success("Document deleted"); qc.invalidateQueries({ queryKey: ["docs", appId] }); qc.invalidateQueries({ queryKey: ["financials", appId] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const items = docs.data?.items ?? [];
  const pending = items.filter((d) => ["queued", "processing"].includes(d.processing_status)).length;
  return (
    <div className="space-y-6">
      {canWrite && <UploadPanel appId={appId} closed={closed} />}
      <Card>
        <CardHeader title={`Documents (${items.length})`} description={pending ? `${pending} document(s) processing in the background…` : "Processing runs asynchronously; this list refreshes automatically."}
          action={canWrite && items.length > 0 && !closed && <Button size="sm" variant="outline" onClick={() => start.mutate()} loading={start.isPending}><Play />Start / resume processing</Button>} />
        {docs.isLoading ? <LoadingBlock /> : docs.error ? <div className="p-5"><ErrorState error={docs.error} /></div> : items.length === 0 ? (
          <div className="p-5"><EmptyState icon={<FileText />} title="No documents uploaded" description="Upload financial statements, GST returns and bank statements — these three are required for a confident assessment." /></div>
        ) : (
          <Table>
            <thead><tr><Th>Document</Th><Th>Type</Th><Th>Uploaded</Th><Th>Processing</Th><Th>Values</Th><Th>Confidence</Th><Th>Periods</Th><Th className="text-right">Actions</Th></tr></thead>
            <tbody>
              {items.map((d) => (
                <tr key={d.id} className="hover:bg-muted/40" data-testid={`doc-row-${d.id}`}>
                  <Td><button onClick={() => setDetail(d)} className="text-left font-medium text-primary hover:underline">{d.original_filename}</button>
                    <div className="text-xs text-muted-foreground">{d.file_kind.toUpperCase()} · {bytes(d.file_size)}{d.page_count ? ` · ${d.page_count} page(s)` : ""}{d.ocr_used ? " · OCR" : ""}</div></Td>
                  <Td>{DOC_CATEGORIES[d.category] ?? d.category}{d.classification_confidence != null && <div className="text-xs text-muted-foreground">classifier {(d.classification_confidence * 100).toFixed(0)}%</div>}</Td>
                  <Td className="whitespace-nowrap text-muted-foreground">{date(d.created_at, true)}</Td>
                  <Td className="min-w-[150px]">
                    {["queued", "processing"].includes(d.processing_status) ? <div className="space-y-1"><StatusBadge status={d.processing_status} /><Progress value={d.processing_progress} /></div>
                      : <div className="space-y-1"><StatusBadge status={d.processing_status === "completed" ? d.extraction_status : d.processing_status} />
                        {d.error_message && <p className="max-w-[220px] text-xs text-red-700">{d.error_message}</p>}
                        {d.warnings.length > 0 && <p className="text-xs text-amber-700">{d.warnings.length} warning(s)</p>}</div>}
                  </Td>
                  <Td className="num">{d.metric_count ?? 0}</Td>
                  <Td>{d.average_confidence != null ? `${(d.average_confidence * 100).toFixed(0)}%` : "—"}</Td>
                  <Td className="text-xs">{d.detected_periods.join(", ") || "—"}</Td>
                  <Td className="text-right">
                    <div className="flex justify-end gap-1">
                      <Button size="icon" variant="ghost" onClick={() => setDetail(d)} aria-label={`View ${d.original_filename}`}><Eye /></Button>
                      {canWrite && !closed && d.processing_status === "failed" && <Button size="icon" variant="ghost" onClick={() => reprocess.mutate(d.id)} aria-label="Retry processing"><RefreshCw /></Button>}
                      {canWrite && !closed && <Button size="icon" variant="ghost" aria-label={`Delete ${d.original_filename}`} onClick={() => { if (confirm(`Delete ${d.original_filename}? Extracted values from this file will be removed.`)) remove.mutate(d.id); }}><Trash2 /></Button>}
                    </div>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
      {detail && <DocumentDetail doc={detail} onClose={() => setDetail(null)} />}
    </div>
  );
}
