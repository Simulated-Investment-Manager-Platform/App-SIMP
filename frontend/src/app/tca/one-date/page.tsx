"use client";

import { parseAsString, useQueryState } from "nuqs";
import * as React from "react";

import { CostScatter } from "@/components/charts";
import { type Col, DataTable } from "@/components/DataTable";
import { LineageDrawer } from "@/components/Lineage";
import { RunScope } from "@/components/RunPicker";
import { Card, CardHeader, CostCell, Empty, ErrorBox, Input, Skeleton, Stat } from "@/components/ui";
import { useJson } from "@/lib/hooks";
import { bps, num, pct, px, usd } from "@/lib/format";

export default function OneDatePage() {
  return (
    <RunScope title="One rebalance date, end to end" subtitle="Auction price, volume, imbalance, ADV, spread and VWAP per order; repriced three ways; sized as % ADV and % auction.">
      {(runId) => <OneDate runId={runId} />}
    </RunScope>
  );
}

function OneDate({ runId }: { runId: string }) {
  const [date, setDate] = useQueryState("date", parseAsString.withDefault("2026-03-20"));
  const [pick, setPick] = React.useState<string | null>(null);
  const q = useJson(["one-date", runId, date], `/runs/${runId}/study/one-date`, { date });
  const d = q.data;
  const err = q.error as any;
  return (
    <div className="space-y-4">
      <div className="flex items-end gap-3">
        <label className="flex flex-col gap-1">
          <span className="text-[10px] font-medium uppercase tracking-wide text-ink-3">Effective date</span>
          <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} className="w-44" />
        </label>
        <span className="pb-2 text-xs text-ink-3">Default 2026-03-20, the March 2026 effective Friday.</span>
      </div>
      {q.isLoading && <Skeleton className="h-96" />}
      {err && (
        <div className="space-y-2">
          <ErrorBox error={err.detail?.message ?? err} title={`No study for ${date}`} />
          {err.detail?.rebalance_dates && (
            <div className="flex flex-wrap gap-2 text-xs">
              Rebalance dates in this run:
              {err.detail.rebalance_dates.slice(-16).map((x: string) => (
                <button key={x} onClick={() => setDate(x)} className="rounded border border-line px-1.5 py-0.5 text-accent hover:bg-line/50">{x}</button>
              ))}
            </div>
          )}
        </div>
      )}
      {d && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
            {d.totals.map((t: any) => (
              <Stat key={t.method} label={t.method === "ACTUAL" ? "As executed" : t.method} value={<span className="num">{bps(t.vs_close_bps)} bps</span>} sub={`${usd(t.vs_close_usd)} vs close · ${bps(t.vs_decision_bps)} vs decision`} />
            ))}
          </div>
          <p className="text-[11px] text-ink-3">Totals are notional-weighted over {num(d.orders.length)} PM orders. vs close = execution cost against the zero-cost T0 close; vs decision includes the delay from the announcement close.</p>

          <div className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
            <Card>
              <CardHeader title="MOC cost vs Q / auction volume" subtitle="Auction-impact model κσ(Q/V)^β(1+λ·same-side) · markers sized by notional" />
              <div className="p-2"><CostScatter points={d.scatter} onPick={setPick} /></div>
            </Card>
            <Card>
              <CardHeader title="Named orders" subtitle="Largest orders called out by name" />
              <div className="space-y-3 p-4 text-xs">
                {[["Largest rebalance order on this date", d.callouts.largest_rebalance_order_on_date], ["Largest rebalance order in the run", d.callouts.largest_rebalance_order_in_run], ["Largest initial order", d.callouts.largest_initial_order]].map(([l, o]: any) =>
                  o ? (
                    <button key={l} onClick={() => setPick(o.pm_order_id)} className="block w-full rounded-md border border-line p-3 text-left hover:border-accent/60">
                      <div className="text-[10px] uppercase tracking-wide text-ink-3">{l}</div>
                      <div className="mt-0.5 text-sm font-medium text-ink">{o.ticker} · {usd(o.exec_notional)} · {String(o.date ?? date).slice(0, 10)}</div>
                      <div className="text-ink-2">exec cost {bps(o.exec_cost_bps)} bps{o.moc_vs_close_bps !== undefined ? ` · MOC vs close ${bps(o.moc_vs_close_bps)} bps` : ""}</div>
                    </button>
                  ) : null,
                )}
              </div>
            </Card>
          </div>

          <Card>
            <CardHeader title="T2 tick replay of the top ten orders by notional" subtitle="VWAP 09:30–15:50 against interval prints, 10% of each interval's volume, aggressive slices pay the half spread" />
            {d.t2_top10?.[0]?.error ? <div className="p-4"><Empty title="T2 unavailable" detail={d.t2_top10[0].error} /></div> : (
              <DataTable data={d.t2_top10} height="auto" className="rounded-none border-x-0 border-b-0" columns={t2Cols} rowKey={(r: any) => r.pm_order_id} onRowClick={(r: any) => setPick(r.pm_order_id)} />
            )}
          </Card>

          <Card>
            <CardHeader title={`All PM orders on ${d.date}`} subtitle="One row per PM order · BMLL market figures, size against liquidity, three repricings and the handoff T0 fill" />
            <DataTable data={d.orders} height={560} className="rounded-none border-x-0 border-b-0" columns={orderCols} rowKey={(r: any) => r.pm_order_id} onRowClick={(r: any) => setPick(r.pm_order_id)} />
          </Card>
          <Card>
            <CardHeader title="Where the methods disagree most" subtitle="Ranked by $ spread between MOC, VWAP and arrival" />
            <DataTable data={d.disagreement} height="auto" className="rounded-none border-x-0 border-b-0" columns={disCols} rowKey={(r: any) => r.pm_order_id} onRowClick={(r: any) => setPick(r.pm_order_id)} />
          </Card>
        </>
      )}
      <LineageDrawer pmOrderId={pick} onClose={() => setPick(null)} />
    </div>
  );
}

const orderCols: Col<any>[] = [
  { header: "Ticker", accessorKey: "ticker" },
  { header: "Side", accessorKey: "side" },
  { header: "Shares", accessorKey: "quantity", cell: (c) => num(c.getValue()), meta: { num: true } },
  { header: "Notional", accessorKey: "notional", cell: (c) => usd(c.getValue()), meta: { num: true } },
  { header: "Reason", accessorKey: "reason" },
  { header: "Decision px", accessorKey: "decision_price", cell: (c) => px(c.getValue()), meta: { num: true } },
  { header: "Auction px", accessorKey: "auction_price", cell: (c) => px(c.getValue()), meta: { num: true } },
  { header: "Auction vol ($)", accessorKey: "auction_notional", cell: (c) => usd(c.getValue()), meta: { num: true } },
  { header: "Imbalance", accessorFn: (r) => (r.imbalance_side ? `${r.imbalance_side} ${usd(r.imbalance_notional)}` : "—") },
  { header: "ADV20 (sh)", accessorKey: "adv20", cell: (c) => num(c.getValue()), meta: { num: true } },
  { header: "ADV60 (sh)", accessorKey: "adv60", cell: (c) => num(c.getValue()), meta: { num: true } },
  { header: "Spread (bps)", accessorKey: "spread_bps", cell: (c) => num(c.getValue(), 1), meta: { num: true } },
  { header: "Day VWAP", accessorKey: "vwap_day", cell: (c) => px(c.getValue()), meta: { num: true } },
  { header: "σ intraday", accessorKey: "vol_intraday", cell: (c) => pct(c.getValue()), meta: { num: true } },
  { header: "Q/ADV20", accessorKey: "pct_adv", cell: (c) => pct(c.getValue(), 3), meta: { num: true } },
  { header: "Q/auction", accessorKey: "pct_auction", cell: (c) => pct(c.getValue(), 3), meta: { num: true } },
  { header: "Q/exp. auction", accessorKey: "pct_exp_auction", cell: (c) => pct(c.getValue(), 3), meta: { num: true } },
  { header: "MOC vs dec.", accessorKey: "moc_bps", cell: (c) => <CostCell v={c.getValue()} max={150} />, meta: { num: true } },
  { header: "MOC vs close", accessorKey: "moc_vs_close_bps", cell: (c) => <CostCell v={c.getValue()} />, meta: { num: true } },
  { header: "VWAP vs dec.", accessorKey: "vwap_bps", cell: (c) => <CostCell v={c.getValue()} max={150} />, meta: { num: true } },
  { header: "Arrival vs dec.", accessorKey: "arrival_bps", cell: (c) => <CostCell v={c.getValue()} max={150} />, meta: { num: true } },
  { header: "Handoff T0", accessorKey: "handoff_bps", cell: (c) => <CostCell v={c.getValue()} max={150} />, meta: { num: true } },
];

const t2Cols: Col<any>[] = [
  { header: "Ticker", accessorKey: "ticker" },
  { header: "Notional", accessorKey: "notional", cell: (c) => usd(c.getValue()), meta: { num: true } },
  { header: "Q/ADV20", accessorKey: "pct_adv", cell: (c) => pct(c.getValue(), 3), meta: { num: true } },
  { header: "T2 avg px", accessorKey: "t2_avg_price", cell: (c) => px(c.getValue()), meta: { num: true } },
  { header: "Fill rate", accessorKey: "t2_fill_rate", cell: (c) => pct(c.getValue(), 1), meta: { num: true } },
  { header: "Fills", accessorKey: "fills", meta: { num: true } },
  { header: "T2 vs close (bps)", accessorKey: "t2_vs_close_bps", cell: (c) => <CostCell v={c.getValue()} />, meta: { num: true } },
  { header: "MOC vs close (bps)", accessorKey: "moc_vs_close_bps", cell: (c) => <CostCell v={c.getValue()} />, meta: { num: true } },
  { header: "T2 vs decision (bps)", accessorKey: "t2_vs_decision_bps", cell: (c) => <CostCell v={c.getValue()} max={150} />, meta: { num: true } },
];

const disCols: Col<any>[] = [
  { header: "Ticker", accessorKey: "ticker" },
  { header: "Side", accessorKey: "side" },
  { header: "Notional", accessorKey: "notional", cell: (c) => usd(c.getValue()), meta: { num: true } },
  { header: "MOC", accessorKey: "moc_bps", cell: (c) => bps(c.getValue()), meta: { num: true } },
  { header: "VWAP", accessorKey: "vwap_bps", cell: (c) => bps(c.getValue()), meta: { num: true } },
  { header: "Arrival", accessorKey: "arrival_bps", cell: (c) => bps(c.getValue()), meta: { num: true } },
  { header: "Spread ($)", accessorKey: "spread_usd", cell: (c) => usd(c.getValue()), meta: { num: true } },
];
