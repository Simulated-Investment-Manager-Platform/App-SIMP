"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { parseAsString, useQueryState } from "nuqs";

import { AnimatedTabs } from "@/components/aceternity";
import { Blotter } from "@/components/Blotter";
import { ActiveChart, CashChart, FlowsChart, LineChart, NavChart, Waterfall } from "@/components/charts";
import { type Col, DataTable } from "@/components/DataTable";
import { RunScope } from "@/components/RunPicker";
import { TcaPanel } from "@/components/TcaPanel";
import { Card, CardHeader, CostCell, Empty, ErrorBox, Input, KV, Skeleton, Stat } from "@/components/ui";
import { getJson } from "@/lib/api/client";
import { useJson } from "@/lib/hooks";
import { bps, num, pct, px, usd } from "@/lib/format";

type Tab = "performance" | "holdings" | "flows" | "orders" | "tca";

export default function FundPage() {
  const { fundId } = useParams<{ fundId: string }>();
  const fund = useQuery({ queryKey: ["funds"], queryFn: () => getJson("/funds") });
  const f = (fund.data ?? []).find((x: any) => x.fund_id === fundId);
  const [tab, setTab] = useQueryState("tab", parseAsString.withDefault("performance"));
  return (
    <RunScope fundId={fundId} title={f?.name ?? fundId} subtitle={f ? `${f.strategy_type} · benchmark ${f.benchmark_id} · inception ${f.inception_date} · fee ${f.fee_bps} bps · cash buffer ${f.cash_buffer_bps} bps` : undefined}>
      {(runId) => (
        <div className="space-y-4">
          <AnimatedTabs<Tab>
            value={tab as Tab}
            onChange={(v) => setTab(v)}
            tabs={[
              { value: "performance", label: "Performance" },
              { value: "holdings", label: "Holdings" },
              { value: "flows", label: "Flows" },
              { value: "orders", label: "Orders" },
              { value: "tca", label: "TCA" },
            ]}
          />
          {tab === "performance" && <Performance runId={runId} />}
          {tab === "holdings" && <Holdings runId={runId} />}
          {tab === "flows" && <Flows runId={runId} />}
          {tab === "orders" && <Blotter runId={runId} />}
          {tab === "tca" && <TcaPanel runId={runId} />}
        </div>
      )}
    </RunScope>
  );
}

function Performance({ runId }: { runId: string }) {
  const nav = useJson(["nav", runId], `/runs/${runId}/nav`);
  const b = useJson(["bench", runId], `/runs/${runId}/benchmark`);
  if (nav.isLoading || b.isLoading) return <Skeleton className="h-96" />;
  if (nav.error || b.error) return <ErrorBox error={nav.error ?? b.error} />;
  const r = b.data?.report;
  if (!r?.available) return <Empty title="No fund report for this run" detail={r?.reason ?? "The Phase 1 generator fund has no benchmark; see TCA."} />;
  const steps = b.data.decomposition_chain
    ? [{ label: "Baseline", bps: 0 }, ...b.data.decomposition_chain.map((s: any) => ({ label: s.label.split(": ").pop(), bps: s.delta_td_bps })), { label: "TD net", bps: r.td_net_bps, total: true }]
    : b.data.decomposition_single;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
        <Stat label="TD net" value={<span className="num">{bps(r.td_net_bps)}</span>} sub="bps / yr" />
        <Stat label="TD gross" value={<span className="num">{bps(r.td_gross_bps)}</span>} sub="bps / yr, before fee" />
        <Stat label="Tracking error" value={<span className="num">{bps(r.te_bps)}</span>} sub="bps, annualised daily" />
        <Stat label="Annual return" value={<span className="num">{pct(r.annual_return)}</span>} sub={`benchmark ${pct(r.benchmark_annual_return)}`} />
        <Stat label="Volatility" value={<span className="num">{pct(r.volatility, 1)}</span>} />
        <Stat label="Max drawdown" value={<span className="num">{pct(r.max_drawdown, 1)}</span>} />
        <Stat label="Sharpe (rf = 0)" value={<span className="num">{num(r.sharpe_rf0, 2)}</span>} sub={`beta ${num(r.beta, 3)}`} />
        <Stat label="Ending NAV" value={<span className="num">{usd(r.ending_nav)}</span>} sub={`unit NAV ${num(r.ending_unit_nav, 2)}`} />
      </div>
      <Card>
        <CardHeader title="Fund vs benchmark" subtitle={`Indexed to ${r.window.start} · ${r.benchmark_label} · ${r.window.sessions} sessions`} />
        <div className="p-2"><NavChart data={(nav.data ?? []).filter((x: any) => x.date >= r.window.start)} /></div>
      </Card>
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="Daily active return" subtitle="Fund minus benchmark, bps" />
          <div className="p-2"><ActiveChart data={b.data.active} /></div>
        </Card>
        <Card>
          <CardHeader title="Rolling tracking error" subtitle="63-session window, annualised, bps" />
          <div className="p-2"><LineChart data={b.data.rolling_te.map((x: any) => [x.date, x.te_63d_bps])} name="Rolling TE (63d)" unit="bps" /></div>
        </Card>
      </div>
      <div className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
        <Card>
          <CardHeader title="Tracking-difference decomposition" subtitle={b.data.decomposition_chain ? "Counterfactual chain: one effect switched on per step (order fixed and printed)" : "Single-run attribution; run a counterfactual chain in Studies for the exact split"} />
          <div className="p-2">{steps ? <Waterfall steps={steps} /> : <Empty title="No decomposition" />}</div>
        </Card>
        <Card className="p-4">
          <h4 className="mb-3 text-xs font-semibold uppercase tracking-wide text-ink-2">Drags and proxy effects</h4>
          <KV
            items={[
              ["Cash drag", `${bps(r.cash_drag_bps)} bps/yr (avg cash ${pct(r.avg_cash_weight, 3)})`],
              ["Explicit trading costs", `${bps(-r.explicit_costs_bps_pa)} bps/yr`],
              ["Management fee", `${bps(-r.fees_bps_pa)} bps/yr`],
              ["Benchmark (a) − (b)", r.proxy_gap_bps === null ? "official TR not in snapshot" : `${bps(r.proxy_gap_bps)} bps/yr (cap-weight proxy vs official TR)`],
              ["Total vs price return", `${bps(r.price_vs_total_bps)} bps/yr (dividends)`],
              ["Net investor flows", `${usd(r.flows_net_usd)} over ${num(r.flow_days)} flow days`],
            ]}
          />
          <h4 className="mb-2 mt-5 text-xs font-semibold uppercase tracking-wide text-ink-2">Worst 10 days</h4>
          <table className="w-full text-xs">
            <tbody>
              {r.worst_days.map((w: any) => (
                <tr key={w.date} className="border-b border-line/60">
                  <td className="num py-1">{w.date}</td>
                  <td className="num py-1 text-right">{pct(w.fund_return)}</td>
                  <td className="num py-1 text-right text-ink-3">bench {pct(w.bench_return)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
      <ExcludedWeight data={b.data.excluded_weight} />
    </div>
  );
}

function ExcludedWeight({ data }: { data: Record<string, number> | null }) {
  if (!data) return null;
  const rows = Object.entries(data).map(([d, w]) => ({ date: d, w }));
  return (
    <Card>
      <CardHeader title="Index weight excluded per decision" subtitle="Names with pending unsupported corporate actions or no BMLL mapping (handoff: 0.6–3.3% per quarter)" />
      <div className="flex flex-wrap gap-2 p-4 text-xs">
        {rows.map((r) => (
          <span key={r.date} className="num rounded border border-line px-2 py-1">{r.date}: {pct(r.w, 2)}</span>
        ))}
      </div>
    </Card>
  );
}

function Holdings({ runId }: { runId: string }) {
  const [on, setOn] = useQueryState("on", parseAsString.withDefault(""));
  const h = useJson(["holdings", runId, on], `/runs/${runId}/holdings`, { on: on || undefined });
  if (h.isLoading) return <Skeleton className="h-96" />;
  if (h.error) return <ErrorBox error={h.error} />;
  const d = h.data;
  return (
    <div className="space-y-4">
      <div className="flex items-end gap-3">
        <label className="flex flex-col gap-1">
          <span className="text-[10px] font-medium uppercase tracking-wide text-ink-3">As of</span>
          <Input type="date" value={on || String(d.date ?? "")} onChange={(e) => setOn(e.target.value)} className="w-44" />
        </label>
        <div className="pb-2 text-xs text-ink-3">{num(d.rows.length)} positions · NAV {usd(d.nav)} · targets from decision {d.decision?.decision_id?.slice(0, 10) ?? "—"} (effective {d.decision?.effective_date ?? "—"})</div>
      </div>
      <div className="grid gap-4 xl:grid-cols-[1fr_380px]">
        <Card>
          <CardHeader title="Weights vs target" subtitle="Active weight = held − target (CRSP cap-weight proxy)" />
          <DataTable data={d.rows} height={560} className="rounded-none border-x-0 border-b-0" columns={holdCols} rowKey={(r: any) => String(r.permno)} />
        </Card>
        <Card>
          <CardHeader title="Excluded names" subtitle="With the reason recorded at the decision" />
          {d.excluded.length === 0 ? <div className="p-4"><Empty title="No exclusions at this decision" /></div> : (
            <ul className="divide-y divide-line text-xs">
              {d.excluded.map((x: any) => (
                <li key={x.permno} className="px-4 py-2">
                  <div className="font-medium text-ink">{x.ticker ?? x.permno} <span className="font-normal text-ink-3">PERMNO {x.permno}</span></div>
                  <div className="text-ink-2">{x.reason}</div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}

const holdCols: Col<any>[] = [
  { header: "Ticker", accessorKey: "ticker" },
  { header: "PERMNO", accessorKey: "permno" },
  { header: "Shares", accessorKey: "shares", cell: (c) => num(c.getValue()), meta: { num: true } },
  { header: "Close", accessorKey: "price", cell: (c) => px(c.getValue()), meta: { num: true } },
  { header: "Market value", accessorKey: "market_value", cell: (c) => usd(c.getValue()), meta: { num: true } },
  { header: "Weight", accessorKey: "weight", cell: (c) => pct(c.getValue(), 3), meta: { num: true } },
  { header: "Target", accessorKey: "target_weight", cell: (c) => pct(c.getValue(), 3), meta: { num: true } },
  { header: "Active (bps)", accessorKey: "active_weight_bps", cell: (c) => <CostCell v={c.getValue()} max={5} />, meta: { num: true } },
];

function Flows({ runId }: { runId: string }) {
  const q = useJson(["flows", runId], `/runs/${runId}/flows`);
  if (q.isLoading) return <Skeleton className="h-96" />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data;
  const subs = d.flows.filter((f: any) => f.amount_usd > 0).reduce((a: number, f: any) => a + f.amount_usd, 0);
  const reds = d.flows.filter((f: any) => f.amount_usd < 0).reduce((a: number, f: any) => a + f.amount_usd, 0);
  const jumps = d.flows.filter((f: any) => f.is_jump);
  if (!d.config.enabled) return <Empty title="Flows are off in this run" detail="The handoff replay runs without investor flows. Submit a run with the default flow preset to see subscriptions and redemptions." action="New run" href="/runs/new" />;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="Subscriptions" value={<span className="num">{usd(subs)}</span>} />
        <Stat label="Redemptions" value={<span className="num">{usd(reds)}</span>} />
        <Stat label="Net" value={<span className="num">{usd(subs + reds)}</span>} />
        <Stat label="Large redemptions (jumps)" value={<span className="num">{jumps.length}</span>} sub={`p = ${d.config.jump_prob}, ${d.config.jump_min_bps} to ${d.config.jump_max_bps} bps`} />
        <Stat label="Process" value={<span className="num">σ {d.config.sigma_bps} bps</span>} sub={`φ ${d.config.phi}, ν ${d.config.nu}, buffer ${d.buffer_bps} bps`} />
      </div>
      <Card>
        <CardHeader title="Daily subscriptions and redemptions" subtitle="Dealt at the close at pre-flow unit NAV, so deposits never show up as returns; cash-flow orders are MOC on the flow date" />
        <div className="p-2"><FlowsChart flows={d.flows} /></div>
      </Card>
      <Card>
        <CardHeader title="Cash vs buffer" subtitle="Net liquid cash (cash + receivables − payables) as bps of NAV" />
        <div className="p-2"><CashChart cash={d.cash} bufferBps={d.buffer_bps} /></div>
      </Card>
    </div>
  );
}
