"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Download } from "lucide-react";
import { parseAsInteger, parseAsString, useQueryStates } from "nuqs";
import * as React from "react";

import { LineageDrawer } from "@/components/Lineage";
import { Button, Card, CostCell, Empty, ErrorBox, Input, Select, Side, Skeleton, Status } from "@/components/ui";
import { getJson } from "@/lib/api/client";
import { day, num, pct, px, usd } from "@/lib/format";
import { exportTable } from "@/lib/exports";

const REASONS = ["", "INITIAL", "INDEX_REBALANCE", "INDEX_ADD", "INDEX_DELETE", "SUBSCRIPTION", "REDEMPTION", "CASH_SWEEP", "CORP_ACTION", "SYNTHETIC_TEST"];
const STRATS = ["", "MOC", "PORTFOLIO_TRADE", "ALGO_VWAP", "ALGO_IS", "HIGH_TOUCH", "INTERNAL_CROSS"];
const BROKERS = ["", "BRK_A", "BRK_B", "BRK_C", "BRK_D", "BRK_E", "INTERNAL"];
const VENUES = ["", "XNYS", "XNAS", "ARCX", "BATS", "EDGX", "IEXG", "MEMX", "DARK", "INTERNAL"];
const BUCKETS = ["", "<=0.1%", "0.1-0.5%", "0.5-1%", "1-5%", "5-10%", ">10%"];
const PAGE = 100;

/** Three-level expandable blotter: PM → trader → child → fills. Filters live in the URL. */
export function Blotter({ runId }: { runId: string }) {
  const [f, setF] = useQueryStates({
    date: parseAsString.withDefault(""),
    reason: parseAsString.withDefault(""),
    strategy: parseAsString.withDefault(""),
    broker: parseAsString.withDefault(""),
    venue: parseAsString.withDefault(""),
    size: parseAsString.withDefault(""),
    side: parseAsString.withDefault(""),
    permno: parseAsString.withDefault(""),
    sort: parseAsString.withDefault("exec_notional"),
    page: parseAsInteger.withDefault(0),
    order: parseAsString,
  });
  const params = { level: "pm", date: f.date || undefined, reason: f.reason || undefined, strategy: f.strategy || undefined, broker: f.broker || undefined, venue: f.venue || undefined, size_bucket: f.size || undefined, side: f.side || undefined, permno: f.permno || undefined, sort: f.sort, limit: PAGE, offset: f.page * PAGE };
  const q = useQuery({ queryKey: ["orders", runId, params], queryFn: () => getJson(`/runs/${runId}/orders`, params), placeholderData: (p) => p });
  const [open, setOpen] = React.useState<Record<string, boolean>>({});
  const set = (k: string, v: string) => setF({ [k]: v || null, page: 0 } as any);
  const total = q.data?.total ?? 0;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-2">
        <Filter label="Date">
          <Input type="date" value={f.date} onChange={(e) => set("date", e.target.value)} className="h-8 w-40" />
        </Filter>
        <Filter label="Reason">
          <Select value={f.reason} onChange={(e) => set("reason", e.target.value)}>{REASONS.map((r) => <option key={r} value={r}>{r || "All"}</option>)}</Select>
        </Filter>
        <Filter label="Strategy">
          <Select value={f.strategy} onChange={(e) => set("strategy", e.target.value)}>{STRATS.map((r) => <option key={r} value={r}>{r || "All"}</option>)}</Select>
        </Filter>
        <Filter label="Broker">
          <Select value={f.broker} onChange={(e) => set("broker", e.target.value)}>{BROKERS.map((r) => <option key={r} value={r}>{r || "All"}</option>)}</Select>
        </Filter>
        <Filter label="Venue">
          <Select value={f.venue} onChange={(e) => set("venue", e.target.value)}>{VENUES.map((r) => <option key={r} value={r}>{r || "All"}</option>)}</Select>
        </Filter>
        <Filter label="Size (Q/ADV)">
          <Select value={f.size} onChange={(e) => set("size", e.target.value)}>{BUCKETS.map((r) => <option key={r} value={r}>{r || "All"}</option>)}</Select>
        </Filter>
        <Filter label="Side">
          <Select value={f.side} onChange={(e) => set("side", e.target.value)}>{["", "BUY", "SELL"].map((r) => <option key={r} value={r}>{r || "Both"}</option>)}</Select>
        </Filter>
        <Filter label="PERMNO">
          <Input value={f.permno} onChange={(e) => set("permno", e.target.value)} className="h-8 w-24" placeholder="any" />
        </Filter>
        <Filter label="Sort">
          <Select value={f.sort} onChange={(e) => set("sort", e.target.value)}>
            {[["exec_notional", "Notional"], ["exec_cost_bps", "Exec cost"], ["is_bps", "IS"], ["pct_adv", "%ADV"], ["pct_auction", "%auction"], ["date", "Date"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </Select>
        </Filter>
        <div className="ml-auto flex gap-2">
          <Button size="sm" onClick={() => exportTable(runId, "tca_order")}><Download className="h-3.5 w-3.5" /> TCA CSV</Button>
          <Button size="sm" onClick={() => exportTable(runId, "fill")}><Download className="h-3.5 w-3.5" /> Fills CSV</Button>
        </div>
      </div>

      {q.error && <ErrorBox error={q.error} />}
      <Card className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-surface">
            <tr className="border-b border-line text-[11px] uppercase tracking-wide text-ink-3">
              {["", "Date", "Ticker", "Side", "Reason", "Qty (sh)", "Filled", "Notional ($)", "%ADV", "%auction", "Strategy", "Exec cost (bps)", "IS (bps)", "vs bench (bps)", "Status"].map((h, i) => (
                <th key={h + i} className={`px-2.5 py-2 font-medium ${i >= 5 && i <= 9 ? "text-right" : i >= 11 && i <= 13 ? "text-right" : "text-left"}`}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {q.isLoading && (
              <tr><td colSpan={15} className="p-3"><Skeleton className="h-64" /></td></tr>
            )}
            {!q.isLoading && (q.data?.rows ?? []).length === 0 && (
              <tr><td colSpan={15}><Empty title="No orders match these filters" detail="Clear a filter or pick another run." /></td></tr>
            )}
            {(q.data?.rows ?? []).map((r: any) => (
              <React.Fragment key={r.pm_order_id}>
                <tr className="cursor-pointer border-b border-line/70 hover:bg-line/30" data-testid="pm-row" onClick={() => setF({ order: r.pm_order_id })}>
                  <td className="px-2.5 py-1.5">
                    <button
                      aria-label="Expand trader orders"
                      className="rounded p-0.5 hover:bg-line"
                      onClick={(e) => {
                        e.stopPropagation();
                        setOpen((o) => ({ ...o, [r.pm_order_id]: !o[r.pm_order_id] }));
                      }}
                    >
                      {open[r.pm_order_id] ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                    </button>
                  </td>
                  <td className="num px-2.5 py-1.5">{day(r.date)}</td>
                  <td className="px-2.5 py-1.5 font-medium">{r.ticker ?? r.permno}</td>
                  <td className="px-2.5 py-1.5"><Side side={r.side} /></td>
                  <td className="px-2.5 py-1.5 text-ink-2">{r.reason}</td>
                  <td className="num px-2.5 py-1.5 text-right">{num(r.quantity)}</td>
                  <td className="num px-2.5 py-1.5 text-right">{num(r.filled_qty)}</td>
                  <td className="num px-2.5 py-1.5 text-right">{usd(r.exec_notional)}</td>
                  <td className="num px-2.5 py-1.5 text-right">{pct(r.pct_adv, 3)}</td>
                  <td className="num px-2.5 py-1.5 text-right">{pct(r.pct_auction, 2)}</td>
                  <td className="px-2.5 py-1.5">{r.main_strategy}{r.n_trader > 1 ? ` +${r.n_trader - 1}` : ""}</td>
                  <td className="px-2.5 py-1.5 text-right"><CostCell v={r.exec_cost_bps} /></td>
                  <td className="px-2.5 py-1.5 text-right"><CostCell v={r.is_bps} max={150} /></td>
                  <td className="px-2.5 py-1.5 text-right"><CostCell v={r.slip_benchmark_bps} /></td>
                  <td className="px-2.5 py-1.5"><Status value={r.status} /></td>
                </tr>
                {open[r.pm_order_id] && <TraderRows runId={runId} pmOrderId={r.pm_order_id} />}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      </Card>
      <div className="flex items-center justify-between text-xs text-ink-3">
        <span>
          {num(total)} PM orders · parent-level costs are notional-weighted over their fills · page {f.page + 1} of {Math.max(1, Math.ceil(total / PAGE))}
        </span>
        <div className="flex gap-2">
          <Button size="sm" disabled={f.page === 0} onClick={() => setF({ page: f.page - 1 })}>Previous</Button>
          <Button size="sm" disabled={(f.page + 1) * PAGE >= total} onClick={() => setF({ page: f.page + 1 })}>Next</Button>
        </div>
      </div>
      <LineageDrawer pmOrderId={f.order} onClose={() => setF({ order: null })} />
    </div>
  );
}

function TraderRows({ runId, pmOrderId }: { runId: string; pmOrderId: string }) {
  const q = useQuery({ queryKey: ["trader", pmOrderId], queryFn: () => getJson(`/runs/${runId}/orders`, { level: "trader", pm_order_id: pmOrderId, limit: 50 }) });
  const [open, setOpen] = React.useState<Record<string, boolean>>({});
  if (q.isLoading) return <tr><td colSpan={15} className="bg-raised px-8 py-2 text-ink-3">Loading trader orders…</td></tr>;
  return (
    <>
      {(q.data?.rows ?? []).map((t: any) => (
        <React.Fragment key={t.trader_order_id}>
          <tr className="border-b border-line/50 bg-raised/60" data-testid="trader-row">
            <td className="py-1.5 pl-6">
              <button aria-label="Expand child orders" className="rounded p-0.5 hover:bg-line" onClick={() => setOpen((o) => ({ ...o, [t.trader_order_id]: !o[t.trader_order_id] }))}>
                {open[t.trader_order_id] ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
              </button>
            </td>
            <td colSpan={4} className="px-2.5 py-1.5 text-ink-2">
              <span className="font-medium text-ink">{t.strategy}</span> via {t.broker_id} · {t.n_child} child · benchmark {t.benchmark} · arrival {px(t.arrival_price)}
            </td>
            <td className="num px-2.5 py-1.5 text-right">{num(t.quantity)}</td>
            <td className="num px-2.5 py-1.5 text-right">{num(t.filled_qty)}</td>
            <td className="num px-2.5 py-1.5 text-right">{usd(t.notional)}</td>
            <td colSpan={3} className="num px-2.5 py-1.5 text-right text-ink-3">avg {px(t.avg_price)}</td>
            <td className="px-2.5 py-1.5 text-right"><CostCell v={t.slip_arrival_bps} /></td>
            <td />
            <td className="px-2.5 py-1.5 text-right"><CostCell v={t.slip_benchmark_bps} /></td>
            <td className="px-2.5 py-1.5"><Status value={t.status} /></td>
          </tr>
          {open[t.trader_order_id] && <ChildRows runId={runId} traderOrderId={t.trader_order_id} />}
        </React.Fragment>
      ))}
    </>
  );
}

function ChildRows({ runId, traderOrderId }: { runId: string; traderOrderId: string }) {
  const c = useQuery({ queryKey: ["child", traderOrderId], queryFn: () => getJson(`/runs/${runId}/orders`, { level: "child", trader_order_id: traderOrderId, limit: 500 }) });
  const f = useQuery({ queryKey: ["fills", traderOrderId], queryFn: () => getJson(`/runs/${runId}/orders`, { level: "fill", trader_order_id: traderOrderId, limit: 2000 }) });
  if (c.isLoading || f.isLoading) return <tr><td colSpan={15} className="px-12 py-2 text-ink-3">Loading child orders and fills…</td></tr>;
  const fills = f.data?.rows ?? [];
  return (
    <>
      {(c.data?.rows ?? []).map((ch: any) => {
        const fs = fills.filter((x: any) => x.child_order_id === ch.child_order_id);
        return (
          <React.Fragment key={ch.child_order_id}>
            <tr className="border-b border-line/40 bg-raised/30 text-ink-2" data-testid="child-row">
              <td />
              <td colSpan={4} className="py-1 pl-10 pr-2.5">
                child {ch.child_order_id.slice(-8)} · {ch.venue_mic} {ch.order_type} {ch.tif}
              </td>
              <td className="num px-2.5 py-1 text-right">{num(ch.quantity)}</td>
              <td className="num px-2.5 py-1 text-right">{num(ch.filled_qty)}</td>
              <td colSpan={7} />
              <td className="px-2.5 py-1"><Status value={ch.status} /></td>
            </tr>
            {fs.map((x: any) => (
              <tr key={x.fill_id} className="border-b border-line/30 text-ink-3" data-testid="fill-row">
                <td />
                <td colSpan={4} className="py-1 pl-14 pr-2.5 font-mono text-[11px]">
                  fill {new Date(x.exec_ts).toLocaleTimeString("en-GB", { timeZone: "America/New_York" })} ET · {x.liquidity} · {x.market_state}
                </td>
                <td className="num px-2.5 py-1 text-right">{num(x.quantity)}</td>
                <td className="num px-2.5 py-1 text-right">{px(x.price)}</td>
                <td className="num px-2.5 py-1 text-right">{usd(x.price * x.quantity)}</td>
                <td colSpan={3} className="num px-2.5 py-1 text-right">fees ${num(x.fees_usd, 2)} · comm ${num(x.commission_usd, 2)}</td>
                <td colSpan={4} className="px-2.5 py-1">{x.fill_model}</td>
              </tr>
            ))}
          </React.Fragment>
        );
      })}
    </>
  );
}

function Filter({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] font-medium uppercase tracking-wide text-ink-3">{label}</span>
      {children}
    </label>
  );
}
