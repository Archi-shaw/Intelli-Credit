"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { Dialog } from "@/components/ui/overlay";
import { Button, Callout, Field, Input, Select, Textarea } from "@/components/ui/primitives";
import { api, ApiError, errorMessage } from "@/lib/api";
import type { Borrower } from "@/lib/types";

const opt = (re: RegExp, msg: string) => z.string().trim().toUpperCase().refine((v) => !v || re.test(v), msg).optional();
const schema = z.object({
  name: z.string().trim().min(2, "Legal name is required"),
  industry: z.string().trim().min(2, "Industry is required"),
  business_type: z.string().min(2, "Constitution is required"),
  pan: opt(/^[A-Z]{5}[0-9]{4}[A-Z]$/, "PAN must look like ABCDE1234F"),
  gstin: opt(/^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/, "Enter a valid 15-character GSTIN"),
  cin: opt(/^[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$/, "Enter a valid 21-character CIN"),
  incorporation_date: z.string().optional(),
  headquarters: z.string().optional(),
  annual_turnover: z.string().optional().refine((v) => !v || (!Number.isNaN(Number(v)) && Number(v) >= 0), "Enter a valid amount in ₹"),
  contact_person: z.string().optional(),
  contact_email: z.string().trim().optional().refine((v) => !v || /^\S+@\S+\.\S+$/.test(v), "Enter a valid e-mail"),
  business_description: z.string().optional(),
});
type Values = z.infer<typeof schema>;

const CONSTITUTIONS = ["Private Limited", "Public Limited", "LLP", "Partnership", "Proprietorship", "One Person Company"];

export function BorrowerDialog({ open, onOpenChange, onCreated, existing }: { open: boolean; onOpenChange: (o: boolean) => void; onCreated?: (b: Borrower) => void; existing?: Borrower }) {
  const qc = useQueryClient();
  const { register, handleSubmit, formState: { errors }, reset, setError } = useForm<Values>({
    resolver: zodResolver(schema),
    values: existing ? {
      name: existing.name, industry: existing.industry, business_type: existing.business_type, pan: existing.pan ?? "", gstin: existing.gstin ?? "",
      cin: existing.cin ?? "", incorporation_date: existing.incorporation_date ?? "", headquarters: existing.headquarters ?? "",
      annual_turnover: existing.annual_turnover?.toString() ?? "", contact_person: existing.contact_person ?? "", contact_email: existing.contact_email ?? "",
      business_description: existing.business_description ?? "",
    } : undefined,
  });
  const m = useMutation({
    mutationFn: (v: Values) => {
      const body: Record<string, unknown> = {};
      for (const [k, val] of Object.entries(v)) if (val !== undefined && val !== "") body[k] = k === "annual_turnover" ? Number(val) : val;
      return existing ? api.patch<Borrower>(`/borrowers/${existing.id}`, body) : api.post<Borrower>("/borrowers", body);
    },
    onSuccess: (b) => {
      toast.success(existing ? "Borrower updated" : "Borrower created");
      qc.invalidateQueries({ queryKey: ["borrowers"] });
      qc.invalidateQueries({ queryKey: ["borrower", b.id] });
      reset();
      onOpenChange(false);
      onCreated?.(b);
    },
    onError: (e) => {
      if (e instanceof ApiError && e.code.startsWith("duplicate_")) setError(e.code.replace("duplicate_", "") as keyof Values, { message: e.message });
    },
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange} wide title={existing ? "Edit borrower" : "Add borrower"} description="Statutory identifiers are validated against Indian formats."
      footer={<><Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button><Button form="borrower-form" type="submit" loading={m.isPending}>{existing ? "Save changes" : "Create borrower"}</Button></>}>
      <form id="borrower-form" onSubmit={handleSubmit((v) => m.mutate(v))} className="grid gap-4 sm:grid-cols-2" noValidate>
        {m.error && !(m.error instanceof ApiError && m.error.code.startsWith("duplicate_")) && <Callout tone="danger" className="sm:col-span-2">{errorMessage(m.error)}</Callout>}
        <div className="sm:col-span-2"><Field label="Legal name" htmlFor="name" required error={errors.name?.message}><Input id="name" {...register("name")} /></Field></div>
        <Field label="Industry / sector" htmlFor="industry" required error={errors.industry?.message}><Input id="industry" placeholder="e.g. Auto Components" {...register("industry")} /></Field>
        <Field label="Constitution" htmlFor="business_type" required error={errors.business_type?.message}>
          <Select id="business_type" {...register("business_type")} defaultValue="Private Limited">{CONSTITUTIONS.map((c) => <option key={c}>{c}</option>)}</Select>
        </Field>
        <Field label="PAN" htmlFor="pan" error={errors.pan?.message}><Input id="pan" maxLength={10} className="uppercase" {...register("pan")} /></Field>
        <Field label="GSTIN" htmlFor="gstin" error={errors.gstin?.message}><Input id="gstin" maxLength={15} className="uppercase" {...register("gstin")} /></Field>
        <Field label="CIN" htmlFor="cin" error={errors.cin?.message}><Input id="cin" maxLength={21} className="uppercase" {...register("cin")} /></Field>
        <Field label="Incorporation date" htmlFor="incorporation_date"><Input id="incorporation_date" type="date" {...register("incorporation_date")} /></Field>
        <Field label="Headquarters" htmlFor="headquarters"><Input id="headquarters" {...register("headquarters")} /></Field>
        <Field label="Declared annual turnover (₹)" htmlFor="annual_turnover" error={errors.annual_turnover?.message} hint="Declared figure — verified later against documents."><Input id="annual_turnover" inputMode="numeric" {...register("annual_turnover")} /></Field>
        <Field label="Contact person" htmlFor="contact_person"><Input id="contact_person" {...register("contact_person")} /></Field>
        <Field label="Contact e-mail" htmlFor="contact_email" error={errors.contact_email?.message}><Input id="contact_email" type="email" {...register("contact_email")} /></Field>
        <div className="sm:col-span-2"><Field label="Business description" htmlFor="business_description"><Textarea id="business_description" rows={3} {...register("business_description")} /></Field></div>
      </form>
    </Dialog>
  );
}
