"use client";

import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { AlertTriangle, CheckCircle2, Info, Loader2, XCircle } from "lucide-react";
import * as React from "react";

import { cn } from "@/lib/utils";

/* ---------------------------------------------------------------- Button */
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground shadow-sm hover:bg-primary/90",
        secondary: "bg-secondary text-secondary-foreground hover:bg-secondary/80",
        outline: "border border-input bg-card hover:bg-muted",
        ghost: "hover:bg-muted",
        destructive: "bg-destructive text-destructive-foreground hover:bg-destructive/90",
        link: "text-primary underline-offset-4 hover:underline px-0",
      },
      size: { default: "h-9 px-4", sm: "h-8 px-3 text-xs", lg: "h-10 px-6", icon: "h-9 w-9" },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean;
  loading?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild, loading, children, disabled, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp ref={ref} className={cn(buttonVariants({ variant, size }), className)} disabled={disabled || loading} {...props}>
        {asChild ? children : (<>{loading && <Loader2 className="animate-spin" aria-hidden />}{children}</>)}
      </Comp>
    );
  },
);
Button.displayName = "Button";

/* ---------------------------------------------------------------- Inputs */
export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(({ className, ...props }, ref) => (
  <input ref={ref} className={cn("flex h-9 w-full rounded-md border border-input bg-card px-3 py-1 text-sm shadow-sm placeholder:text-muted-foreground disabled:opacity-50 aria-[invalid=true]:border-destructive", className)} {...props} />
));
Input.displayName = "Input";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(({ className, ...props }, ref) => (
  <textarea ref={ref} className={cn("flex min-h-[80px] w-full rounded-md border border-input bg-card px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground", className)} {...props} />
));
Textarea.displayName = "Textarea";

export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(({ className, children, ...props }, ref) => (
  <select ref={ref} className={cn("flex h-9 w-full rounded-md border border-input bg-card px-2 py-1 text-sm shadow-sm", className)} {...props}>{children}</select>
));
Select.displayName = "Select";

export function Field({ label, htmlFor, error, hint, children, required }: { label: string; htmlFor: string; error?: string; hint?: string; children: React.ReactNode; required?: boolean }) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="text-sm font-medium">{label}{required && <span className="text-destructive"> *</span>}</label>
      {children}
      {error ? <p role="alert" className="text-xs text-destructive">{error}</p> : hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

/* ---------------------------------------------------------------- Card */
export function Card({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("min-w-0 rounded-lg border bg-card text-card-foreground shadow-sm", className)} {...p} />;
}
export function CardHeader({ title, description, action, className }: { title: React.ReactNode; description?: React.ReactNode; action?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("flex items-start justify-between gap-4 border-b px-5 py-4", className)}>
      <div><h3 className="text-sm font-semibold">{title}</h3>{description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}</div>
      {action}
    </div>
  );
}
export function CardBody({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-5", className)} {...p} />;
}

/* ---------------------------------------------------------------- Badge */
const TONES = {
  neutral: "bg-muted text-muted-foreground border-transparent",
  info: "bg-sky-50 text-sky-800 border-sky-200",
  success: "bg-emerald-50 text-emerald-800 border-emerald-200",
  warning: "bg-amber-50 text-amber-800 border-amber-200",
  danger: "bg-red-50 text-red-800 border-red-200",
  primary: "bg-blue-50 text-blue-900 border-blue-200",
  violet: "bg-violet-50 text-violet-800 border-violet-200",
} as const;
export type Tone = keyof typeof TONES;
export function Badge({ tone = "neutral", className, children }: { tone?: Tone; className?: string; children: React.ReactNode }) {
  return <span className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium", TONES[tone], className)}>{children}</span>;
}

export const severityTone = (s: string): Tone =>
  ({ critical: "danger", high: "danger", medium: "warning", low: "info", info: "neutral", positive: "success" } as Record<string, Tone>)[s] ?? "neutral";
export const statusTone = (s: string): Tone =>
  ({ completed: "success", extracted: "success", approved: "success", consistent: "success", verified: "success", explained: "success",
     processing: "info", running: "info", queued: "neutral", retrying: "warning", partial: "warning", under_review: "info",
     awaiting_review: "violet", under_analysis: "info", documents_pending: "neutral", potential_discrepancy: "danger",
     unusual_pattern: "warning", requires_verification: "warning", failed: "danger", rejected: "danger", escalated: "danger",
     no_data: "warning", insufficient_data: "neutral", disputed: "danger" } as Record<string, Tone>)[s] ?? "neutral";

/* ---------------------------------------------------------------- Feedback */
export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("size-4 animate-spin text-muted-foreground", className)} aria-label="Loading" />;
}
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-md bg-muted", className)} />;
}
export function LoadingBlock({ label = "Loading…" }: { label?: string }) {
  return <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground" role="status"><Spinner />{label}</div>;
}
export function EmptyState({ icon, title, description, action }: { icon?: React.ReactNode; title: string; description?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed bg-card px-6 py-12 text-center">
      {icon && <div className="text-muted-foreground [&_svg]:size-8">{icon}</div>}
      <p className="text-sm font-semibold">{title}</p>
      {description && <p className="max-w-md text-sm text-muted-foreground">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
const CALLOUT = {
  info: { cls: "border-sky-200 bg-sky-50 text-sky-900", Icon: Info },
  warning: { cls: "border-amber-200 bg-amber-50 text-amber-900", Icon: AlertTriangle },
  danger: { cls: "border-red-200 bg-red-50 text-red-900", Icon: XCircle },
  success: { cls: "border-emerald-200 bg-emerald-50 text-emerald-900", Icon: CheckCircle2 },
};
export function Callout({ tone = "info", title, children, className }: { tone?: keyof typeof CALLOUT; title?: string; children?: React.ReactNode; className?: string }) {
  const { cls, Icon } = CALLOUT[tone];
  return (
    <div role={tone === "danger" ? "alert" : "note"} className={cn("flex gap-3 rounded-md border px-4 py-3 text-sm", cls, className)}>
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div className="space-y-1">{title && <p className="font-medium">{title}</p>}{children && <div className="text-[13px] leading-relaxed opacity-90">{children}</div>}</div>
    </div>
  );
}
export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const msg = error instanceof Error ? error.message : "Something went wrong.";
  return (
    <Callout tone="danger" title="Could not load this section">
      <p>{msg}</p>
      {onRetry && <button onClick={onRetry} className="mt-1 font-medium underline">Try again</button>}
    </Callout>
  );
}

export function Progress({ value, className, tone = "primary" }: { value: number; className?: string; tone?: "primary" | "success" | "warning" | "danger" }) {
  const color = { primary: "bg-primary", success: "bg-emerald-600", warning: "bg-amber-500", danger: "bg-red-600" }[tone];
  return (
    <div className={cn("h-2 w-full overflow-hidden rounded-full bg-muted", className)} role="progressbar" aria-valuenow={Math.round(value)} aria-valuemin={0} aria-valuemax={100}>
      <div className={cn("h-full rounded-full transition-all", color)} style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  );
}

/* ---------------------------------------------------------------- Table */
export function Table({ className, ...p }: React.TableHTMLAttributes<HTMLTableElement>) {
  return <div className="w-full overflow-x-auto"><table className={cn("w-full caption-bottom text-sm", className)} {...p} /></div>;
}
export const Th = ({ className, ...p }: React.ThHTMLAttributes<HTMLTableCellElement>) => (
  <th className={cn("h-9 whitespace-nowrap border-b bg-muted/60 px-3 text-left align-middle text-xs font-medium uppercase tracking-wide text-muted-foreground", className)} {...p} />
);
export const Td = ({ className, ...p }: React.TdHTMLAttributes<HTMLTableCellElement>) => (
  <td className={cn("border-b px-3 py-2.5 align-middle", className)} {...p} />
);

export function PageHeader({ title, description, actions, eyebrow }: { title: React.ReactNode; description?: React.ReactNode; actions?: React.ReactNode; eyebrow?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        {eyebrow && <div className="mb-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">{eyebrow}</div>}
        <h1 className="text-2xl font-semibold">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Stat({ label, value, hint, icon, tone }: { label: string; value: React.ReactNode; hint?: React.ReactNode; icon?: React.ReactNode; tone?: "danger" | "warning" | "default" }) {
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between text-xs font-medium text-muted-foreground"><span>{label}</span>{icon && <span className="[&_svg]:size-4">{icon}</span>}</div>
      <div className={cn("num mt-2 text-2xl font-semibold", tone === "danger" && "text-red-700", tone === "warning" && "text-amber-700")}>{value}</div>
      {hint && <div className="mt-1 text-xs text-muted-foreground">{hint}</div>}
    </Card>
  );
}

export function KV({ items, className }: { items: [React.ReactNode, React.ReactNode][]; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2", className)}>
      {items.map(([k, v], i) => (
        <div key={i}><dt className="text-xs text-muted-foreground">{k}</dt><dd className="mt-0.5 text-sm font-medium">{v ?? "—"}</dd></div>
      ))}
    </dl>
  );
}
