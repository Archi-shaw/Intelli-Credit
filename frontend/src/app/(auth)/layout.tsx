import { ShieldCheck } from "lucide-react";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid min-h-screen lg:grid-cols-[1fr_480px]">
      <div className="relative hidden flex-col justify-between bg-sidebar p-10 text-sidebar-foreground lg:flex">
        <div className="flex items-center gap-2 text-white">
          <div className="grid size-9 place-items-center rounded-md bg-sky-500/20 text-sky-300"><ShieldCheck className="size-5" /></div>
          <span className="text-lg font-semibold">Intelli-Credit</span>
        </div>
        <div className="max-w-lg space-y-4">
          <h2 className="text-3xl font-semibold leading-tight text-white">Evidence-backed credit appraisal for Indian MSMEs.</h2>
          <ul className="space-y-2 text-sm text-sidebar-muted">
            <li>• Extracts figures from financial statements, GST returns and bank statements — with page references.</li>
            <li>• Reconciles sources and surfaces potential discrepancies for verification.</li>
            <li>• Explainable risk scoring, Five Cs and policy-driven recommendations.</li>
            <li>• Generates Credit Appraisal Memos for authorised human review.</li>
          </ul>
        </div>
        <p className="text-xs text-sidebar-muted">Decision-support tool. It does not approve or reject loans.</p>
      </div>
      <div className="flex items-center justify-center p-6 sm:p-10">{children}</div>
    </div>
  );
}
