"use client";

import { useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { AnimatedTabs } from "@/components/aceternity";
import { type Col, DataTable } from "@/components/DataTable";
import { Badge, Button, Card, CardHeader, ErrorBox, Input, Select, Skeleton, Stat, Status } from "@/components/ui";
import { sendJson } from "@/lib/api/client";
import { useJson } from "@/lib/hooks";
import { ago, num, pct } from "@/lib/format";

type Tab = "lake" | "jobs" | "mapping" | "ca";

export default function DataPage() {
  const [tab, setTab] = React.useState<Tab>("lake");
  const cov = useJson(["coverage"], "/data/coverage");
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Data</h1>
        <p className="text-sm text-ink-2">Lake manifests, pull jobs, the BMLL mapping review queue and the corporate-action review queue. Runs reference a snapshot by hash, never &quot;latest&quot;.</p>
      </div>
      {cov.data && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat label="Data source" value={cov.data.data_source} sub={cov.data.data_label === "SYNTHETIC" ? "schema-identical synthetic CRSP/BMLL" : cov.data.data_label} />
          <Stat label="WRDS" value={cov.data.wrds.configured ? "credentials set" : "not configured"} sub={cov.data.wrds.release ? `release ${cov.data.wrds.release}` : "—"} />
          <Stat label="BMLL" value={cov.data.bmll.configured ? "SSH key set" : "BMLL_KEY_PATH not set"} sub="SDK puller runs in an isolated env (bmll pins NumPy < 2)" />
          <Stat label="Mapping coverage" value={pct(cov.data.mapping?.member_weight_mapped, 2)} sub={`${cov.data.mapping?.mapped}/${cov.data.mapping?.names} names · target ≥ 99.5% of notional`} />
        </div>
      )}
      <AnimatedTabs<Tab> value={tab} onChange={setTab} tabs={[{ value: "lake", label: "Lake & snapshots" }, { value: "jobs", label: "Pull jobs" }, { value: "mapping", label: "Mapping review" }, { value: "ca", label: "Corporate-action review" }]} />
      {tab === "lake" && <Lake cov={cov} />}
      {tab === "jobs" && <Jobs />}
      {tab === "mapping" && <MappingQueue />}
      {tab === "ca" && <CaQueue />}
    </div>
  );
}

function Lake({ cov }: { cov: any }) {
  if (cov.isLoading) return <Skeleton className="h-96" />;
  if (cov.error) return <ErrorBox error={cov.error} />;
  const cols: Col<any>[] = [
    { header: "Dataset", accessorKey: "dataset" },
    { header: "Label", accessorKey: "label", cell: (c) => <Badge tone={c.getValue() === "SYNTHETIC" ? "serious" : "good"}>{c.getValue()}</Badge> },
    { header: "Rows", accessorKey: "rows", cell: (c) => num(c.getValue()), meta: { num: true } },
    { header: "Partitions", accessorKey: "partitions", meta: { num: true } },
    { header: "First", accessorKey: "first" },
    { header: "Last", accessorKey: "last" },
    { header: "Pulled", accessorKey: "pulled_at", cell: (c) => ago(c.getValue()) },
    { header: "SDK", accessorKey: "sdk" },
    { header: "Manifest SHA-256", accessorKey: "manifest", cell: (c) => <span className="font-mono">{String(c.getValue()).slice(0, 16)}</span> },
  ];
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="Lake datasets (latest manifest each)" subtitle="lake/{source}/{dataset}/date=YYYY-MM-DD/ · contracts checked on write" />
        <DataTable data={cov.data.datasets} height="auto" className="rounded-none border-x-0 border-b-0" columns={cols} rowKey={(r: any) => r.dataset} />
      </Card>
      <Card>
        <CardHeader title="Snapshots" subtitle="A snapshot pins one manifest per dataset; its hash goes on every run" />
        <ul className="divide-y divide-line text-xs">
          {[...cov.data.snapshots].reverse().map((s: any) => (
            <li key={s.snapshot_hash} className="flex items-center justify-between px-4 py-2">
              <span className="font-mono">{s.snapshot_hash}</span>
              <span className="text-ink-3">{Object.keys(s.manifests).length} datasets · {ago(s.created_at)}</span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

function Jobs() {
  const qc = useQueryClient();
  const jobs = useJson(["jobs"], "/data/jobs", undefined, { refetch: 3000 });
  const [source, setSource] = React.useState("synthetic");
  const [seed, setSeed] = React.useState("20260321");
  const pull = async () => {
    try {
      const r = await sendJson("POST", "/data/pull", { source, seed: Number(seed), start: "2022-03-01", end: "2026-07-31" });
      toast.success(`Pull job ${r.job_id.slice(0, 10)} queued`);
      qc.invalidateQueries({ queryKey: ["jobs"] });
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  const cols: Col<any>[] = [
    { header: "Job", accessorKey: "job_id", cell: (c) => <span className="font-mono">{String(c.getValue()).slice(0, 12)}</span> },
    { header: "Kind", accessorKey: "kind" },
    { header: "Source", accessorKey: "source" },
    { header: "Status", accessorKey: "status", cell: (c) => <Status value={c.getValue()} /> },
    { header: "Created", accessorKey: "created_at", cell: (c) => ago(c.getValue()) },
    { header: "By", accessorKey: "created_by" },
    { header: "Result / error", accessorFn: (r) => (r.error ? r.error.split("\n")[0] : r.result ? `snapshot ${String(r.result.snapshot).slice(0, 12)} · ${r.result.pending} pending CAs · ${r.result.mapping_queue} unmapped` : "") },
  ];
  return (
    <Card>
      <CardHeader
        title="Pull jobs"
        subtitle="CRSP directly from WRDS; BMLL via the SDK puller or a Data Lab job. Synthetic regenerates the schema-identical stand-in."
        right={
          <div className="flex items-center gap-2">
            <Select value={source} onChange={(e) => setSource(e.target.value)}>
              <option value="synthetic">synthetic</option>
              <option value="live">live (WRDS + BMLL)</option>
            </Select>
            {source === "synthetic" && <Input value={seed} onChange={(e) => setSeed(e.target.value)} className="h-8 w-28" aria-label="Seed" />}
            <Button variant="primary" size="sm" onClick={pull}><RefreshCw className="h-3.5 w-3.5" /> Pull now</Button>
          </div>
        }
      />
      <DataTable data={jobs.data ?? []} height={420} className="rounded-none border-x-0 border-b-0" columns={cols} rowKey={(r: any) => r.job_id} empty="No pull jobs yet" />
    </Card>
  );
}

function MappingQueue() {
  const qc = useQueryClient();
  const q = useJson(["mapping-review"], "/data/mapping-review");
  const [lid, setLid] = React.useState<Record<string, string>>({});
  const act = async (r: any, action: string) => {
    try {
      await sendJson("POST", `/data/mapping-review/${r.permno}/${r.valid_from}`, { action, listing_id: lid[r.permno] ? Number(lid[r.permno]) : undefined });
      toast.success(`${r.ticker}: ${action}`);
      qc.invalidateQueries({ queryKey: ["mapping-review"] });
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  const cols: Col<any>[] = [
    { header: "PERMNO", accessorKey: "permno" },
    { header: "Ticker", accessorKey: "ticker" },
    { header: "ISIN (from CUSIP9)", accessorKey: "isin" },
    { header: "From", accessorKey: "valid_from" },
    { header: "Reason", accessorKey: "reason" },
    { header: "Status", accessorKey: "status", cell: (c) => <Status value={c.getValue()} /> },
    {
      id: "act",
      header: "Action",
      cell: ({ row }) => (
        <div className="flex items-center gap-1">
          <Input placeholder="ListingId" value={lid[row.original.permno] ?? ""} onChange={(e) => setLid((x) => ({ ...x, [row.original.permno]: e.target.value }))} className="h-7 w-28 text-xs" />
          <Button size="sm" onClick={() => act(row.original, "FIX")}>Approve fix</Button>
          <Button size="sm" variant="ghost" onClick={() => act(row.original, "EXCLUDE")}>Exclude</Button>
          <Button size="sm" variant="ghost" onClick={() => act(row.original, "REOPEN")}>Reopen</Button>
        </div>
      ),
    },
  ];
  if (q.error) return <ErrorBox error={q.error} />;
  return (
    <Card>
      <CardHeader title="Unmapped names" subtitle="No ISIN match and the ticker + OPOL fallback failed its 1 bp price check. Never dropped silently: runs exclude (listed) or stop." />
      <DataTable data={q.data ?? []} height="auto" className="rounded-none border-x-0 border-b-0" columns={cols} rowKey={(r: any) => `${r.permno}${r.valid_from}`} empty="Mapping queue is empty" />
    </Card>
  );
}

function CaQueue() {
  const qc = useQueryClient();
  const [status, setStatus] = React.useState("PENDING");
  const q = useJson(["ca", status], "/data/corporate-actions", { status: status || undefined, limit: 500 });
  const sum = useJson(["ca-sum"], "/data/corporate-actions/summary");
  const act = async (r: any, s: string) => {
    try {
      await sendJson("POST", `/data/corporate-actions/${r.permno}/${r.ex_date}/${r.type}`, { status: s });
      toast.success(`${r.type} ${r.permno} ${r.ex_date}: ${s}`);
      qc.invalidateQueries({ queryKey: ["ca"] });
      qc.invalidateQueries({ queryKey: ["ca-sum"] });
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  const cols: Col<any>[] = [
    { header: "PERMNO", accessorKey: "permno" },
    { header: "Type", accessorKey: "type" },
    { header: "Ex", accessorKey: "ex_date" },
    { header: "Declared", accessorKey: "declared_date" },
    { header: "Pay", accessorKey: "pay_date", cell: (c) => c.getValue() ?? <Badge tone="serious">missing</Badge> },
    { header: "Amount", accessorKey: "amount", cell: (c) => num(c.getValue(), 4), meta: { num: true } },
    { header: "Factor", accessorKey: "factor", meta: { num: true } },
    { header: "Status", accessorKey: "review_status", cell: (c) => <Status value={c.getValue()} /> },
    { header: "Note", accessorKey: "note" },
    {
      id: "act",
      header: "Review",
      cell: ({ row }) => (
        <div className="flex gap-1">
          <Button size="sm" disabled={row.original.type === "MG"} onClick={() => act(row.original, "APPROVED")}>Approve</Button>
          <Button size="sm" variant="ghost" onClick={() => act(row.original, "REJECTED")}>Reject</Button>
        </div>
      ),
    },
  ];
  return (
    <Card>
      <CardHeader
        title="Corporate-action review queue"
        subtitle="Ported from the notebook's decision CSVs. An unreviewed or unsupported action on a held security stops the run unless the exclusion policy omits it. The review set is hashed into each run."
        right={
          <Select value={status} onChange={(e) => setStatus(e.target.value)}>
            {["PENDING", "UNSUPPORTED", "APPROVED", "REJECTED", ""].map((s) => <option key={s} value={s}>{s || "All"}</option>)}
          </Select>
        }
      />
      <div className="flex flex-wrap gap-2 px-4 py-2 text-[11px] text-ink-3">
        {(sum.data ?? []).map((s: any) => <span key={`${s.type}${s.review_status}`}>{s.type} {s.review_status}: {s.n}</span>)}
      </div>
      <DataTable data={q.data ?? []} height={520} className="rounded-none border-x-0 border-b-0" columns={cols} rowKey={(r: any) => `${r.permno}${r.ex_date}${r.type}`} />
    </Card>
  );
}

