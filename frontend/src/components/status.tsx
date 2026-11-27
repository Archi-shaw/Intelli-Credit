import { Badge, statusTone, severityTone } from "@/components/ui/primitives";
import { label, OUTCOME_LABELS, STATUS_LABELS } from "@/lib/format";

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={statusTone(status)}>{STATUS_LABELS[status] ?? label(status)}</Badge>;
}

export function SeverityBadge({ severity }: { severity: string }) {
  return <Badge tone={severityTone(severity)}>{label(severity)}</Badge>;
}

export function OutcomeBadge({ outcome }: { outcome: string }) {
  const tone = outcome === "approve" ? "success" : outcome === "conditional_approval" ? "warning" : outcome === "reject" ? "danger" : "neutral";
  return <Badge tone={tone}>{OUTCOME_LABELS[outcome] ?? label(outcome)}</Badge>;
}

export function SyntheticBadge() {
  return <Badge tone="warning" className="uppercase tracking-wide">Synthetic demo</Badge>;
}

/** Confidence is about data coverage/quality — deliberately styled differently from the risk score. */
export function ConfidenceBadge({ level, score }: { level: string; score?: number }) {
  const tone = level === "High" ? "primary" : level === "Medium" ? "info" : "warning";
  return <Badge tone={tone}>Confidence: {level}{score != null ? ` (${score.toFixed(0)}/100)` : ""}</Badge>;
}
