"use client";

import { Download } from "lucide-react";
import Link from "next/link";
import { parseAsString, useQueryStates } from "nuqs";
import * as React from "react";

import { BucketBars, ConcentrationChart, CostScatter, Heatmap, MethodBars } from "@/components/charts";
import { type Col, DataTable } from "@/components/DataTable";
import { LineageDrawer } from "@/components/Lineage";
import { Badge, Button, Card, CardHeader, CostCell, DataLabel, Empty, ErrorBox, Select, Seg, Skeleton, Stat } from "@/components/ui";
import { exportTable } from "@/lib/exports";
import { useJson } from "@/lib/hooks";
import { bps, num, pct, usd } from "@/lib/format";

const METHODS = ["ACTUAL", "T0", "MOC", "VWAP", "ARRIVAL", "HANDOFF"] as const;
const ACTUAL_METRICS = [
  ["exec", "Execution + explicit vs arrival"],
  ["is", "Implementation shortfall vs decision"],
  ["slip_benchmark", "vs strategy benchmark"],
  ["slip_close", "vs close"],
  ["slip_vwap", "vs day VWAP"],
  ["delay", "Delay (decision → arrival)"],
  ["explicit", "Explicit (fees + commission)"],
];

export function TcaPanel({ runId }: { runId: string }) {
  const [f, setF] = useQueryStates({
    date: parseAsString.withDefault(""),
    method: parseAsString.withDefault("ACTUAL"),
    metric: parseAsString.withDefault("exec"),
    reason: parseAsString.withDefault(""),
    cut: parseAsString.withDefault("size_bucket"),
  });
  const [pick, setPick] = React.useState<string | null>(null);
  const metric = f.method === "ACTUAL" ? f.metric : f.metric === "vs_decision" ? "vs_decision" : "vs_close";
  const common = { date: f.date || undefined, reason: f.reason || undefined };
  const dates = useJson(["tca-dates", runId], `/runs/${runId}/tca/dates`);
  const total = useJson(["tca", runId, "total"], `/runs/${runId}/tca`, { ...common, group_by: f.cut, method: f.method, metric });
  const byDate = useJson(["tca", runId, "date"], `/runs/${runId}/tca`, { reason: common.reason, group_by: "date", method: f.method, metric });
  const heat = useJson(["tca-heat", runId], `/runs/${runId}/tca/heatmap`, { ...common, method: f.method, metric });
  const scatter = useJson(["tca-scatter", runId], `/runs/${runId}/tca/scatter`, { ...common, method: f.method, metric });
  const methods = useJson(["tca-methods", runId], `/runs/${runId}/tca/methods`, { ...common, metric: "vs_close" });
  const outl = useJson(["tca-outliers", runId], `/runs/${runId}/tca/outliers`, { date: common.date });
  const conc = useJson(["tca-conc", runId], `/runs/${runId}/tca/concentration`);
  const net = useJson(["netting", runId], `/runs/${runId}/netting`);

  const t = total.data;
  const rebalDates = (dates.data ?? []).filter((d: any) => d.rebalance_orders > 50);
  const weighting = "Notional-weighted (executed $). Equal-weighted shown only as a secondary column.";
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1">
          <span className="text-[10px] font-medium uppercase tracking-wide text-ink-3">Date</span>
          <Select value={f.date} onChange={(e) => setF({ date: e.target.value || null })} className="w-56">
            <option value="">All dates</option>
            {rebalDates.length > 0 && (
              <optgroup label="Rebalance dates">
                {rebalDates.map((d: any) => (
                  <option key={d.date} value={d.date}>
                    {d.date} · {d.rebalance_orders} rebal orders · {usd(d.notional)}
                  </option>
                ))}
              </optgroup>
            )}
            <optgroup label="All execution dates">
              {(dates.data ?? []).slice(-400).map((d: any) => (
                <option key={`a${d.date}`} value={d.date}>
                  {d.date} · {d.orders} orders
                </option>
              ))}
            </optgroup>
          </Select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[10px] font-medium uppercase tracking-wide text-ink-3">Method</span>
          <Seg value={f.method as any} options={METHODS.map((m) => ({ value: m, label: m === "ACTUAL" ? "As executed" : m }))} onChange={(v) => setF({ method: v, metric: v === "ACTUAL" ? "exec" : "vs_close" })} />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[10px] font-medium uppercase tracking-wide text-ink-3">Metric</span>
          {f.method === "ACTUAL" ? (
            <Select value={f.metric} onChange={(e) => setF({ metric: e.target.value })}>
              {ACTUAL_METRICS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </Select>
          ) : (
            <Select value={metric} onChange={(e) => setF({ metric: e.target.value })}>
              <option value="vs_close">vs T0 close (execution cost)</option>
              <option value="vs_decision">vs decision price (incl. delay)</option>
            </Select>
          )}
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[10px] font-medium uppercase tracking-wide text-ink-3">Orders</span>
          <Select value={f.reason} onChange={(e) => setF({ reason: e.target.value || null })}>
            <option value="">All reasons</option>
            <option value="INDEX_REBALANCE,INDEX_ADD,INDEX_DELETE">Rebalance (incl. adds/deletes)</option>
            <option value="SUBSCRIPTION,REDEMPTION,CASH_SWEEP">Cash-flow orders</option>
            <option value="INITIAL">Initial</option>
            <option value="CORP_ACTION">Corporate-action exits</option>
            <option value="SYNTHETIC_TEST">Phase 1 generator</option>
          </Select>
        </label>
        <div className="ml-auto flex items-center gap-2">
          <DataLabel label={t?.data_label} />
          {f.date && (
            <Link href={`/tca/one-date?run=${runId}&date=${f.date}`} className="text-xs font-medium text-accent hover:underline">
              One-date study →
            </Link>
          )}
          <Button size="sm" onClick={() => exportTable(runId, "tca_buckets")}>
            <Download className="h-3.5 w-3.5" /> Buckets CSV
          </Button>
        </div>
      </div>

      {total.error && <ErrorBox error={total.error} />}
      {total.isLoading ? (
        <Skeleton className="h-24" />
      ) : t?.empty ? (
        <Empty title="No TCA rows for this selection" detail={t.empty} />
      ) : (
        t && (
          <>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
              <Stat label="Weighted cost" value={<span className="num">{bps(t.total?.cost_bps)} bps</span>} sub={`90% CI ${bps(t.total?.ci90?.[0])} to ${bps(t.total?.ci90?.[1])} · block bootstrap by date`} />
              <Stat label="Cost $" value={<span className="num">{usd(t.total?.cost_usd)}</span>} sub={`equal-weighted ${bps(t.total?.eq_weighted_bps)} bps (secondary)`} />
              <Stat label="Notional traded" value={<span className="num">{usd(t.total?.notional)}</span>} sub={`${num(t.total?.orders)} orders`} />
              <Stat label="Tracking difference" value={<span className="num">{bps(t.td?.td_net_bps)} bps/yr</span>} sub={t.td ? `TE ${bps(t.td.te_bps)} bps · gross ${bps(t.td.td_gross_bps)}` : "fund report not available"} />
              <Stat
                label="Fallbacks"
                value={<span className="num">{pct(t.fallbacks?.fallback_share, 3)}</span>}
                sub={t.fallbacks?.provisional ? <Badge tone="serious">Provisional: fallbacks above 1% of notional</Badge> : "of notional used a BMLL fallback"}
              />
            </div>

            <div className="grid gap-4 xl:grid-cols-2">
              <Card>
                <CardHeader
                  title="Cost by size against liquidity"
                  subtitle={weighting}
                  right={
                    <Seg value={f.cut as any} options={[{ value: "size_bucket", label: "Q/ADV20" }, { value: "auction_bucket", label: "Q/auction" }, { value: "main_strategy", label: "Strategy" }, { value: "main_broker", label: "Broker" }, { value: "main_venue", label: "Venue" }]} onChange={(v) => setF({ cut: v })} />
                  }
                />
                <div className="p-2">
                  <BucketBars groups={t.groups} label={f.cut.replace("_", " ")} />
                </div>
                <DataTable
                  data={t.groups}
                  height="auto"
                  className="rounded-none border-x-0 border-b-0"
                  columns={bucketCols}
                  rowKey={(r: any) => JSON.stringify(r.key)}
                />
              </Card>
              <Card>
                <CardHeader title="Method comparison" subtitle="Same orders repriced: cost vs the T0 close (zero-cost baseline), notional-weighted" />
                <div className="p-3">{methods.data?.methods ? <MethodBars methods={methods.data.methods} /> : <Skeleton className="h-40" />}</div>
                <CardHeader title="Cost vs Q / auction volume" subtitle="Each order sized by notional · notional-weighted totals above · click a point to open its lineage" className="border-t" />
                <div className="p-2">{scatter.data ? <CostScatter points={scatter.data} onPick={setPick} /> : <Skeleton className="h-72" />}</div>
              </Card>
            </div>

            <div className="grid gap-4 xl:grid-cols-2">
              <Card>
                <CardHeader title="Weighted cost: %ADV × %auction volume" subtitle="Notional-weighted bps per cell · diverging scale centred on zero (red = cost)" />
                <div className="p-2">{heat.data ? <Heatmap data={heat.data} /> : <Skeleton className="h-72" />}</div>
              </Card>
              <Card>
                <CardHeader title="Concentration of rebalance notional" subtitle="Cumulative notional by share of orders, one line per rebalance date · handoff: top 10% carried 85%" />
                <div className="p-2">{conc.data?.length ? <ConcentrationChart dates={conc.data} /> : <Empty title="No rebalance dates in this run" />}</div>
                <div className="flex flex-wrap gap-2 px-4 pb-3 text-[11px] text-ink-3">
                  {(conc.data ?? []).map((c: any) => (
                    <span key={c.date} className="num">{c.date}: {pct(c.top10_share, 1)}</span>
                  ))}
                </div>
              </Card>
            </div>

            {!f.date && byDate.data?.groups && (
              <Card>
                <CardHeader title="Cost by date beside tracking difference" subtitle={`${weighting} · TD for the same run: ${bps(t.td?.td_net_bps)} bps/yr, TE ${bps(t.td?.te_bps)} bps`} />
                <DataTable
                  data={(byDate.data.groups as any[]).filter((g) => g.notional > 1e8).slice().reverse()}
                  height={360}
                  className="rounded-none border-x-0 border-b-0"
                  columns={dateCols}
                  rowKey={(r: any) => r.key.date}
                  onRowClick={(r: any) => setF({ date: r.key.date })}
                />
              </Card>
            )}

            <div className="grid gap-4 xl:grid-cols-2">
              <Card>
                <CardHeader title="Where the methods disagree most" subtitle="Spread between MOC, VWAP and arrival repricings, ranked by $ · click to open lineage" />
                <DataTable data={outl.data ?? []} height={360} className="rounded-none border-x-0 border-b-0" columns={outlierCols} rowKey={(r: any) => r.pm_order_id} onRowClick={(r: any) => setPick(r.pm_order_id)} />
              </Card>
              <Card>
                <CardHeader title="Flow vs rebalance netting" subtitle="Opposite-side flow and rebalance quantity crossed internally at the close" />
                <div className="grid grid-cols-3 gap-3 p-4">
                  <Stat label="Netting events" value={num(net.data?.summary?.events)} />
                  <Stat label="Notional saved" value={usd(net.data?.summary?.saved_notional)} />
                  <Stat label="Saved, bps of NAV" value={bps(net.data?.summary?.saved_bps_of_nav, 2)} />
                </div>
                <DataTable data={net.data?.by_date ?? []} height={220} className="rounded-none border-x-0 border-b-0" columns={netCols} rowKey={(r: any) => r.date} />
              </Card>
            </div>
          </>
        )
      )}
      <LineageDrawer pmOrderId={pick} onClose={() => setPick(null)} />
    </div>
  );
}

const bucketCols: Col<any>[] = [
  { header: "Group", accessorFn: (r) => Object.values(r.key).join(" · ") },
  { header: "Orders", accessorKey: "orders", cell: (c) => num(c.getValue()), meta: { num: true } },
  { header: "Notional share", accessorKey: "notional_share", cell: (c) => pct(c.getValue(), 1), meta: { num: true } },
  { header: "Cost (bps)", accessorKey: "cost_bps", cell: (c) => <CostCell v={c.getValue()} />, meta: { num: true } },
  { header: "90% CI (bps)", accessorFn: (r) => `${bps(r.ci90?.[0])} to ${bps(r.ci90?.[1])}`, meta: { num: true } },
  { header: "Cost ($)", accessorKey: "cost_usd", cell: (c) => usd(c.getValue()), meta: { num: true } },
  { header: "Equal-wtd (bps)", accessorKey: "eq_weighted_bps", cell: (c) => <span className="text-ink-3">{bps(c.getValue())}</span>, meta: { num: true } },
];

const dateCols: Col<any>[] = [
  { header: "Date", accessorFn: (r) => r.key.date },
  { header: "Orders", accessorKey: "orders", cell: (c) => num(c.getValue()), meta: { num: true } },
  { header: "Notional", accessorKey: "notional", cell: (c) => usd(c.getValue()), meta: { num: true } },
  { header: "Cost (bps)", accessorKey: "cost_bps", cell: (c) => <CostCell v={c.getValue()} />, meta: { num: true } },
  { header: "90% CI", accessorFn: (r) => `${bps(r.ci90?.[0])} to ${bps(r.ci90?.[1])}`, meta: { num: true } },
  { header: "Cost ($)", accessorKey: "cost_usd", cell: (c) => usd(c.getValue()), meta: { num: true } },
];

const outlierCols: Col<any>[] = [
  { header: "Date", accessorKey: "date" },
  { header: "Ticker", accessorKey: "ticker" },
  { header: "Side", accessorKey: "side" },
  { header: "Notional", accessorKey: "notional", cell: (c) => usd(c.getValue()), meta: { num: true } },
  { header: "MOC", accessorKey: "moc_bps", cell: (c) => bps(c.getValue()), meta: { num: true } },
  { header: "VWAP", accessorKey: "vwap_bps", cell: (c) => bps(c.getValue()), meta: { num: true } },
  { header: "Arrival", accessorKey: "arrival_bps", cell: (c) => bps(c.getValue()), meta: { num: true } },
  { header: "Spread (bps)", accessorKey: "spread_bps", cell: (c) => bps(c.getValue()), meta: { num: true } },
  { header: "Spread ($)", accessorKey: "spread_usd", cell: (c) => usd(c.getValue()), meta: { num: true } },
];

const netCols: Col<any>[] = [
  { header: "Date", accessorKey: "date" },
  { header: "Names", accessorKey: "names", meta: { num: true } },
  { header: "Shares crossed", accessorKey: "shares", cell: (c) => num(c.getValue()), meta: { num: true } },
  { header: "Saved", accessorKey: "saved", cell: (c) => usd(c.getValue()), meta: { num: true } },
];
