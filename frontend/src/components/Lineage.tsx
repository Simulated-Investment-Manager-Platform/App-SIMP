"use client";

import { useQuery } from "@tanstack/react-query";

import { Timeline } from "@/components/aceternity";
import { IntradayChart } from "@/components/charts";
import { type Col, DataTable } from "@/components/DataTable";
import { Badge, Card, CardHeader, CostCell, Drawer, ErrorBox, KV, Side, Skeleton, Status } from "@/components/ui";
import { getJson } from "@/lib/api/client";
import { bps, clock, dt, num, pct, px, usd } from "@/lib/format";

const KIND_TONE: Record<string, string> = { decision: "bg-[#6250d6]", trader: "bg-accent", fill: "bg-good", close: "bg-ink-3" };

export function LineageDrawer({ pmOrderId, onClose }: { pmOrderId: string | null; onClose: () => void }) {
  const q = useQuery({ queryKey: ["lineage", pmOrderId], enabled: !!pmOrderId, queryFn: () => getJson(`/orders/${pmOrderId}/lineage`) });
  const d = q.data;
  const pm = d?.pm_order;
  const t = d?.tca;
  return (
    <Drawer
      open={!!pmOrderId}
      onOpenChange={(v) => !v && onClose()}
      title={pm ? `${t?.ticker ?? pm.permno} · ${pm.side} ${num(pm.quantity)} sh · ${pm.reason}` : "Order lineage"}
      subtitle={pmOrderId ? `PM order ${pmOrderId} · decision → trader orders → child orders → fills → close` : undefined}
    >
      {q.isLoading && <Skeleton className="h-96" />}
      {q.error && <ErrorBox error={q.error} />}
      {d && (
        <div className="space-y-5">
          <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
            <Card>
              <CardHeader title="Intraday price, execution date" subtitle={d.chart?.synthetic_intraday ? "Synthetic minute path rebuilt from lake columns · execution-date share basis" : "Day VWAP only (no tick data pulled)"} />
              <div className="p-2">
                {d.chart ? (
                  <IntradayChart chart={d.chart} fills={d.fills} decision={d.chart.decision_price_exec_basis} arrival={d.chart.arrival_price} />
                ) : (
                  <div className="p-6 text-xs text-ink-3">No intraday data for this date.</div>
                )}
              </div>
            </Card>
            <Card className="p-4">
              <h4 className="mb-3 text-xs font-semibold uppercase tracking-wide text-ink-2">Timeline</h4>
              <div className="max-h-[300px] overflow-y-auto pr-1">
                <Timeline
                  items={(d.timeline as any[]).slice(0, 40).map((e, i) => ({
                    key: `${e.kind}${i}`,
                    time: dt(e.ts),
                    title: <span>{e.label}</span>,
                    body: e.price ? `${px(e.price)}${e.quantity ? ` · ${num(e.quantity)} sh` : ""}` : undefined,
                    tone: KIND_TONE[e.kind],
                  }))}
                />
              </div>
            </Card>
          </div>

          <div className="grid gap-4 md:grid-cols-3">
            <Card className="p-4">
              <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-2">PM order</h4>
              <KV
                items={[
                  ["Side", <Side key="s" side={pm.side} />],
                  ["Quantity", `${num(pm.quantity)} (decision basis)`],
                  ["Filled", num(pm.filled_qty)],
                  ["Split factor", pm.quantity_adjustment_factor],
                  ["Instruction", pm.instruction],
                  ["Decision", dt(pm.decision_ts)],
                  ["Decision price", `${px(pm.decision_price)} (${pm.decision_price_source})`],
                  ["Deadline", dt(pm.deadline_ts)],
                  ["Status", <Status key="st" value={pm.status} />],
                  ["Decision type", d.decision?.decision_type],
                  ["Created by", pm.created_by],
                ]}
              />
            </Card>
            <Card className="p-4">
              <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-2">Implementation shortfall (bps)</h4>
              {t ? (
                <KV
                  items={[
                    ["Delay", <CostCell key="a" v={t.delay_bps} max={100} />],
                    ["Execution", <CostCell key="b" v={t.execution_bps} />],
                    ["Explicit", <CostCell key="c" v={t.explicit_bps} />],
                    ["Opportunity", <CostCell key="d" v={t.opportunity_bps} />],
                    ["Total IS", <CostCell key="e" v={t.is_bps} max={100} />],
                    ["Exec cost (excl. delay)", <CostCell key="f" v={t.exec_cost_bps} />],
                    ["vs benchmark", <CostCell key="g" v={t.slip_benchmark_bps} />],
                    ["Cost $", usd(t.cost_usd)],
                  ]}
                />
              ) : (
                <div className="text-xs text-ink-3">No TCA row (order cancelled before execution).</div>
              )}
            </Card>
            <Card className="p-4">
              <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-2">Liquidity & reference</h4>
              {t && (
                <KV
                  items={[
                    ["Q / ADV20", pct(t.pct_adv, 3)],
                    ["Q / auction vol", pct(t.pct_auction, 3)],
                    ["Q / expected auction", pct(t.pct_exp_auction, 3)],
                    ["Spread", `${bps(t.spread_bps)} bps`],
                    ["Intraday vol", pct(t.vol_intraday)],
                    ["Imbalance", t.imbalance_side ? `${t.imbalance_side} ${usd(t.imbalance_notional)}` : "—"],
                    ["Arrival / avg / close", `${px(t.arrival_price)} / ${px(t.avg_price)} / ${px(t.close_price)}`],
                    ["Markouts 1s/5s/60s/300s", [t.markout_1s, t.markout_5s, t.markout_60s, t.markout_300s].map((x: number) => bps(x)).join(" / ")],
                    ["Next open vs close", `${bps(t.next_open_vs_close_bps)} bps`],
                    ["Flags", (t.flags ?? []).join(", ") || "none"],
                  ]}
                />
              )}
            </Card>
          </div>

          {d.reprice && (
            <Card className="p-4">
              <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-2">Repriced three ways (+ T0 and handoff), cost vs decision, bps</h4>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
                {[["T0 close", "t0"], ["MOC", "moc"], ["VWAP", "vwap"], ["Arrival", "arrival"], ["Handoff (Tue close)", "handoff"]].map(([l, k]) => (
                  <div key={k} className="rounded-md border border-line p-2">
                    <div className="text-[10px] uppercase text-ink-3">{l}</div>
                    <div className="num text-sm">{px(d.reprice[`${k}_price`])}</div>
                    <CostCell v={d.reprice[`${k}_bps`]} max={100} />
                  </div>
                ))}
              </div>
            </Card>
          )}

          {d.program && (
            <div className="text-xs text-ink-2">
              <Badge>PORTFOLIO_TRADE</Badge> program {d.program.program_id} via {d.program.broker_id}: {d.program.n_orders} orders, {usd(d.program.notional)}, fee {d.program.fee_bps} bps, closing-price guarantee.
            </div>
          )}

          {(d.trader_orders as any[]).map((to) => (
            <Card key={to.trader_order_id}>
              <CardHeader
                title={
                  <span className="flex flex-wrap items-center gap-2">
                    {to.strategy} via {to.broker_id} <Status value={to.status} />
                  </span>
                }
                subtitle={`${to.trader_order_id} · ${num(to.quantity)} sh · ${clock(to.start_ts)}–${clock(to.end_ts)} ET · arrival ${px(to.arrival_price)} · ${to.commission_bps} bps commission · rule ${to.params?.rule ?? "—"}`}
              />
              <DataTable
                data={(to.children as any[]).flatMap((c) => (c.fills.length ? c.fills.map((f: any) => ({ ...f, child: c })) : [{ child: c }]))}
                height={260}
                rowKey={(r: any, i) => r.fill_id ?? `${r.child.child_order_id}${i}`}
                columns={fillColumns}
              />
            </Card>
          ))}
        </div>
      )}
    </Drawer>
  );
}

const fillColumns: Col<any>[] = [
  { header: "Child", accessorFn: (r) => r.child.child_order_id.slice(-8), cell: (c) => <span className="font-mono">{c.getValue()}</span> },
  { header: "Venue", accessorFn: (r) => r.child.venue_mic },
  { header: "Type", accessorFn: (r) => r.child.order_type },
  { header: "Sent (ET)", accessorFn: (r) => clock(r.child.sent_ts) },
  { header: "Exec (ET)", accessorFn: (r) => clock(r.exec_ts) },
  { header: "Qty (sh)", accessorFn: (r) => r.quantity, cell: (c) => num(c.getValue()), meta: { num: true } },
  { header: "Price", accessorFn: (r) => r.price, cell: (c) => px(c.getValue()), meta: { num: true } },
  { header: "Liq.", accessorKey: "liquidity" },
  { header: "State", accessorKey: "market_state" },
  { header: "Fees ($)", accessorFn: (r) => r.fees_usd, cell: (c) => num(c.getValue(), 2), meta: { num: true } },
  { header: "Comm. ($)", accessorFn: (r) => r.commission_usd, cell: (c) => num(c.getValue(), 2), meta: { num: true } },
  { header: "Mid at exec", accessorFn: (r) => r.ref_snapshot?.mid, cell: (c) => px(c.getValue()), meta: { num: true } },
  { header: "Model", accessorKey: "fill_model" },
];
