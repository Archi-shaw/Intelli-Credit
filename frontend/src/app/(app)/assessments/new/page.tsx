"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Check, Plus, Search, Trash2 } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { toast } from "sonner";

import { BorrowerDialog } from "@/components/borrower-form";
import { WorkflowStepper } from "@/components/workflow";
import { Button, Callout, Card, CardBody, CardHeader, Field, Input, PageHeader, Select, Textarea } from "@/components/ui/primitives";
import { api, ApiError, errorMessage } from "@/lib/api";
import { inr, LOAN_TYPES } from "@/lib/format";
import type { Assessment, Borrower } from "@/lib/types";

type Collateral = { type: string; description: string; value: string };
type Borrowing = { lender: string; facility_type: string; outstanding_amount: string; interest_rate: string; remaining_tenure_months: string };

function Wizard() {
  const router = useRouter();
  const params = useSearchParams();
  const [step, setStep] = useState(1);
  const [borrower, setBorrower] = useState<Borrower | null>(null);
  const [q, setQ] = useState("");
  const [addOpen, setAddOpen] = useState(false);
  const [form, setForm] = useState({ loan_type: "term_loan", requested_amount: "", tenure_months: "60", purpose: "", repayment_frequency: "monthly", moratorium_months: "0" });
  const [collateral, setCollateral] = useState<Collateral[]>([]);
  const [borrowings, setBorrowings] = useState<Borrowing[]>([]);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const list = useQuery({ queryKey: ["borrowers", "picker", q], queryFn: () => api.get<{ items: Borrower[] }>(`/borrowers?limit=20${q ? `&q=${encodeURIComponent(q)}` : ""}`) });
  const preselect = params.get("borrower");
  const pre = useQuery({ queryKey: ["borrower", Number(preselect)], queryFn: () => api.get<Borrower>(`/borrowers/${preselect}`), enabled: !!preselect });
  useEffect(() => { if (pre.data && !borrower) { setBorrower(pre.data); setStep(2); } }, [pre.data, borrower]);

  const create = useMutation({
    mutationFn: () => api.post<Assessment>("/assessments", {
      company_id: borrower!.id, loan_type: form.loan_type, requested_amount: Number(form.requested_amount), tenure_months: Number(form.tenure_months),
      purpose: form.purpose, repayment_frequency: form.repayment_frequency, moratorium_months: Number(form.moratorium_months || 0),
      collateral: collateral.filter((c) => c.value).map((c) => ({ type: c.type, description: c.description || undefined, value: Number(c.value) })),
      existing_borrowings: borrowings.filter((b) => b.lender).map((b) => ({
        lender: b.lender, facility_type: b.facility_type, outstanding_amount: b.outstanding_amount ? Number(b.outstanding_amount) : undefined,
        interest_rate: b.interest_rate ? Number(b.interest_rate) : undefined, remaining_tenure_months: b.remaining_tenure_months ? Number(b.remaining_tenure_months) : undefined,
      })),
    }),
    onSuccess: (a) => { toast.success(`Assessment ${a.reference} created`); router.push(`/assessments/${a.id}?tab=documents`); },
    onError: (e) => { if (e instanceof ApiError && e.status === 422) setErrors({ form: e.message }); },
  });

  const validate = () => {
    const e: Record<string, string> = {};
    const amt = Number(form.requested_amount);
    if (!form.requested_amount || !Number.isFinite(amt) || amt <= 0) e.requested_amount = "Enter the requested amount in ₹";
    const t = Number(form.tenure_months);
    if (!Number.isInteger(t) || t < 1 || t > 360) e.tenure_months = "Tenure must be 1–360 months";
    if (form.purpose.trim().length < 5) e.purpose = "Describe the purpose (at least 5 characters)";
    collateral.forEach((c, i) => { if (c.value && (Number(c.value) < 0 || Number.isNaN(Number(c.value)))) e[`col${i}`] = "Invalid value"; });
    setErrors(e);
    return Object.keys(e).length === 0;
  };

  const set = (k: keyof typeof form) => (ev: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setForm({ ...form, [k]: ev.target.value });

  return (
    <>
      <PageHeader title="New credit assessment" description="Steps 1–2 capture the request; documents, processing, reconciliation, assessment, review and CAM follow in the assessment workspace." />
      <WorkflowStepper steps={["Select borrower", "Loan request", "Upload documents", "Process documents", "Review extraction", "Run reconciliation", "Credit assessment", "Review recommendation", "Generate CAM"].map((n, i) => ({ index: i + 1, name: n, done: i + 1 < step }))} current={step} />

      {step === 1 && (
        <Card className="mt-6">
          <CardHeader title="Step 1 · Select borrower" action={<Button size="sm" variant="outline" onClick={() => setAddOpen(true)}><Plus />Add new borrower</Button>} />
          <CardBody>
            <div className="relative mb-4"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input placeholder="Search borrowers" value={q} onChange={(e) => setQ(e.target.value)} className="pl-9" aria-label="Search borrowers" /></div>
            {list.data && list.data.items.length === 0 && <Callout>No borrowers found. Add a borrower to continue.</Callout>}
            <ul className="grid gap-2 sm:grid-cols-2">
              {list.data?.items.map((b) => (
                <li key={b.id}>
                  <button onClick={() => { setBorrower(b); setStep(2); }} className={`w-full rounded-md border p-3 text-left hover:border-primary hover:bg-accent/40 ${borrower?.id === b.id ? "border-primary bg-accent/50" : ""}`}>
                    <div className="font-medium">{b.name}</div>
                    <div className="text-xs text-muted-foreground">{b.industry} · {b.pan ?? "PAN not provided"}{b.is_synthetic ? " · synthetic" : ""}</div>
                  </button>
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      )}

      {step === 2 && borrower && (
        <Card className="mt-6">
          <CardHeader title="Step 2 · Loan request" description={<>Borrower: <span className="font-medium text-foreground">{borrower.name}</span> · <button className="text-primary hover:underline" onClick={() => setStep(1)}>change</button></>} />
          <CardBody className="space-y-6">
            {(errors.form || create.error) && <Callout tone="danger">{errors.form ?? errorMessage(create.error)}</Callout>}
            <div className="grid gap-4 md:grid-cols-3">
              <Field label="Facility type" htmlFor="loan_type" required><Select id="loan_type" value={form.loan_type} onChange={set("loan_type")}>{Object.entries(LOAN_TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Select></Field>
              <Field label="Requested amount (₹)" htmlFor="requested_amount" required error={errors.requested_amount} hint={form.requested_amount && !errors.requested_amount ? inr(Number(form.requested_amount)) : "e.g. 30000000 for ₹3 Cr"}>
                <Input id="requested_amount" inputMode="numeric" value={form.requested_amount} onChange={set("requested_amount")} aria-invalid={!!errors.requested_amount} /></Field>
              <Field label="Tenure (months)" htmlFor="tenure_months" required error={errors.tenure_months}><Input id="tenure_months" inputMode="numeric" value={form.tenure_months} onChange={set("tenure_months")} /></Field>
              <Field label="Repayment frequency" htmlFor="repayment_frequency"><Select id="repayment_frequency" value={form.repayment_frequency} onChange={set("repayment_frequency")}>
                <option value="monthly">Monthly</option><option value="quarterly">Quarterly</option><option value="half_yearly">Half-yearly</option><option value="bullet">Bullet</option></Select></Field>
              <Field label="Moratorium (months)" htmlFor="moratorium_months"><Input id="moratorium_months" inputMode="numeric" value={form.moratorium_months} onChange={set("moratorium_months")} /></Field>
            </div>
            <Field label="Purpose of the facility" htmlFor="purpose" required error={errors.purpose}><Textarea id="purpose" rows={2} value={form.purpose} onChange={set("purpose")} /></Field>

            <div>
              <div className="mb-2 flex items-center justify-between"><h4 className="text-sm font-semibold">Collateral offered</h4>
                <Button size="sm" variant="outline" onClick={() => setCollateral([...collateral, { type: "property", description: "", value: "" }])}><Plus />Add collateral</Button></div>
              {collateral.length === 0 && <p className="text-xs text-muted-foreground">None declared — the facility will be assessed as unsecured.</p>}
              {collateral.map((c, i) => (
                <div key={i} className="mb-2 grid gap-2 sm:grid-cols-[180px_1fr_180px_40px]">
                  <Select aria-label="Collateral type" value={c.type} onChange={(e) => setCollateral(collateral.map((x, j) => j === i ? { ...x, type: e.target.value } : x))}>
                    {["property", "plant_machinery", "fixed_deposit", "receivables", "inventory", "shares", "vehicle", "other"].map((t) => <option key={t} value={t}>{t.replace("_", " ")}</option>)}</Select>
                  <Input aria-label="Collateral description" placeholder="Description" value={c.description} onChange={(e) => setCollateral(collateral.map((x, j) => j === i ? { ...x, description: e.target.value } : x))} />
                  <Input aria-label="Collateral value" placeholder="Value (₹)" inputMode="numeric" value={c.value} aria-invalid={!!errors[`col${i}`]} onChange={(e) => setCollateral(collateral.map((x, j) => j === i ? { ...x, value: e.target.value } : x))} />
                  <Button variant="ghost" size="icon" aria-label="Remove collateral" onClick={() => setCollateral(collateral.filter((_, j) => j !== i))}><Trash2 /></Button>
                </div>
              ))}
            </div>
            <div>
              <div className="mb-2 flex items-center justify-between"><h4 className="text-sm font-semibold">Existing borrowings (declared)</h4>
                <Button size="sm" variant="outline" onClick={() => setBorrowings([...borrowings, { lender: "", facility_type: "term_loan", outstanding_amount: "", interest_rate: "", remaining_tenure_months: "" }])}><Plus />Add borrowing</Button></div>
              {borrowings.length === 0 && <p className="text-xs text-muted-foreground">None declared. Debt in the financial statements will still be reconciled against this declaration.</p>}
              {borrowings.map((b, i) => (
                <div key={i} className="mb-2 grid gap-2 sm:grid-cols-[1fr_150px_150px_110px_110px_40px]">
                  <Input aria-label="Lender" placeholder="Lender" value={b.lender} onChange={(e) => setBorrowings(borrowings.map((x, j) => j === i ? { ...x, lender: e.target.value } : x))} />
                  <Select aria-label="Facility" value={b.facility_type} onChange={(e) => setBorrowings(borrowings.map((x, j) => j === i ? { ...x, facility_type: e.target.value } : x))}>{Object.entries(LOAN_TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Select>
                  <Input aria-label="Outstanding" placeholder="Outstanding ₹" inputMode="numeric" value={b.outstanding_amount} onChange={(e) => setBorrowings(borrowings.map((x, j) => j === i ? { ...x, outstanding_amount: e.target.value } : x))} />
                  <Input aria-label="Rate" placeholder="Rate %" inputMode="decimal" value={b.interest_rate} onChange={(e) => setBorrowings(borrowings.map((x, j) => j === i ? { ...x, interest_rate: e.target.value } : x))} />
                  <Input aria-label="Months left" placeholder="Months left" inputMode="numeric" value={b.remaining_tenure_months} onChange={(e) => setBorrowings(borrowings.map((x, j) => j === i ? { ...x, remaining_tenure_months: e.target.value } : x))} />
                  <Button variant="ghost" size="icon" aria-label="Remove borrowing" onClick={() => setBorrowings(borrowings.filter((_, j) => j !== i))}><Trash2 /></Button>
                </div>
              ))}
            </div>
            <div className="flex justify-between border-t pt-4">
              <Button variant="outline" onClick={() => setStep(1)}>Back</Button>
              <Button onClick={() => validate() && create.mutate()} loading={create.isPending}><Check />Create assessment & continue to documents</Button>
            </div>
          </CardBody>
        </Card>
      )}
      <BorrowerDialog open={addOpen} onOpenChange={setAddOpen} onCreated={(b) => { setBorrower(b); setStep(2); }} />
    </>
  );
}

export default function NewAssessmentPage() {
  return <Suspense><Wizard /></Suspense>;
}
