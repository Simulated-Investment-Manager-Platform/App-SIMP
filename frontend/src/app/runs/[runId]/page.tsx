"use client";

import { useQueryClient } from "@tanstack/react-query";
import { BarChart3, ListTree, LineChart, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { MultiStepLoader, type StepState } from "@/components/aceternity";
import { Badge, Button, Card, CardHeader, DataLabel, ErrorBox, KV, Skeleton, Stat, Status } from "@/components/ui";
import { sendJson } from "@/lib/api/client";
import { useRun } from "@/lib/hooks";
import { bps, dt, num, pct, usd } from "@/lib/format";

const STAGES = [
  ["data_check", "Data check", "Verify the pinned lake snapshot hashes"],
  ["mapping", "Mapping", "PERMNO → BMLL ListingId coverage"],
  ["simulate", "Simulate", "Session loop: decisions → desk → execution → accounting"],
  ["analyse", "Analyse", "Order-chain audit, TCA, fund report"],
] as const;

type Ev = { id: number; kind: string; data: any };

export default function RunDetail() {
  const { runId } = useParams<{ runId: string }>();
  const run = useRun(runId, true);
  const qc = useQueryClient();
  const router = useRouter();
  const [events, setEvents] = React.useState<Ev[]>([]);
  const [progress, setProgress] = React.useState<any>(null);

  React.useEffect(() => {
    const es = new EventSource(`/api/runs/${runId}/events`, { withCredentials: true });
    const on = (kind: string) => (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      if (kind === "progress") setProgress(data);
      else setEvents((x) => (x.some((y) => y.id === Number(e.lastEventId)) ? x : [...x, { id: Number(e.lastEventId), kind, data }]));
      if (kind === "done" || kind === "error") {
        es.close();
        qc.invalidateQueries({ queryKey: ["run", runId] });
        qc.invalidateQueries({ queryKey: ["runs"] });
      }
    };
    for (const k of ["stage", "progress", "log", "error", "done"]) es.addEventListener(k, on(k) as EventListener);
    es.onerror = () => es.close();
    return () => es.close();
  }, [runId, qc]);

  const r = run.data as any;
  if (run.isLoading) return <Skeleton className="h-96" />;
  if (run.error || !r) return <ErrorBox error={run.error ?? "run not found"} />;
  const stageIdx = STAGES.findIndex(([k]) => k === r.stage);
  const failed = r.status === "FAILED";
  const done = r.status === "DONE";
  const steps = STAGES.map(([k, label, detail], i) => {
    let state: StepState = "pending";
    if (done || i < stageIdx) state = "done";
    else if (i === stageIdx) state = failed ? "failed" : "active";
    else if (failed && stageIdx === -1 && i === 0) state = "failed";
    const extra = k === "simulate" && progress && !done ? ` · session ${progress.session} (${progress.i + 1}/${progress.n}) · NAV ${usd(progress.nav)} · ${num(progress.orders)} orders · ${num(progress.fills)} fills` : "";
    return { label, detail: detail + extra, state };
  });
  const s = r.summary ?? {};
  const f = s.fund ?? {};
  const err = r.error;
  const replay = async () => {
    const res = await sendJson("POST", "/runs", r.config);
    toast.success("Replay queued with the identical config and snapshot");
    router.push(`/runs/${res.run_id}`);
  };
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-semibold">{r.name}</h1>
            <Status value={r.status} />
            <DataLabel label={r.data_label} />
          </div>
          <div className="mt-1 font-mono text-xs text-ink-3">{r.run_id} · config {r.config_hash.slice(0, 16)} · code {r.code_version}</div>
        </div>
        <div className="flex flex-wrap gap-2">
          {done && (
            <>
              <Link href={`/funds/${r.fund_id}?run=${r.run_id}`}><Button><LineChart className="h-4 w-4" /> Fund</Button></Link>
              <Link href={`/tca?run=${r.run_id}`}><Button><BarChart3 className="h-4 w-4" /> TCA</Button></Link>
              <Link href={`/blotter?run=${r.run_id}`}><Button><ListTree className="h-4 w-4" /> Blotter</Button></Link>
            </>
          )}
          <Button onClick={replay}><RotateCcw className="h-4 w-4" /> Replay</Button>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
        <Card className="p-5">
          <h3 className="mb-4 text-xs font-semibold uppercase tracking-wide text-ink-2">Progress</h3>
          <MultiStepLoader steps={steps} />
          {!done && !failed && progress && (
            <div className="mt-4 h-1.5 overflow-hidden rounded bg-line">
              <div className="h-full bg-accent transition-[width]" style={{ width: `${((progress.i + 1) / progress.n) * 100}%` }} />
            </div>
          )}
        </Card>
        <div className="space-y-4">
          {failed && err && (
            <Card className="border-critical/50">
              <CardHeader title={<span className="text-critical">{err.type}</span>} subtitle={`Failing session: ${err.session ?? "—"} · ${err.expected ? "named engine error (hard stop)" : "unexpected exception"}`} />
              <div className="space-y-2 p-4">
                <div className="text-sm text-ink">{err.message}</div>
                <details>
                  <summary className="cursor-pointer text-xs text-ink-3">Traceback</summary>
                  <pre className="mt-2 max-h-72 overflow-auto rounded bg-raised p-3 font-mono text-[11px] text-ink-2">{err.traceback}</pre>
                </details>
              </div>
            </Card>
          )}
          {done && (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <Stat label="TD net" value={<span className="num">{bps(f.td_net_bps)} bps/yr</span>} sub={f.available ? `gross ${bps(f.td_gross_bps)}` : "no benchmark"} />
              <Stat label="Tracking error" value={<span className="num">{bps(f.te_bps)} bps</span>} />
              <Stat label="Exec cost" value={<span className="num">{bps(s.tca?.exec?.cost_bps)} bps</span>} sub={`${usd(s.tca?.exec?.cost_usd)} · notional-weighted`} />
              <Stat label="Notional traded" value={<span className="num">{usd(s.tca?.exec?.notional)}</span>} sub={`${num(s.counts?.fills)} fills`} />
            </div>
          )}
          <Card>
            <CardHeader title="Run record" />
            <div className="grid gap-6 p-4 md:grid-cols-2">
              <KV
                items={[
                  ["Submitted", `${dt(r.submitted_at)} by ${r.submitted_by}`],
                  ["Started", dt(r.started_at)],
                  ["Finished", dt(r.finished_at)],
                  ["Timing", s.timing ? `simulate ${s.timing.simulate_s}s · total ${s.timing.total_s}s` : "—"],
                  ["Output hash", <span key="o" className="font-mono">{r.output_hash?.slice(0, 24) ?? "—"}</span>],
                  ["Lake snapshot", <span key="l" className="font-mono">{r.data_snapshot_hashes?.lake_snapshot?.slice(0, 24) ?? "—"}</span>],
                  ["CA reviews hash", <span key="c" className="font-mono">{r.data_snapshot_hashes?.ca_reviews?.slice(0, 24) ?? "—"}</span>],
                  ["Desk policy hash", <span key="d" className="font-mono">{r.data_snapshot_hashes?.desk_policy?.slice(0, 24) ?? "—"}</span>],
                ]}
              />
              {done && (
                <KV
                  items={[
                    ["Order-chain audit", s.audit?.passed ? <Badge key="a" tone="good">passed · lineage 100%</Badge> : "—"],
                    ["Orphan fills / overruns", `${s.audit?.orphan_fills ?? "—"} / ${(s.audit?.fill_overruns ?? 0) + (s.audit?.trader_overruns ?? 0)}`],
                    ["Timestamp violations", s.audit?.ts_violations],
                    ["Counts", `${num(s.counts?.pm_orders)} PM · ${num(s.counts?.trader_orders)} trader · ${num(s.counts?.child_orders)} child · ${num(s.counts?.fills)} fills`],
                    ["Ledger entries", num(s.counts?.ledger_entries)],
                    ["BMLL mapping", `${s.mapping?.mapped_names}/${s.mapping?.names} names · traded notional mapped ${pct(s.mapping?.traded_notional_mapped_share, 2)}`],
                    ["Netting saved", `${usd(s.netting?.saved_notional)} (${bps(s.netting?.saved_bps_of_nav, 2)} bps of NAV)`],
                    ["Provisional dividends", `${s.provisional_dividends?.events ?? 0} events · ${usd(s.provisional_dividends?.usd)}`],
                    ["Fallback share", `${pct(s.tca?.fallbacks?.fallback_share, 3)}${s.tca?.fallbacks?.provisional ? " · PROVISIONAL" : ""}`],
                    ["Engine flags", Object.entries(s.flags ?? {}).map(([k, v]) => `${k}: ${v}`).join(", ") || "none"],
                  ]}
                />
              )}
            </div>
          </Card>
          <Card>
            <CardHeader title="Log" subtitle="Stages, mapping report and errors streamed over SSE" />
            <ul className="max-h-64 overflow-y-auto px-4 py-2 font-mono text-[11px]">
              {events.map((e) => (
                <li key={e.id} className={e.kind === "error" ? "text-critical" : "text-ink-2"}>
                  {new Date(e.data.ts).toLocaleTimeString("en-GB")} [{e.kind}] {e.data.stage ?? e.data.msg ?? e.data.message ?? (e.data.output_hash ? `done · output ${e.data.output_hash.slice(0, 16)} · ${e.data.seconds}s` : "")}
                </li>
              ))}
            </ul>
          </Card>
          <details className="rounded-lg border border-line bg-surface p-4">
            <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-ink-2">Config</summary>
            <pre className="mt-3 max-h-96 overflow-auto font-mono text-[11px] text-ink-2">{JSON.stringify(r.config, null, 2)}</pre>
          </details>
        </div>
      </div>
    </div>
  );
}
