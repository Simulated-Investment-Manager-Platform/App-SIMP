"use client";

import { parseAsString, useQueryState } from "nuqs";

import { Waterfall } from "@/components/charts";
import { type Col, DataTable } from "@/components/DataTable";
import { Card, CardHeader, Empty, ErrorBox, Skeleton } from "@/components/ui";
import { useJson } from "@/lib/hooks";
import { bps, pct, usd } from "@/lib/format";

export default function Compare() {
  const [ids] = useQueryState("ids", parseAsString.withDefault(""));
  const q = useJson(["compare", ids], ids ? "/runs/compare" : null, { ids });
  if (!ids) return <Empty title="Pick two or more runs to compare" action="Runs" href="/runs" />;
  if (q.isLoading) return <Skeleton className="h-96" />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data;
  const steps = [{ label: "first run", bps: 0 }, ...d.steps.map((s: any) => ({ label: String(s.label).split(": ").pop(), bps: s.delta_td_bps }))];
  const final = d.runs[d.runs.length - 1]?.td_net_bps ?? 0;
  const first = d.runs[0]?.td_net_bps ?? 0;
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Compare runs</h1>
        <p className="text-sm text-ink-2">{d.note}</p>
      </div>
      <Card>
        <CardHeader title="Side by side" />
        <DataTable data={d.runs} height="auto" className="rounded-none border-x-0 border-b-0" columns={cols} rowKey={(r: any) => r.run_id} />
      </Card>
      {d.steps.length > 0 && (
        <Card>
          <CardHeader title="Change in TD attributed to each step" subtitle={`From ${bps(first)} to ${bps(final)} bps/yr, steps in the printed order`} />
          <div className="p-2">
            <Waterfall steps={[...steps.slice(1).map((s) => s), { label: "Δ total", bps: final - first, total: true }]} />
          </div>
          <DataTable data={d.steps} height="auto" className="rounded-none border-x-0 border-b-0" columns={stepCols} rowKey={(r: any) => r.to} />
        </Card>
      )}
    </div>
  );
}

const cols: Col<any>[] = [
  { header: "Step", accessorKey: "study_step" },
  { header: "Run", accessorKey: "run_id", cell: (c) => <span className="font-mono">{String(c.getValue()).slice(0, 12)}</span> },
  { header: "Name", accessorKey: "name" },
  { header: "Model", accessorKey: "execution_model" },
  { header: "Timing", accessorKey: "timing" },
  { header: "TD net (bps)", accessorKey: "td_net_bps", cell: (c) => bps(c.getValue()), meta: { num: true } },
  { header: "TD gross (bps)", accessorKey: "td_gross_bps", cell: (c) => bps(c.getValue()), meta: { num: true } },
  { header: "TE (bps)", accessorKey: "te_bps", cell: (c) => bps(c.getValue()), meta: { num: true } },
  { header: "Ann. return", accessorKey: "annual_return", cell: (c) => pct(c.getValue()), meta: { num: true } },
  { header: "Ending NAV", accessorKey: "ending_nav", cell: (c) => usd(c.getValue()), meta: { num: true } },
  { header: "Exec cost (bps)", accessorKey: "exec_cost_bps", cell: (c) => bps(c.getValue()), meta: { num: true } },
  { header: "Exec cost ($)", accessorKey: "exec_cost_usd", cell: (c) => usd(c.getValue()), meta: { num: true } },
  { header: "Prov. dividends", accessorFn: (r) => (r.provisional_dividends ? `${r.provisional_dividends.events} · ${usd(r.provisional_dividends.usd)}` : "—") },
  { header: "Output hash", accessorKey: "output_hash", cell: (c) => <span className="font-mono">{String(c.getValue() ?? "").slice(0, 10)}</span> },
];

const stepCols: Col<any>[] = [
  { header: "Step", accessorKey: "label" },
  { header: "Δ TD (bps/yr)", accessorKey: "delta_td_bps", cell: (c) => bps(c.getValue(), 2), meta: { num: true } },
  { header: "Δ TE (bps)", accessorKey: "delta_te_bps", cell: (c) => bps(c.getValue(), 2), meta: { num: true } },
  { header: "Δ ending NAV", accessorKey: "delta_nav", cell: (c) => usd(c.getValue()), meta: { num: true } },
];
