"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { Eye, EyeOff } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, Callout, Field, Input } from "@/components/ui/primitives";
import { api, errorMessage } from "@/lib/api";

const schema = z.object({
  full_name: z.string().trim().min(2, "Enter your full name"),
  email: z.string().trim().email("Enter a valid e-mail address"),
  organization_name: z.string().trim().min(2, "Enter your organisation's name"),
  password: z.string().min(10, "At least 10 characters").regex(/[A-Za-z]/, "Include a letter").regex(/\d/, "Include a number"),
  confirm: z.string(),
}).refine((v) => v.password === v.confirm, { path: ["confirm"], message: "Passwords do not match" });
type Values = z.infer<typeof schema>;

export default function RegisterPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const [show, setShow] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm<Values>({ resolver: zodResolver(schema) });

  const onSubmit = async ({ confirm: _c, ...v }: Values) => {
    setError(null);
    try {
      await api.post("/auth/register", v);
      qc.clear();
      router.replace("/");
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  return (
    <div className="w-full max-w-sm space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Create your organisation</h1>
        <p className="mt-1 text-sm text-muted-foreground">You will be the administrator and can invite analysts and reviewers.</p>
      </div>
      {error && <Callout tone="danger">{error}</Callout>}
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
        <Field label="Full name" htmlFor="full_name" error={errors.full_name?.message}><Input id="full_name" autoComplete="name" {...register("full_name")} /></Field>
        <Field label="Work e-mail" htmlFor="email" error={errors.email?.message}><Input id="email" type="email" autoComplete="email" {...register("email")} /></Field>
        <Field label="Organisation" htmlFor="organization_name" error={errors.organization_name?.message}><Input id="organization_name" autoComplete="organization" {...register("organization_name")} /></Field>
        <Field label="Password" htmlFor="password" error={errors.password?.message} hint="At least 10 characters with letters and numbers.">
          <div className="relative">
            <Input id="password" type={show ? "text" : "password"} autoComplete="new-password" className="pr-10" {...register("password")} />
            <button type="button" onClick={() => setShow((s) => !s)} className="absolute inset-y-0 right-0 grid w-10 place-items-center text-muted-foreground" aria-label={show ? "Hide password" : "Show password"}>
              {show ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
            </button>
          </div>
        </Field>
        <Field label="Confirm password" htmlFor="confirm" error={errors.confirm?.message}><Input id="confirm" type={show ? "text" : "password"} autoComplete="new-password" {...register("confirm")} /></Field>
        <Button type="submit" className="w-full" loading={isSubmitting}>Create account</Button>
      </form>
      <p className="text-center text-sm text-muted-foreground">Already have an account? <Link href="/login" className="font-medium text-primary hover:underline">Sign in</Link></p>
    </div>
  );
}
