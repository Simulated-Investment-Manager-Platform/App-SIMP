"use client";

import { useQueryClient } from "@tanstack/react-query";
import * as React from "react";
import { toast } from "sonner";

import { AnimatedTabs } from "@/components/aceternity";
import { type Col, DataTable } from "@/components/DataTable";
import { Badge, Button, Card, CardHeader, ErrorBox, Input, Select, Skeleton } from "@/components/ui";
import { sendJson } from "@/lib/api/client";
import { useJson, useMe } from "@/lib/hooks";

type Tab = "users" | "brokers" | "venues" | "policy";

export default function Settings() {
  const [tab, setTab] = React.useState<Tab>("policy");
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Settings</h1>
        <p className="text-sm text-ink-2">Users and roles, simulated brokers and venues, and the desk policy (data, not code).</p>
      </div>
      <AnimatedTabs<Tab> value={tab} onChange={setTab} tabs={[{ value: "policy", label: "Desk policy" }, { value: "users", label: "Users & roles" }, { value: "brokers", label: "Brokers" }, { value: "venues", label: "Venues" }]} />
      {tab === "users" && <Users />}
      {tab === "brokers" && <Simple path="/brokers" cols={brokerCols} k="broker_id" title="Brokers" subtitle="Commission schedules; Broker C runs portfolio trades at a fixed fee with a closing-price guarantee" />}
      {tab === "venues" && <Simple path="/venues" cols={venueCols} k="mic" title="Venues" subtitle="MIC, auction support and fee model (USD per share; negative = rebate)" />}
      {tab === "policy" && <Policy />}
    </div>
  );
}

function Users() {
  const qc = useQueryClient();
  const me = useMe();
  const q = useJson(["users"], "/users");
  const [u, setU] = React.useState("");
  const [role, setRole] = React.useState("viewer");
  const switchUser = async (name: string) => {
    try {
      await sendJson("POST", "/auth/dev-login", { username: name });
      toast.success(`Signed in as ${name}`);
      qc.invalidateQueries();
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  const save = async () => {
    try {
      await sendJson("POST", "/users", { username: u, role });
      toast.success(`${u} is ${role}`);
      qc.invalidateQueries({ queryKey: ["users"] });
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  const cols: Col<any>[] = [
    { header: "User", accessorKey: "username" },
    { header: "Role", accessorKey: "role", cell: (c) => <Badge>{c.getValue()}</Badge> },
    { header: "Created", accessorKey: "created_at", cell: (c) => String(c.getValue()).slice(0, 10) },
    { id: "s", header: "", cell: ({ row }) => (me.data?.auth_mode === "dev" ? <Button size="sm" variant="ghost" onClick={() => switchUser(row.original.username)}>Sign in as</Button> : null) },
  ];
  return (
    <Card>
      <CardHeader title="Allow-list" subtitle={`Roles: viewer (read), researcher (submit runs, reviews, policy), admin (users). Auth mode: ${me.data?.auth_mode ?? "—"}. You are ${me.data?.username} (${me.data?.role}).`}
        right={
          <div className="flex items-center gap-2">
            <Input value={u} onChange={(e) => setU(e.target.value)} placeholder="username" className="h-8 w-36" />
            <Select value={role} onChange={(e) => setRole(e.target.value)}>{["viewer", "researcher", "admin"].map((r) => <option key={r}>{r}</option>)}</Select>
            <Button size="sm" onClick={save} disabled={!u}>Save</Button>
          </div>
        } />
      <DataTable data={q.data ?? []} height="auto" className="rounded-none border-x-0 border-b-0" columns={cols} rowKey={(r: any) => r.username} />
    </Card>
  );
}

function Simple({ path, cols, k, title, subtitle }: { path: string; cols: Col<any>[]; k: string; title: string; subtitle: string }) {
  const q = useJson([path], path);
  if (q.error) return <ErrorBox error={q.error} />;
  return (
    <Card>
      <CardHeader title={title} subtitle={subtitle} />
      <DataTable data={q.data ?? []} height="auto" className="rounded-none border-x-0 border-b-0" columns={cols} rowKey={(r: any) => r[k]} />
    </Card>
  );
}

function Policy() {
  const qc = useQueryClient();
  const q = useJson(["policies"], "/desk-policies");
  const [name, setName] = React.useState("default");
  const [text, setText] = React.useState("");
  const [val, setVal] = React.useState<{ ok: boolean; errors: string[]; rules?: number } | null>(null);
  React.useEffect(() => {
    const p = (q.data ?? []).find((x: any) => x.name === name);
    if (p) setText(p.yaml);
  }, [q.data, name]);
  React.useEffect(() => {
    if (!text) return;
    const t = setTimeout(async () => setVal(await sendJson("POST", "/desk-policies/validate", { yaml: text })), 350);
    return () => clearTimeout(t);
  }, [text]);
  const save = async (as: string) => {
    try {
      await sendJson("PUT", `/desk-policies/${as}`, { yaml: text });
      toast.success(`Policy '${as}' saved; new runs can select it`);
      qc.invalidateQueries({ queryKey: ["policies"] });
      setName(as);
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  if (q.isLoading) return <Skeleton className="h-96" />;
  return (
    <Card>
      <CardHeader
        title="Desk policy (YAML)"
        subtitle="First matching rule decides a PM order's trader orders. Validated live against the policy schema; each run records the policy hash."
        right={
          <div className="flex items-center gap-2">
            <Select value={name} onChange={(e) => setName(e.target.value)}>{(q.data ?? []).map((p: any) => <option key={p.name}>{p.name}</option>)}</Select>
            <Button size="sm" disabled={!val?.ok} onClick={() => save(name)}>Save</Button>
            <Button size="sm" disabled={!val?.ok} onClick={() => { const n = prompt("Save as new policy name:", `${name}-variant`); if (n) save(n); }}>Save as…</Button>
          </div>
        }
      />
      <div className="grid gap-0 lg:grid-cols-[1fr_320px]">
        <textarea
          aria-label="Desk policy YAML"
          spellCheck={false}
          value={text}
          onChange={(e) => setText(e.target.value)}
          className="min-h-[560px] w-full resize-y border-0 bg-raised p-4 font-mono text-xs leading-5 text-ink focus:outline-none"
        />
        <div className="border-l border-line p-4 text-xs">
          {val?.ok ? <Badge tone="good">valid · {val.rules} rules</Badge> : <Badge tone="critical">invalid</Badge>}
          <ul className="mt-3 space-y-1 text-critical">{(val?.errors ?? []).map((e) => <li key={e} className="font-mono">{e}</li>)}</ul>
          <div className="mt-4 space-y-2 text-ink-2">
            <p><b>when</b>: reason, instruction, max_notional, basket, max_pct_expected_auction, max_pct_adv</p>
            <p><b>then</b>: legs of strategy + broker (or weighted brokers), window, pct_expected_auction, remainder</p>
            <p>The last rule must be a catch-all. MOC quantity above <code>auction_spill_cap</code> × expected auction volume spills to a continuous VWAP leg.</p>
          </div>
        </div>
      </div>
    </Card>
  );
}

const brokerCols: Col<any>[] = [
  { header: "Broker", accessorKey: "broker_id" },
  { header: "Name", accessorKey: "name" },
  { header: "Specialty", accessorKey: "specialty" },
  { header: "Commission (bps)", accessorKey: "commission_bps", meta: { num: true } },
  { header: "Portfolio fee (bps)", accessorKey: "portfolio_fee_bps", meta: { num: true } },
];
const venueCols: Col<any>[] = [
  { header: "MIC", accessorKey: "mic" },
  { header: "Name", accessorKey: "name" },
  { header: "Kind", accessorKey: "kind" },
  { header: "Auction", accessorKey: "supports_auction", cell: (c) => (c.getValue() ? "yes" : "no") },
  { header: "Remove ($/sh)", accessorKey: "fee_remove", meta: { num: true } },
  { header: "Add ($/sh)", accessorKey: "fee_add", meta: { num: true } },
  { header: "Auction ($/sh)", accessorKey: "fee_auction", meta: { num: true } },
  { header: "Midpoint ($/sh)", accessorKey: "fee_midpoint", meta: { num: true } },
];
