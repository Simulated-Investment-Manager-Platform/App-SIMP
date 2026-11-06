"use client";

import type { EChartsOption } from "echarts";
import { parseAsString, useQueryState } from "nuqs";
import * as React from "react";

import { LineChart } from "@/components/charts";
import { axisStyle, EChart } from "@/components/charts/EChart";
import { type Col, DataTable } from "@/components/DataTable";
import { Badge, Card, CardHeader, DataLabel, Empty, ErrorBox, Input, KV, Skeleton, Stat } from "@/components/ui";
import { CATEGORICAL, INK, type Theme } from "@/lib/colors";
import { getJson } from "@/lib/api/client";
import { useJson } from "@/lib/hooks";
import { bps, num, pct, px } from "@/lib/format";

export default function MarketPage() {
  const [permno, setPermno] = useQueryState("permno", parseAsString.withDefault(""));
  const [date, setDate] = useQueryState("date", parseAsString.withDefault(""));
  // no ?permno=: open on the largest index member of the current snapshot (PERMNOs differ by data source)
  const def = useJson<{ permno: number; ticker: string }>(["security-default"], permno ? null : "/securities/default");
  const shown = permno || (def.data ? String(def.data.permno) : "");
  const [draft, setDraft] = React.useState(shown);
  const [lookupError, setLookupError] = React.useState<string | null>(null);
  React.useEffect(() => setDraft(shown), [shown]);
  const q = useJson(["market", shown, date], shown ? `/securities/${shown}/market` : null, { date: date || undefined });
  const d = q.data;
  async function go(value: string) {
    const v = value.trim();
    setLookupError(null);
    if (!v || v === shown) return;
    if (/^\d+$/.test(v)) return void setPermno(v);
    const r = await getJson<{ securities: { permno: number; ticker: string }[] }>("/search", { q: v });
    const hit = r.securities.find((x) => x.ticker.toUpperCase() === v.toUpperCase()) ?? r.securities[0];
    if (hit) setPermno(String(hit.permno));
    else setLookupError(`no security matches "${v}"`);
  }
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Market explorer</h1>
          <p className="text-sm text-ink-2">Derived BMLL daily figures per security and date, venue shares and the PERMNO ↔ ListingId mapping history. Raw rows stay in the lake.</p>
        </div>
        <div className="flex items-end gap-2">
          <label className="flex flex-col gap-1"><span className="text-[10px] uppercase text-ink-3">PERMNO or ticker</span><Input value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => e.key === "Enter" && go(draft)} onBlur={() => go(draft)} className="h-8 w-28" /></label>
          <label className="flex flex-col gap-1"><span className="text-[10px] uppercase text-ink-3">Date</span><Input type="date" value={date} onChange={(e) => setDate(e.target.value)} className="h-8 w-40" /></label>
        </div>
      </div>
      {(q.isLoading || def.isLoading) && <Skeleton className="h-96" />}
      {(q.error || def.error || lookupError) && <ErrorBox error={q.error ?? def.error ?? lookupError} title="Security not available" />}
      {d && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-semibold">{d.ticker}</h2>
            <span className="text-sm text-ink-3">PERMNO {d.permno} · primary {d.primary_venue} · ListingId {d.listing_id ?? "—"} · {d.date}</span>
            {d.mapped ? <Badge tone="good">mapped</Badge> : <Badge tone="critical">unmapped — in review queue</Badge>}
            <DataLabel label={d.data_label} />
          </div>
          {d.today && (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
              <Stat label="Close auction" value={<span className="num">{px(d.today.auction_price)}</span>} sub={d.today.auction_price_fallback ? "AUCTION_FALLBACK_CRSP" : `close ${px(d.today.close)}`} />
              <Stat label="Auction volume" value={<span className="num">{num(d.today.auction_volume)}</span>} sub={d.today.auction_volume_fallback ? "AUCVOL_FALLBACK_MEDIAN" : "shares, primary venue"} />
              <Stat label="Imbalance" value={<span className="num">{num(d.today.imbalance_qty)}</span>} sub="signed shares at the close" />
              <Stat label="ADV20 / ADV60" value={<span className="num">{num((d.today.adv20 ?? 0) / 1e6, 2)}m</span>} sub={`${num((d.today.adv60 ?? 0) / 1e6, 2)}m shares`} />
              <Stat label="Spread" value={<span className="num">{num(d.today.spread_bps, 2)} bps</span>} sub="Spread|RelTWA" />
              <Stat label="VWAP / TWAP" value={<span className="num">{px(d.today.vwap)}</span>} sub={px(d.today.twap)} />
              <Stat label="Intraday vol" value={<span className="num">{pct(d.today.vol_intraday)}</span>} sub="daily" />
              <Stat label="Sweep $100k" value={<span className="num">{num(d.today.sweep_100k_bps, 1)} bps</span>} sub={`dislocation ${bps(d.today.dislocation_bps)} bps`} />
            </div>
          )}
          <div className="grid gap-4 xl:grid-cols-2">
            <Card>
              <CardHeader title="Close vs day VWAP" subtitle="Daily, around the selected date" />
              <div className="p-2"><TwoLines series={d.series} /></div>
            </Card>
            <Card>
              <CardHeader title="Closing auction volume" subtitle="Shares at the primary venue (index-event dates spike)" />
              <div className="p-2"><LineChart data={d.series.map((s: any) => [s.date, s.auction_volume / 1e6])} name="Auction volume" unit="m sh" /></div>
            </Card>
            <Card>
              <CardHeader title="Intraday mid, selected date" subtitle="Minute path (synthetic source) · 09:30–16:00 ET" />
              <div className="p-2">{d.intraday ? <LineChart data={d.intraday.map(([m, p]: [number, number]) => [new Date(Date.UTC(2000, 0, 1, 14, 30 + m)).toISOString(), p])} name="Mid" unit="$" /> : <Empty title="No intraday data" detail="Live mode needs Trades Plus @SIP partitions." />}</div>
            </Card>
            <Card>
              <CardHeader title="Execution-venue share" subtitle="From classified trades by ExecutionVenue (continuous + dark)" />
              <div className="p-2"><VenueBars rows={d.venue_share} /></div>
            </Card>
          </div>
          <div className="grid gap-4 xl:grid-cols-3">
            <Card>
              <CardHeader title="PERMNO ↔ BMLL mapping history" />
              <DataTable data={d.mapping_history} height="auto" className="rounded-none border-x-0 border-b-0" columns={mapCols} rowKey={(r: any) => r.valid_from} empty="No mapping: see Data → mapping review queue" />
            </Card>
            <Card>
              <CardHeader title="Corporate actions" />
              <DataTable data={d.corporate_actions} height={260} className="rounded-none border-x-0 border-b-0" columns={caCols} rowKey={(r: any, i) => `${r.kind}${r.ex}${i}`} />
            </Card>
            <Card className="p-4">
              <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-2">S&P 500 membership</h4>
              <KV items={d.membership.map((m: any, i: number) => [`interval ${i + 1}`, `${m.start} → ${String(m.end).startsWith("9999") ? "present" : m.end}`])} />
            </Card>
          </div>
        </>
      )}
    </div>
  );
}

function TwoLines({ series }: { series: any[] }) {
  const option = React.useCallback(
    (t: Theme): EChartsOption => ({
      legend: { data: ["Close", "Day VWAP"] },
      tooltip: { trigger: "axis", valueFormatter: (v) => px(v) },
      xAxis: { type: "time", ...axisStyle(t), splitLine: { show: false } },
      yAxis: { type: "value", scale: true, ...axisStyle(t) },
      series: [
        { name: "Close", type: "line", showSymbol: false, lineStyle: { width: 2 }, color: CATEGORICAL[t][0], data: series.map((s) => [s.date, s.close]) },
        { name: "Day VWAP", type: "line", showSymbol: false, lineStyle: { width: 2 }, color: CATEGORICAL[t][1], data: series.map((s) => [s.date, s.vwap]) },
      ],
    }),
    [series],
  );
  return <EChart option={option} height={220} ariaLabel="Daily close and VWAP" />;
}

function VenueBars({ rows }: { rows: { venue: string; share: number }[] }) {
  const sorted = [...rows].sort((a, b) => b.share - a.share);
  const option = React.useCallback(
    (t: Theme): EChartsOption => ({
      grid: { left: 64, right: 48, top: 8, bottom: 24 },
      tooltip: { trigger: "item", formatter: (p: any) => `${p.name}: ${pct(p.value, 1)}` },
      yAxis: { type: "category", inverse: true, data: sorted.map((r) => r.venue), ...axisStyle(t), splitLine: { show: false } },
      xAxis: { type: "value", ...axisStyle(t), axisLabel: { ...axisStyle(t).axisLabel, formatter: (v: number) => `${(v * 100).toFixed(0)}%` } },
      series: [{ type: "bar", barMaxWidth: 16, itemStyle: { borderRadius: 4, color: CATEGORICAL[t][0] }, label: { show: true, position: "right", color: INK[t].secondary, formatter: (p: any) => pct(p.value, 1) }, data: sorted.map((r) => r.share) }],
    }),
    [sorted],
  );
  return <EChart option={option} height={240} ariaLabel="Share of continuous volume by execution venue" />;
}

const mapCols: Col<any>[] = [
  { header: "From", accessorKey: "valid_from" },
  { header: "To", accessorKey: "valid_to", cell: (c) => (String(c.getValue()).startsWith("9999") ? "open" : c.getValue()) },
  { header: "ListingId", accessorKey: "listing_id" },
  { header: "@SIP", accessorKey: "sip_listing_id" },
  { header: "ISIN", accessorKey: "isin" },
  { header: "Ticker", accessorKey: "ticker" },
  { header: "OPOL", accessorKey: "opol" },
  { header: "Method", accessorKey: "method" },
];
const caCols: Col<any>[] = [
  { header: "Type", accessorKey: "kind" },
  { header: "Ex", accessorKey: "ex" },
  { header: "Pay", accessorKey: "pay", cell: (c) => c.getValue() ?? "missing" },
  { header: "Declared", accessorKey: "declared" },
  { header: "Amount", accessorKey: "amount", cell: (c) => px(c.getValue()), meta: { num: true } },
  { header: "Factor", accessorKey: "factor", meta: { num: true } },
];

