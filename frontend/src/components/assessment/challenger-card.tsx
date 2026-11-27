import { FlaskConical } from "lucide-react";

import { Badge, Callout, Card, CardBody, CardHeader, Table, Td, Th } from "@/components/ui/primitives";
import { label } from "@/lib/format";
import type { Challenger } from "@/lib/types";

/** Experimental ML challenger: shown for comparison only, visually separate from the policy scorecard. */
export function ChallengerCard({ c }: { c: Challenger }) {
  if (c.status !== "ok") {
    return <Callout title="Challenger model unavailable">{c.detail} The policy scorecard above is unaffected.</Callout>;
  }
  const lg = c.test_metrics?.lightgbm_calibrated;
  const sc = c.test_metrics?.policy_scorecard;
  return (
    <Card className="border-dashed border-violet-300">
      <CardHeader title={<span className="flex items-center gap-2"><FlaskConical className="size-4 text-violet-700" />Challenger model (experimental)</span>}
        description={c.note}
        action={<div className="flex flex-wrap gap-1"><Badge tone="violet">{c.model_version}</Badge>
          <Badge tone={c.dataset_type === "synthetic" ? "warning" : "info"}>Training data: {label(c.dataset_type)}</Badge>
          <Badge tone="danger">Not used for decisions</Badge></div>} />
      <CardBody className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="rounded-md border p-3">
            <div className="text-xs text-muted-foreground">{c.probability_label}</div>
            <div className="num mt-1 text-2xl font-semibold" data-testid="challenger-probability">{((c.probability ?? 0) * 100).toFixed(1)}%</div>
            <div className="text-xs text-muted-foreground">A probability estimate, not a confidence score; threshold {((c.decision_threshold ?? 0) * 100).toFixed(1)}%.</div>
          </div>
          <div className="rounded-md border p-3 text-xs sm:col-span-2">
            <div className="font-medium">Evaluation status</div>
            <p className="mt-1 text-muted-foreground">{c.evaluation_status}</p>
            {lg && sc && <p className="mt-2">Held-out synthetic test: challenger ROC-AUC {lg.roc_auc?.toFixed(3)} · PR-AUC {lg.pr_auc?.toFixed(3)} · Brier {lg.brier?.toFixed(4)}; policy scorecard ROC-AUC {sc.roc_auc?.toFixed(3)}. Synthetic results are not evidence of real-world performance.</p>}
          </div>
        </div>
        <div>
          <h4 className="mb-2 text-sm font-semibold">Main contributing features (TreeSHAP, log-odds)</h4>
          <Table>
            <thead><tr><Th>Feature</Th><Th className="text-right">Value</Th><Th className="text-right">Contribution</Th><Th>Effect</Th></tr></thead>
            <tbody>{c.top_contributions?.map((f) => (
              <tr key={f.feature}><Td><div className="font-medium">{f.feature}</div><div className="text-xs text-muted-foreground">{f.description}</div></Td>
                <Td className="num text-right">{f.value ?? <span className="text-muted-foreground">missing</span>}</Td>
                <Td className="num text-right">{f.shap_log_odds > 0 ? "+" : ""}{f.shap_log_odds.toFixed(3)}</Td>
                <Td><Badge tone={f.direction === "raises risk" ? "danger" : "success"}>{f.direction}</Badge></Td></tr>
            ))}</tbody>
          </Table>
          {!!c.missing_features?.length && <p className="mt-2 text-xs text-muted-foreground">Missing inputs (left missing, never zero-filled): {c.missing_features.join(", ")}.</p>}
        </div>
      </CardBody>
    </Card>
  );
}
