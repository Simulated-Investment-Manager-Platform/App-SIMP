"use client";

import Link from "next/link";

import { DataLabel, Empty, Select, Skeleton } from "@/components/ui";
import { useSelectedRun } from "@/lib/hooks";
import { day } from "@/lib/format";

/** Every working screen is scoped to a run (?run= in the URL so views are shareable). */
export function RunScope({ fundId, title, subtitle, children }: { fundId?: string; title: string; subtitle?: string; children: (runId: string) => React.ReactNode }) {
  const { runId, setRun, runs, loading } = useSelectedRun(fundId);
  const done = runs.filter((r) => r.status === "DONE");
  const cur = runs.find((r) => r.run_id === runId);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink">{title}</h1>
          {subtitle && <p className="mt-0.5 text-sm text-ink-2">{subtitle}</p>}
        </div>
        <div className="flex items-center gap-2">
          <DataLabel label={cur?.data_label} />
          <Select value={runId ?? ""} onChange={(e) => setRun(e.target.value || null)} className="max-w-[420px]" aria-label="Run">
            {done.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.name} · {r.run_id.slice(0, 10)} · {day(r.finished_at)}
              </option>
            ))}
          </Select>
        </div>
      </div>
      {loading ? <Skeleton className="h-96" /> : runId ? children(runId) : <Empty title="No finished run yet" detail="The screen fills in once a run reaches DONE." action="Start a run" href="/runs/new" />}
      {cur && (
        <div className="text-[11px] text-ink-3">
          Run <Link className="font-mono text-accent hover:underline" href={`/runs/${cur.run_id}`}>{cur.run_id}</Link> · config {cur.config_hash.slice(0, 12)} · code {cur.code_version ?? "—"}
        </div>
      )}
    </div>
  );
}
