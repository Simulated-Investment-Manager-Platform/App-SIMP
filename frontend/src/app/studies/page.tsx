"use client";

import { useQueryClient } from "@tanstack/react-query";
import { FlaskConical } from "lucide-react";
import Link from "next/link";
import { parseAsString, useQueryState } from "nuqs";
import { toast } from "sonner";

import { Waterfall } from "@/components/charts";
import { type Col, DataTable } from "@/components/DataTable";
import { Button, Card, CardHeader, Empty, Skeleton, Status } from "@/components/ui";
import { sendJson } from "@/lib/api/client";
import { useJson } from "@/lib/hooks";
import { ago, bps, usd } from "@/lib/format";

const KINDS = [
  ["counterfactual_chain", "TD decomposition chain", "Handoff baseline → effective-date timing → flows → T1 costs → fee → keep securities; each step's TD change is attributed to it."],
  ["dividend_rule", "Provisional dividend rule", "Run the window twice, provisional rule on and off: Δ NAV, Δ TD, Δ TE and the affected dividend events."],
  ["exclusions", "Exclusion policy", "exclude_securities vs keep_securities where both are valid: the TD effect of the excluded weight."],
  ["cost_params", "Cost-model parameters", "Y, κ and β at ±50% and the MOC auction share at 2% and 10%: how total cost and TD move."],
  ["flow_regime", "Flow regime", "σ at 5, 10 and 25 bps a day × cash buffer at 10 and 50 bps."],
] as const;

export default function Studies() {
  const qc = useQueryClient();
  const [sel, setSel] = useQueryState("study", parseAsString);
  const list = useJson(["studies"], "/studies", undefined, { refetch: 4000 });
  const one = useJson(["study", sel], sel ? `/studies/${sel}` : null, undefined, { refetch: 4000 });
  const start = async (kind: string, name: string) => {
    try {
      const r = await sendJson("POST", "/studies", { kind, name });
      toast.success(`${name}: ${r.runs.length} runs queued`);
      qc.invalidateQueries({ queryKey: ["studies"] });
      setSel(r.study_id);
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Studies</h1>
        <p className="text-sm text-ink-2">Counterfactual chains switch one effect on at a time; sensitivity studies sweep one input. Each is a set of ordinary, reproducible runs.</p>
      </div>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        {KINDS.map(([k, name, d]) => (
          <Card key={k} className="flex flex-col justify-between p-4">
            <div>
              <FlaskConical className="mb-2 h-4 w-4 text-accent" />
              <div className="text-sm font-semibold">{name}</div>
              <p className="mt-1 text-xs text-ink-2">{d}</p>
            </div>
            <Button size="sm" className="mt-3" onClick={() => start(k, name)}>Start study</Button>
          </Card>
        ))}
      </div>
      <div className="grid gap-4 xl:grid-cols-[360px_1fr]">
        <Card>
          <CardHeader title="Studies" />
          {list.isLoading ? <Skeleton className="m-4 h-40" /> : (list.data ?? []).length === 0 ? <div className="p-4"><Empty title="No studies yet" detail="Start one above; runs go to the worker queue." /></div> : (
            <ul className="divide-y divide-line">
              {(list.data ?? []).map((s: any) => (
                <li key={s.study_id}>
                  <button onClick={() => setSel(s.study_id)} className={`w-full px-4 py-2.5 text-left hover:bg-line/30 ${sel === s.study_id ? "bg-line/40" : ""}`}>
                    <div className="text-sm font-medium">{s.name}</div>
                    <div className="text-xs text-ink-3">{s.kind} · {s.done}/{s.runs} runs done · {ago(s.created_at)}</div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <div className="space-y-4">
          {!sel && <Empty title="Select a study" />}
          {sel && one.isLoading && <Skeleton className="h-96" />}
          {one.data && <StudyView d={one.data} />}
        </div>
      </div>
    </div>
  );
}

function StudyView({ d }: { d: any }) {
  const c = d.compare;
  const chain = d.study.kind === "counterfactual_chain";
  const runCols: Col<any>[] = [
    { header: "#", accessorKey: "study_step" },
    { header: "Step", accessorFn: (r) => String(r.name).split(": ").pop() },
    { header: "Status", accessorKey: "status", cell: (x) => <Status value={x.getValue()} /> },
    { header: "Progress", accessorFn: (r) => (r.status === "DONE" ? "" : `${Math.round((r.progress ?? 0) * 100)}%`) },
    { header: "Run", accessorKey: "run_id", cell: (x) => <Link className="font-mono text-accent hover:underline" href={`/runs/${x.getValue()}`}>{String(x.getValue()).slice(0, 12)}</Link> },
  ];
  const resCols: Col<any>[] = [
    { header: "Step", accessorFn: (r) => String(r.name).split(": ").pop() },
    { header: "TD net (bps/yr)", accessorKey: "td_net_bps", cell: (x) => bps(x.getValue(), 2), meta: { num: true } },
    { header: "TE (bps)", accessorKey: "te_bps", cell: (x) => bps(x.getValue(), 2), meta: { num: true } },
    { header: "Ending NAV", accessorKey: "ending_nav", cell: (x) => usd(x.getValue()), meta: { num: true } },
    { header: "Exec cost (bps)", accessorKey: "exec_cost_bps", cell: (x) => bps(x.getValue()), meta: { num: true } },
    { header: "Exec cost ($)", accessorKey: "exec_cost_usd", cell: (x) => usd(x.getValue()), meta: { num: true } },
    { header: "Prov. dividends", accessorFn: (r) => (r.provisional_dividends ? `${r.provisional_dividends.booked}/${r.provisional_dividends.events} booked · ${usd(r.provisional_dividends.usd)}` : "—") },
  ];
  const base = c?.runs?.[0];
  return (
    <>
      <Card>
        <CardHeader title={d.study.name} subtitle={`${d.study.kind} · steps run in this fixed order`} />
        <DataTable data={d.runs} height="auto" className="rounded-none border-x-0 border-b-0" columns={runCols} rowKey={(r: any) => r.run_id} />
      </Card>
      {c && (
        <Card>
          <CardHeader title="Results" subtitle={chain ? "Each step's change in TD is attributed to that effect; effects interact, so the order is printed" : `Deltas vs the first step (${base?.name?.split(": ").pop()})`} />
          {chain && c.steps.length > 0 && (
            <div className="p-2">
              <Waterfall steps={[...c.steps.map((s: any) => ({ label: String(s.label).split(": ").pop(), bps: s.delta_td_bps })), { label: "TD net", bps: c.runs[c.runs.length - 1].td_net_bps - c.runs[0].td_net_bps, total: true }]} />
            </div>
          )}
          <DataTable data={c.runs.map((r: any) => ({ ...r, d_td: base ? r.td_net_bps - base.td_net_bps : null }))} height="auto" className="rounded-none border-x-0 border-b-0" columns={[...resCols, { header: "Δ TD vs first", accessorKey: "d_td", cell: (x: any) => bps(x.getValue(), 2), meta: { num: true } }]} rowKey={(r: any) => r.run_id} />
        </Card>
      )}
    </>
  );
}
