"use client";

import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Badge, Table, Td, Th } from "@/components/ui/primitives";
import { inr, lakh, MISSING, ratio as fmtRatio } from "@/lib/format";
import type { Ratio, Statement } from "@/lib/types";

/* Validated categorical slots (light surface): blue, orange, aqua — see the data-viz palette check.
   Aqua is below 3:1 contrast on white, so every chart is paired with a table of the same values. */
const SERIES = ["#2a78d6", "#eb6834", "#1baf7a"];
const GRID = "hsl(216 22% 90%)";
const AXIS = { fontSize: 11, fill: "hsl(218 13% 42%)" };

export const PL_ROWS: [string, string][] = [
  ["revenue_from_operations", "Revenue from operations"], ["other_income", "Other income"], ["total_income", "Total income"],
  ["cost_of_goods_sold", "Cost of materials / goods"], ["employee_expense", "Employee benefits"], ["ebitda", "EBITDA"],
  ["depreciation", "Depreciation & amortisation"], ["finance_costs", "Finance costs"], ["pbt", "Profit before tax"],
  ["tax_expense", "Tax expense"], ["pat", "Profit after tax"],
];
export const BS_ROWS: [string, string][] = [
  ["share_capital", "Share capital"], ["reserves_and_surplus", "Reserves & surplus"], ["shareholders_equity", "Net worth"],
  ["long_term_borrowings", "Long-term borrowings"], ["short_term_borrowings", "Short-term borrowings"], ["trade_payables", "Trade payables"],
  ["current_liabilities", "Current liabilities"], ["inventory", "Inventories"], ["trade_receivables", "Trade receivables"],
  ["cash_and_equivalents", "Cash & equivalents"], ["current_assets", "Current assets"], ["total_assets", "Total assets"],
];
export const CF_ROWS: [string, string][] = [
  ["operating_cash_flow", "Operating cash flow"], ["investing_cash_flow", "Investing cash flow"], ["financing_cash_flow", "Financing cash flow"],
];

/** Period-by-period statement table in ₹ lakh with page references. Unextracted rows are listed as missing. */
export function StatementTable({ periods, rows, title, docNames }: { periods: Statement[]; rows?: [string, string][]; title?: string; docNames?: Record<number, string> }) {
  const allRows = rows ?? [...PL_ROWS, ...BS_ROWS, ...CF_ROWS];
  const present = allRows.filter(([k]) => periods.some((p) => p.line_items[k]?.value != null));
  const missing = allRows.filter(([k]) => !periods.some((p) => p.line_items[k]?.value != null));
  return (
    <div>
      {title && <h4 className="mb-2 text-sm font-semibold">{title}</h4>}
      {present.length === 0 ? <p className="text-sm text-muted-foreground">No figures extracted for this statement.</p> : (
        <Table>
          <thead><tr><Th>₹ lakh</Th>{periods.map((p) => <Th key={p.period_label} className="text-right">{p.period_label}</Th>)}<Th>Source (latest)</Th></tr></thead>
          <tbody>
            {present.map(([k, name]) => {
              const latest = periods[periods.length - 1]?.line_items[k];
              return (
                <tr key={k}>
                  <Td className="font-medium">{name}</Td>
                  {periods.map((p) => {
                    const it = p.line_items[k];
                    return <Td key={p.period_label} className={`num text-right ${it?.value != null && it.value < 0 ? "text-red-700" : ""}`}>{it?.value != null ? lakh(it.value) : <span className="text-muted-foreground">{MISSING}</span>}</Td>;
                  })}
                  <Td className="text-xs text-muted-foreground">
                    {latest ? <>{docNames?.[latest.document_id] ?? `Doc ${latest.document_id}`}{latest.page_number ? `, p.${latest.page_number}` : ""} · {(latest.confidence * 100).toFixed(0)}%{latest.verified && <Badge tone="success" className="ml-1">verified</Badge>}</> : "—"}
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      )}
      {missing.length > 0 && present.length > 0 && <p className="mt-2 text-xs text-muted-foreground">Not found in the documents (shown as missing, not zero): {missing.map(([, n]) => n).join(", ")}.</p>}
    </div>
  );
}

function LakhTooltip({ active, payload, label }: { active?: boolean; payload?: { name: string; value: number | null; color: string }[]; label?: string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border bg-card px-3 py-2 text-xs shadow-md">
      <div className="mb-1 font-medium">{label}</div>
      {payload.map((p) => (
        <div key={p.name} className="flex items-center gap-2"><span className="inline-block size-2 rounded-sm" style={{ background: p.color }} />
          <span className="text-muted-foreground">{p.name}</span><span className="num ml-auto pl-3 font-medium">{p.value == null ? "not available" : inr(p.value)}</span></div>
      ))}
    </div>
  );
}

const lakhTick = (v: number) => `${(v / 1e5).toLocaleString("en-IN", { maximumFractionDigits: 0 })}L`;

/** Revenue / EBITDA / PAT across periods — only rendered when at least two periods were extracted. */
export function TrendChart({ trend }: { trend: Record<string, number | string | null>[] }) {
  const usable = trend.filter((t) => t.revenue_from_operations != null || t.pat != null);
  if (usable.length < 2) return <p className="text-sm text-muted-foreground">A trend needs at least two extracted periods; only {usable.length} available.</p>;
  return (
    <div className="h-64" role="img" aria-label="Revenue, EBITDA and profit after tax by period; values are listed in the statement table">
      <ResponsiveContainer>
        <LineChart data={usable} margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
          <CartesianGrid vertical={false} stroke={GRID} />
          <XAxis dataKey="period" tick={AXIS} tickLine={false} axisLine={{ stroke: GRID }} />
          <YAxis tick={AXIS} tickLine={false} axisLine={false} tickFormatter={lakhTick} width={56} />
          <Tooltip content={<LakhTooltip />} cursor={{ stroke: GRID, strokeWidth: 1 }} />
          <Legend iconType="plainline" wrapperStyle={{ fontSize: 12 }} />
          {([["revenue_from_operations", "Revenue"], ["ebitda", "EBITDA"], ["pat", "PAT"]] as const).map(([k, n], i) => (
            <Line key={k} dataKey={k} name={n} stroke={SERIES[i]} strokeWidth={2} dot={{ r: 4, strokeWidth: 2, stroke: "#fff", fill: SERIES[i] }} activeDot={{ r: 5 }} connectNulls={false} isAnimationActive={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Monthly GST taxable value vs bank business credits (two series, grouped bars, one axis in ₹). */
export function GstBankChart({ gst, bank }: { gst: { month: string; GSTR3B?: number | null; GSTR1?: number | null }[]; bank: { month: string; business_credits: number }[] }) {
  const months = Array.from(new Set([...gst.map((g) => g.month), ...bank.map((b) => b.month)])).sort();
  const data = months.map((m) => ({
    month: m,
    gst: gst.find((g) => g.month === m)?.GSTR3B ?? gst.find((g) => g.month === m)?.GSTR1 ?? null,
    bank: bank.find((b) => b.month === m)?.business_credits ?? null,
  }));
  if (!data.some((d) => d.gst != null) || !data.some((d) => d.bank != null)) {
    return <p className="text-sm text-muted-foreground">Needs both GST returns and bank statements to compare monthly figures.</p>;
  }
  return (
    <div className="h-72" role="img" aria-label="Monthly GST taxable value compared with bank business credits">
      <ResponsiveContainer>
        <BarChart data={data} barGap={2} barCategoryGap="22%" margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
          <CartesianGrid vertical={false} stroke={GRID} />
          <XAxis dataKey="month" tick={AXIS} tickLine={false} axisLine={{ stroke: GRID }} />
          <YAxis tick={AXIS} tickLine={false} axisLine={false} tickFormatter={lakhTick} width={56} />
          <Tooltip content={<LakhTooltip />} cursor={{ fill: "hsl(216 25% 95%)" }} />
          <Legend iconType="square" wrapperStyle={{ fontSize: 12 }} />
          <Bar dataKey="gst" name="GST taxable value (GSTR-3B)" fill={SERIES[0]} radius={[4, 4, 0, 0]} isAnimationActive={false} />
          <Bar dataKey="bank" name="Bank business credits" fill={SERIES[1]} radius={[4, 4, 0, 0]} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

const RATIO_GROUPS: Record<string, string> = {
  liquidity: "Liquidity", leverage: "Leverage", coverage: "Debt servicing", profitability: "Profitability",
  efficiency: "Working capital", growth: "Growth", cash_flow: "Cash flow", governance: "Governance",
};

export function RatioTable({ ratios }: { ratios: Ratio[] }) {
  if (!ratios.length) return <p className="text-sm text-muted-foreground">Ratios are calculated when the credit assessment is generated.</p>;
  const groups = Array.from(new Set(ratios.map((r) => r.category)));
  return (
    <Table>
      <thead><tr><Th>Ratio</Th><Th className="text-right">Value</Th><Th>Formula</Th><Th>Interpretation</Th></tr></thead>
      <tbody>
        {groups.flatMap((g) => [
          <tr key={`g-${g}`}><Td colSpan={4} className="bg-muted/40 py-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{RATIO_GROUPS[g] ?? g}</Td></tr>,
          ...ratios.filter((r) => r.category === g).map((r) => (
            <tr key={r.key}>
              <Td className="font-medium">{r.name}</Td>
              <Td className="num text-right">{r.value != null ? fmtRatio(r.value, r.unit) : <Badge tone={r.status === "negative_denominator" ? "danger" : "neutral"}>{r.status === "missing_inputs" ? "missing inputs" : r.status.replace(/_/g, " ")}</Badge>}</Td>
              <Td className="text-xs text-muted-foreground">{r.formula}</Td>
              <Td className="text-xs">{r.interpretation}</Td>
            </tr>
          )),
        ])}
      </tbody>
    </Table>
  );
}
