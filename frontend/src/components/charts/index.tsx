"use client";

import type { EChartsOption } from "echarts";
import * as React from "react";

import { axisStyle, EChart } from "@/components/charts/EChart";
import { BUY, CATEGORICAL, costColor, divergingStops, INK, SELL, type Theme } from "@/lib/colors";
import { bps, usd } from "@/lib/format";

const tipBps = (v: number) => `${bps(v)} bps`;

/* ---------------------------------------------------------------- NAV vs benchmark (indexed) */
export function NavChart({ data }: { data: { date: string; fund_level: number; bench_level: number }[] }) {
  const option = React.useCallback(
    (t: Theme): EChartsOption => ({
      legend: { data: ["Fund (unit NAV, net)", "Benchmark (TR)"] },
      tooltip: { trigger: "axis", valueFormatter: (v) => (typeof v === "number" ? ((v - 1) * 100).toFixed(2) + "%" : String(v)) },
      xAxis: { type: "time", ...axisStyle(t), splitLine: { show: false } },
      yAxis: { type: "value", scale: true, ...axisStyle(t), axisLabel: { ...axisStyle(t).axisLabel, formatter: (v: number) => `${((v - 1) * 100).toFixed(0)}%` } },
      series: [
        { name: "Fund (unit NAV, net)", type: "line", showSymbol: false, lineStyle: { width: 2 }, color: CATEGORICAL[t][0], data: data.map((d) => [d.date, d.fund_level]) },
        { name: "Benchmark (TR)", type: "line", showSymbol: false, lineStyle: { width: 2 }, color: CATEGORICAL[t][1], data: data.map((d) => [d.date, d.bench_level]) },
      ],
    }),
    [data],
  );
  return <EChart option={option} height={300} ariaLabel="Fund unit NAV versus benchmark, indexed to the window start" />;
}

/* ---------------------------------------------------------------- Active return (daily bars) + rolling TE line are separate charts */
export function ActiveChart({ data }: { data: { date: string; active: number }[] }) {
  const option = React.useCallback(
    (t: Theme): EChartsOption => ({
      tooltip: { trigger: "axis", valueFormatter: (v) => tipBps(Number(v)) },
      xAxis: { type: "time", ...axisStyle(t), splitLine: { show: false } },
      yAxis: { type: "value", name: "bps", ...axisStyle(t) },
      series: [
        {
          name: "Daily active return",
          type: "bar",
          barMaxWidth: 3,
          data: data.map((d) => ({ value: [d.date, d.active * 1e4], itemStyle: { color: d.active >= 0 ? CATEGORICAL[t][0] : CATEGORICAL[t][1] } })),
        },
      ],
    }),
    [data],
  );
  return <EChart option={option} height={200} ariaLabel="Daily active return of the fund over the benchmark, in basis points" />;
}

export function LineChart({ data, name, unit, color = 0, height = 200 }: { data: [string, number][]; name: string; unit: string; color?: number; height?: number }) {
  const option = React.useCallback(
    (t: Theme): EChartsOption => ({
      tooltip: { trigger: "axis", valueFormatter: (v) => `${Number(v).toFixed(2)} ${unit}` },
      xAxis: { type: "time", ...axisStyle(t), splitLine: { show: false } },
      yAxis: { type: "value", name: unit, scale: true, ...axisStyle(t) },
      series: [{ name, type: "line", showSymbol: false, lineStyle: { width: 2 }, color: CATEGORICAL[t][color], data }],
    }),
    [data, name, unit, color],
  );
  return <EChart option={option} height={height} ariaLabel={name} />;
}

/* ---------------------------------------------------------------- TD waterfall */
export function Waterfall({ steps }: { steps: { label: string; bps: number; total?: boolean }[] }) {
  const option = React.useCallback(
    (t: Theme): EChartsOption => {
      let run = 0;
      const base: number[] = [];
      const val: { value: number; itemStyle: { color: string } }[] = [];
      for (const s of steps) {
        if (s.total) {
          base.push(Math.min(0, s.bps));
          val.push({ value: Math.abs(s.bps), itemStyle: { color: INK[t].secondary } });
        } else {
          const lo = Math.min(run, run + s.bps);
          base.push(lo);
          val.push({ value: Math.abs(s.bps), itemStyle: { color: s.bps >= 0 ? CATEGORICAL[t][0] : CATEGORICAL[t][1] } });
          run += s.bps;
        }
      }
      return {
        grid: { left: 56, right: 16, top: 16, bottom: 64 },
        tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, formatter: (p: any) => `${p[1].name}: ${tipBps(steps[p[1].dataIndex].bps)}` },
        xAxis: { type: "category", data: steps.map((s) => s.label), ...axisStyle(t), axisLabel: { ...axisStyle(t).axisLabel, interval: 0, width: 90, overflow: "break" } },
        yAxis: { type: "value", name: "bps / yr", ...axisStyle(t) },
        series: [
          { type: "bar", stack: "w", itemStyle: { color: "transparent" }, data: base, silent: true },
          { type: "bar", stack: "w", data: val, barMaxWidth: 36, itemStyle: { borderRadius: 4 } },
        ],
      };
    },
    [steps],
  );
  return <EChart option={option} height={260} ariaLabel="Tracking difference decomposition waterfall in basis points per year" />;
}

/* ---------------------------------------------------------------- Flows */
export function FlowsChart({ flows }: { flows: { date: string; amount_usd: number; is_jump: boolean }[] }) {
  const option = React.useCallback(
    (t: Theme): EChartsOption => ({
      legend: { data: ["Subscriptions", "Redemptions"] },
      tooltip: { trigger: "axis", valueFormatter: (v) => usd(Number(v)) },
      xAxis: { type: "time", ...axisStyle(t), splitLine: { show: false } },
      yAxis: { type: "value", ...axisStyle(t), axisLabel: { ...axisStyle(t).axisLabel, formatter: (v: number) => usd(v, 0) } },
      series: [
        { name: "Subscriptions", type: "bar", barMaxWidth: 3, color: CATEGORICAL[t][0], data: flows.filter((f) => f.amount_usd >= 0).map((f) => [f.date, f.amount_usd]) },
        { name: "Redemptions", type: "bar", barMaxWidth: 3, color: CATEGORICAL[t][1], data: flows.filter((f) => f.amount_usd < 0).map((f) => [f.date, f.amount_usd]) },
      ],
    }),
    [flows],
  );
  return <EChart option={option} height={240} ariaLabel="Daily investor subscriptions and redemptions in US dollars" />;
}

export function CashChart({ cash, bufferBps }: { cash: { date: string; cash_weight: number }[]; bufferBps: number }) {
  const option = React.useCallback(
    (t: Theme): EChartsOption => ({
      legend: { data: ["Cash weight", "Buffer target"] },
      tooltip: { trigger: "axis", valueFormatter: (v) => `${Number(v).toFixed(1)} bps` },
      xAxis: { type: "time", ...axisStyle(t), splitLine: { show: false } },
      yAxis: { type: "value", name: "bps of NAV", ...axisStyle(t) },
      series: [
        { name: "Cash weight", type: "line", showSymbol: false, lineStyle: { width: 2 }, color: CATEGORICAL[t][0], data: cash.map((c) => [c.date, c.cash_weight * 1e4]) },
        { name: "Buffer target", type: "line", showSymbol: false, lineStyle: { width: 2, type: "dashed" }, color: CATEGORICAL[t][1], data: cash.map((c) => [c.date, bufferBps]) },
      ],
    }),
    [cash, bufferBps],
  );
  return <EChart option={option} height={220} ariaLabel="Cash as basis points of NAV versus the buffer target" />;
}

/* ---------------------------------------------------------------- Bucket bars with 90% CI */
export function BucketBars({ groups, label }: { groups: { key: Record<string, string | null>; cost_bps: number | null; ci90: (number | null)[]; notional: number; orders: number }[]; label: string }) {
  const option = React.useCallback(
    (t: Theme): EChartsOption => {
      const cats = groups.map((g) => Object.values(g.key).join(" · ") || "all");
      return {
        grid: { left: 56, right: 16, top: 16, bottom: 40 },
        tooltip: {
          trigger: "item",
          formatter: (p: any) => {
            const g = groups[p.dataIndex];
            return `<b>${cats[p.dataIndex]}</b><br/>${tipBps(g.cost_bps ?? NaN)} (90% CI ${bps(g.ci90[0])} to ${bps(g.ci90[1])})<br/>${usd(g.notional)} · ${g.orders} orders`;
          },
        },
        xAxis: { type: "category", data: cats, ...axisStyle(t), name: label, nameLocation: "middle", nameGap: 26 },
        yAxis: { type: "value", name: "bps", ...axisStyle(t) },
        series: [
          { type: "bar", barMaxWidth: 28, itemStyle: { borderRadius: [4, 4, 0, 0] }, data: groups.map((g) => ({ value: g.cost_bps, itemStyle: { color: costColor(g.cost_bps, 30, t) === "transparent" ? INK[t].muted : CATEGORICAL[t][0] } })) },
          {
            type: "custom",
            silent: true,
            renderItem: (_: any, api: any) => {
              const i = api.value(0);
              const lo = api.coord([i, api.value(1)]);
              const hi = api.coord([i, api.value(2)]);
              const w = 6;
              const s = { stroke: INK[t].primary, lineWidth: 1.5 };
              return { type: "group", children: [
                { type: "line", shape: { x1: lo[0], y1: lo[1], x2: hi[0], y2: hi[1] }, style: s },
                { type: "line", shape: { x1: lo[0] - w, y1: lo[1], x2: lo[0] + w, y2: lo[1] }, style: s },
                { type: "line", shape: { x1: hi[0] - w, y1: hi[1], x2: hi[0] + w, y2: hi[1] }, style: s },
              ] };
            },
            data: groups.map((g, i) => [i, g.ci90[0] ?? 0, g.ci90[1] ?? 0]),
            z: 10,
          } as any,
        ],
      };
    },
    [groups, label],
  );
  return <EChart option={option} height={260} ariaLabel={`Notional-weighted cost by ${label} with 90% confidence intervals`} />;
}

/* ---------------------------------------------------------------- Heatmap */
export function Heatmap({ data }: { data: { x: string[]; y: string[]; cells: { size_bucket: string; auction_bucket: string; cost_bps: number | null; orders: number; notional: number }[] } }) {
  const option = React.useCallback(
    (t: Theme): EChartsOption => {
      const vals = data.cells.map((c) => c.cost_bps).filter((v): v is number => v !== null && Number.isFinite(v));
      const m = Math.max(5, ...vals.map((v) => Math.abs(v)));
      return {
        grid: { left: 80, right: 80, top: 16, bottom: 48 },
        tooltip: {
          formatter: (p: any) => {
            const c = data.cells.find((x) => x.auction_bucket === data.x[p.value[0]] && x.size_bucket === data.y[p.value[1]]);
            return c ? `%ADV ${c.size_bucket} · %auction ${c.auction_bucket}<br/>${tipBps(c.cost_bps ?? NaN)} · ${usd(c.notional)} · ${c.orders} orders` : "";
          },
        },
        xAxis: { type: "category", data: data.x, name: "Q / auction volume", nameLocation: "middle", nameGap: 30, ...axisStyle(t), splitLine: { show: false } },
        yAxis: { type: "category", data: data.y, name: "Q / ADV20", ...axisStyle(t), splitLine: { show: false } },
        visualMap: { min: -m, max: m, calculable: false, orient: "vertical", right: 0, top: "middle", itemHeight: 140, inRange: { color: divergingStops(t) }, textStyle: { color: INK[t].muted }, formatter: (v: any) => `${Number(v).toFixed(0)}` },
        series: [
          {
            type: "heatmap",
            data: data.cells.filter((c) => c.cost_bps !== null).map((c) => [data.x.indexOf(c.auction_bucket), data.y.indexOf(c.size_bucket), c.cost_bps]),
            label: { show: true, color: INK[t].primary, fontSize: 10, formatter: (p: any) => bps(p.value[2], 0) },
            itemStyle: { borderColor: INK[t].surface, borderWidth: 2, borderRadius: 4 },
          },
        ],
      };
    },
    [data],
  );
  return <EChart option={option} height={300} ariaLabel="Heatmap of notional-weighted cost by percent of ADV and percent of auction volume" />;
}

/* ---------------------------------------------------------------- Scatter: cost vs Q/auction, sized by notional */
export function CostScatter({ points, xLabel = "Q / auction volume", onPick }: { points: { x: number; y: number; notional: number; ticker: string; side: string; pm_order_id: string }[]; xLabel?: string; onPick?: (id: string) => void }) {
  const option = React.useCallback(
    (t: Theme): EChartsOption => {
      const maxN = Math.max(1, ...points.map((p) => p.notional));
      const mk = (side: string) =>
        points.filter((p) => p.side === side && p.x > 0).map((p) => ({ value: [p.x * 100, p.y], name: p.ticker, id: p.pm_order_id, notional: p.notional, symbolSize: 6 + 22 * Math.sqrt(p.notional / maxN) }));
      return {
        legend: { data: ["Buy", "Sell"] },
        tooltip: { trigger: "item", formatter: (p: any) => `<b>${p.data.name}</b> ${p.seriesName}<br/>${xLabel} ${p.value[0].toFixed(3)}%<br/>${tipBps(p.value[1])} · ${usd(p.data.notional)}` },
        xAxis: { type: "log", name: `${xLabel} (%)`, nameLocation: "middle", nameGap: 26, ...axisStyle(t) },
        yAxis: { type: "value", name: "bps", ...axisStyle(t) },
        series: [
          { name: "Buy", type: "scatter", color: BUY(t), itemStyle: { opacity: 0.75, borderColor: INK[t].surface, borderWidth: 1 }, symbolSize: (_: any, p: any) => p.data.symbolSize, data: mk("BUY") },
          { name: "Sell", type: "scatter", color: SELL(t), itemStyle: { opacity: 0.75, borderColor: INK[t].surface, borderWidth: 1 }, symbolSize: (_: any, p: any) => p.data.symbolSize, data: mk("SELL") },
        ],
      };
    },
    [points, xLabel],
  );
  const click = React.useCallback((p: any) => onPick?.(p.data?.id), [onPick]);
  return <EChart option={option} height={320} onClick={onPick ? click : undefined} ariaLabel={`Cost in bps against ${xLabel}, markers sized by notional`} />;
}

/* ---------------------------------------------------------------- Method comparison bars */
export function MethodBars({ methods, field = "cost_bps" }: { methods: { method: string; cost_bps?: number | null; ci90?: (number | null)[]; cost_usd?: number }[]; field?: string }) {
  const option = React.useCallback(
    (t: Theme): EChartsOption => ({
      grid: { left: 80, right: 32, top: 8, bottom: 28 },
      tooltip: { trigger: "item", formatter: (p: any) => `${p.name}: ${tipBps(p.value)}<br/>${usd(methods[p.dataIndex].cost_usd)}` },
      yAxis: { type: "category", data: methods.map((m) => m.method), inverse: true, ...axisStyle(t), splitLine: { show: false } },
      xAxis: { type: "value", name: "bps", ...axisStyle(t) },
      series: [{ type: "bar", barMaxWidth: 18, itemStyle: { borderRadius: 4, color: CATEGORICAL[t][0] }, label: { show: true, position: "right", color: INK[t].secondary, formatter: (p: any) => bps(p.value) }, data: methods.map((m) => (m as any)[field] ?? null) }],
    }),
    [methods, field],
  );
  return <EChart option={option} height={Math.max(160, methods.length * 34)} ariaLabel="Notional-weighted cost by execution method" />;
}

/* ---------------------------------------------------------------- Concentration curve */
export function ConcentrationChart({ dates }: { dates: { date: string; curve: [number, number][]; top10_share: number }[] }) {
  const option = React.useCallback(
    (t: Theme): EChartsOption => ({
      legend: { show: false },
      tooltip: { trigger: "item", formatter: (p: any) => `${p.seriesName}: top ${(p.value[0] * 100).toFixed(0)}% of orders = ${(p.value[1] * 100).toFixed(1)}% of notional` },
      xAxis: { type: "value", min: 0, max: 1, name: "share of orders (largest first)", nameLocation: "middle", nameGap: 26, ...axisStyle(t), axisLabel: { ...axisStyle(t).axisLabel, formatter: (v: number) => `${v * 100}%` } },
      yAxis: { type: "value", min: 0, max: 1, ...axisStyle(t), axisLabel: { ...axisStyle(t).axisLabel, formatter: (v: number) => `${v * 100}%` } },
      series: [
        ...dates.map((d) => ({ name: d.date, type: "line" as const, showSymbol: false, lineStyle: { width: 1, opacity: 0.55 }, color: CATEGORICAL[t][0], data: [[0, 0], ...d.curve] })),
        { name: "85% in top 10% (handoff)", type: "scatter", symbolSize: 10, color: CATEGORICAL[t][1], data: [[0.1, 0.85]] },
      ],
    }),
    [dates],
  );
  return <EChart option={option} height={280} ariaLabel="Cumulative notional share by share of rebalance orders for each rebalance date" />;
}

/* ---------------------------------------------------------------- Intraday lineage chart */
export function IntradayChart({ chart, fills, decision, arrival }: { chart: { minutes: [number, number][]; vwap: number; close: number }; fills: { exec_ts: string; price: number; quantity: number; side: string }[]; decision?: number | null; arrival?: number | null }) {
  const option = React.useCallback(
    (t: Theme): EChartsOption => {
      const toMin = (ts: string) => {
        const d = new Date(ts);
        const ny = new Date(d.toLocaleString("en-US", { timeZone: "America/New_York" }));
        return ny.getHours() * 60 + ny.getMinutes() + ny.getSeconds() / 60 - 570;
      };
      const label = (m: number) => {
        const tot = 570 + m;
        return `${String(Math.floor(tot / 60)).padStart(2, "0")}:${String(Math.floor(tot % 60)).padStart(2, "0")}`;
      };
      const ref = (name: string, v: number | null | undefined, k: number) =>
        v ? { name, type: "line" as const, showSymbol: false, color: CATEGORICAL[t][k], lineStyle: { width: 1.5, type: "dashed" as const }, data: [[0, v], [390, v]] } : null;
      const maxQ = Math.max(1, ...fills.map((f) => f.quantity));
      return {
        legend: { top: 0 },
        tooltip: { trigger: "item", formatter: (p: any) => `${p.seriesName} ${label(p.value[0])}<br/>${Number(p.value[1]).toFixed(4)}${p.value[2] ? ` · ${p.value[2].toLocaleString()} sh` : ""}` },
        xAxis: { type: "value", min: 0, max: 390, ...axisStyle(t), splitLine: { show: false }, axisLabel: { ...axisStyle(t).axisLabel, formatter: (v: number) => label(v) } },
        yAxis: { type: "value", scale: true, ...axisStyle(t) },
        series: [
          { name: "Mid", type: "line", showSymbol: false, lineStyle: { width: 2 }, color: INK[t].muted, data: chart.minutes },
          ...[ref("Decision", decision, 6), ref("Arrival", arrival, 2), ref("VWAP", chart.vwap, 3), ref("Close", chart.close, 4)].filter(Boolean) as any[],
          { name: "Fills", type: "scatter", color: fills[0]?.side === "SELL" ? SELL(t) : BUY(t), itemStyle: { borderColor: INK[t].surface, borderWidth: 2 }, symbolSize: (v: any) => 8 + 12 * Math.sqrt(v[2] / maxQ), data: fills.map((f) => [Math.min(390, Math.max(0, toMin(f.exec_ts))), f.price, f.quantity]) },
        ],
      };
    },
    [chart, fills, decision, arrival],
  );
  return <EChart option={option} height={260} ariaLabel="Intraday mid price with decision, arrival, VWAP, close and fills" />;
}

/* ---------------------------------------------------------------- Sparkline (SVG, for tiles) */
export function Sparkline({ values, className }: { values: number[]; className?: string }) {
  if (values.length < 2) return <div className={className} />;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * 100},${30 - ((v - lo) / (hi - lo || 1)) * 28 - 1}`).join(" ");
  return (
    <svg viewBox="0 0 100 30" preserveAspectRatio="none" className={className} aria-hidden>
      <polyline points={pts} fill="none" stroke="rgb(var(--accent))" strokeWidth={2} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
