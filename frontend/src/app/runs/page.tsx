"use client";

import { GitCompare, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import * as React from "react";

import { type Col, DataTable } from "@/components/DataTable";
import { Button, ErrorBox, Select, Skeleton, Status } from "@/components/ui";
import { useRuns } from "@/lib/hooks";
import { ago, day } from "@/lib/format";

export default function RunsPage() {
  const [status, setStatus] = React.useState("");
  const runs = useRuns(status ? { status } : {}, 3000);
  const [sel, setSel] = React.useState<string[]>([]);
  const router = useRouter();
  const cols: Col<any>[] = [
    {
      id: "sel",
      header: "",
      cell: ({ row }) => (
        <input
          type="checkbox"
          aria-label="Select for compare"
          checked={sel.includes(row.original.run_id)}
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => setSel((s) => (e.target.checked ? [...s, row.original.run_id] : s.filter((x) => x !== row.original.run_id)))}
        />
      ),
      meta: { width: 28 },
    },
    { header: "Run", accessorKey: "run_id", cell: (c) => <span className="font-mono">{String(c.getValue()).slice(0, 14)}</span> },
    { header: "Name", accessorKey: "name" },
    { header: "Fund", accessorKey: "fund_id" },
    { header: "Status", accessorKey: "status", cell: (c) => <Status value={c.getValue()} /> },
    { header: "Stage", accessorFn: (r) => (r.status === "DONE" ? "" : `${r.stage ?? ""} ${r.progress ? Math.round(r.progress * 100) + "%" : ""}`) },
    { header: "Config hash", accessorKey: "config_hash", cell: (c) => <span className="font-mono text-ink-2">{String(c.getValue()).slice(0, 12)}</span> },
    { header: "Data", accessorKey: "data_label" },
    { header: "Submitted", accessorKey: "submitted_at", cell: (c) => `${day(c.getValue())} (${ago(c.getValue())})` },
    {
      header: "Duration (s)",
      accessorFn: (r) => (r.finished_at && r.started_at ? (new Date(r.finished_at).getTime() - new Date(r.started_at).getTime()) / 1000 : null),
      cell: (c) => (c.getValue() ? (c.getValue() as number).toFixed(1) : "—"),
      meta: { num: true },
    },
    { header: "By", accessorKey: "submitted_by" },
    { header: "Study", accessorFn: (r) => (r.study_id ? `${r.study_id.slice(0, 8)} #${r.study_step}` : "") },
  ];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Runs</h1>
          <p className="text-sm text-ink-2">A run is defined by its config, data snapshot hashes and code version, and replays bit for bit.</p>
        </div>
        <div className="flex items-center gap-2">
          <Select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status filter">
            {["", "QUEUED", "RUNNING", "ANALYSING", "DONE", "FAILED"].map((s) => <option key={s} value={s}>{s || "All statuses"}</option>)}
          </Select>
          <Button disabled={sel.length < 2} onClick={() => router.push(`/runs/compare?ids=${sel.join(",")}`)}>
            <GitCompare className="h-4 w-4" /> Compare {sel.length > 0 ? `(${sel.length})` : ""}
          </Button>
          <Link href="/runs/new"><Button variant="primary"><Plus className="h-4 w-4" /> New run</Button></Link>
        </div>
      </div>
      {runs.error && <ErrorBox error={runs.error} />}
      {runs.isLoading ? <Skeleton className="h-96" /> : <DataTable data={runs.data ?? []} columns={cols} height={680} rowKey={(r: any) => r.run_id} onRowClick={(r: any) => router.push(`/runs/${r.run_id}`)} />}
    </div>
  );
}
