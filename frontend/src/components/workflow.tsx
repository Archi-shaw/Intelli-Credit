import { Check } from "lucide-react";

import { cn } from "@/lib/utils";
import type { WorkflowStep } from "@/lib/types";

export function WorkflowStepper({ steps, current, onSelect }: { steps: WorkflowStep[]; current?: number; onSelect?: (index: number) => void }) {
  return (
    <ol className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-5 2xl:grid-cols-9" aria-label="Assessment workflow">
      {steps.map((s) => {
        const isCurrent = current === s.index;
        const Comp = onSelect ? "button" : "div";
        return (
          <li key={s.index}>
            <Comp {...(onSelect ? { onClick: () => onSelect(s.index), type: "button" as const, "aria-label": `Step ${s.index}: ${s.name}${s.done ? " (done)" : ""}` } : {})}
              aria-current={isCurrent ? "step" : undefined}
              className={cn("flex h-full w-full items-center gap-2 rounded-md border bg-card px-2.5 py-2 text-left text-xs",
                s.done && "border-emerald-200 bg-emerald-50/60", isCurrent && "border-primary ring-1 ring-primary", onSelect && "hover:border-primary")}>
              <span className={cn("grid size-5 shrink-0 place-items-center rounded-full border text-[10px] font-semibold",
                s.done ? "border-emerald-600 bg-emerald-600 text-white" : isCurrent ? "border-primary text-primary" : "text-muted-foreground")}>
                {s.done ? <Check className="size-3" /> : s.index}
              </span>
              <span className={cn("leading-tight", s.done || isCurrent ? "font-medium" : "text-muted-foreground")}>{s.name}</span>
            </Comp>
          </li>
        );
      })}
    </ol>
  );
}
