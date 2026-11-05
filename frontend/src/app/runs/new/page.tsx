"use client";

import { useQuery } from "@tanstack/react-query";
import { Check, ChevronLeft, ChevronRight, Play, XCircle } from "lucide-react";
import { useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { MovingBorderButton } from "@/components/aceternity";
import { Button, Card, ErrorBox, Field, Input, Select, Skeleton } from "@/components/ui";
import { getJson, sendJson } from "@/lib/api/client";
import { cn } from "@/lib/cn";

const STEPS = ["Fund", "Window", "Timing rule", "Flows", "Desk policy", "Execution model", "Review"] as const;

export default function NewRun() {
  const router = useRouter();
  const presets = useQuery({ queryKey: ["presets"], queryFn: () => getJson("/presets") });
  const funds = useQuery({ queryKey: ["funds"], queryFn: () => getJson("/funds") });
  const [step, setStep] = React.useState(0);
  const [cfg, setCfg] = React.useState<any>(null);
  const [busy, setBusy] = React.useState(false);
  React.useEffect(() => {
    if (presets.data && !cfg) setCfg(presets.data.presets.index_t1_flows);
  }, [presets.data, cfg]);
  const check = useQuery({ queryKey: ["check", cfg], enabled: !!cfg && step === STEPS.length - 1, queryFn: () => sendJson("POST", "/runs/check", cfg) });
  if (!cfg || !presets.data) return <Skeleton className="h-96" />;
  const set = (path: string, v: any) =>
    setCfg((c: any) => {
      const n = structuredClone(c);
      const ks = path.split(".");
      let o = n;
      for (const k of ks.slice(0, -1)) o = o[k];
      o[ks[ks.length - 1]] = v;
      return n;
    });
  const submit = async () => {
    setBusy(true);
    try {
      const r = await sendJson("POST", "/runs", cfg);
      toast.success(`Run ${r.run_id.slice(0, 12)} queued`);
      router.push(`/runs/${r.run_id}`);
    } catch (e: any) {
      toast.error(e.message);
      setBusy(false);
    }
  };
  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div>
        <h1 className="text-xl font-semibold">New run</h1>
        <p className="text-sm text-ink-2">Fund → window → timing rule → flows preset → desk policy → execution model → review. A data-availability check runs before submit.</p>
      </div>
      <div className="flex flex-wrap gap-2">
        {Object.entries(presets.data.presets).map(([k, v]: any) => (
          <Button key={k} size="sm" variant={cfg.name === v.name ? "primary" : "default"} onClick={() => setCfg(structuredClone(v))}>
            {v.name}
          </Button>
        ))}
      </div>
      <ol className="flex flex-wrap gap-1">
        {STEPS.map((s, i) => (
          <li key={s}>
            <button onClick={() => setStep(i)} className={cn("flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs", i === step ? "border-accent bg-accent/15 text-ink" : i < step ? "border-line text-ink-2" : "border-line text-ink-3")}>
              <span className="num">{i + 1}</span> {s}
            </button>
          </li>
        ))}
      </ol>
      <Card className="p-5">
        {step === 0 && (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Fund">
              <Select
                className="h-9 w-full"
                value={cfg.fund_id}
                onChange={(e) => {
                  const f = (funds.data ?? []).find((x: any) => x.fund_id === e.target.value);
                  set("fund_id", e.target.value);
                  set("strategy", f?.strategy_type === "SYNTHETIC" ? "synthetic_generator" : "index_replication");
                }}
              >
                {(funds.data ?? []).map((f: any) => <option key={f.fund_id} value={f.fund_id}>{f.name}</option>)}
              </Select>
            </Field>
            <Field label="Run name"><Input value={cfg.name} onChange={(e) => set("name", e.target.value)} /></Field>
            <Field label="Strategy" hint="index_replication or the Phase 1 random order generator">
              <Select className="h-9 w-full" value={cfg.strategy} onChange={(e) => set("strategy", e.target.value)}>
                <option value="index_replication">index_replication</option>
                <option value="synthetic_generator">synthetic_generator</option>
              </Select>
            </Field>
            <Field label="Seed" hint="PCG64 per (seed, component, date)"><Input type="number" value={cfg.seed} onChange={(e) => set("seed", Number(e.target.value))} /></Field>
            <Field label="Initial AUM ($)"><Input type="number" value={cfg.initial_aum} onChange={(e) => set("initial_aum", Number(e.target.value))} /></Field>
            <Field label="Management fee (bps / yr)"><Input type="number" step="0.5" value={cfg.fee_bps} onChange={(e) => set("fee_bps", Number(e.target.value))} /></Field>
          </div>
        )}
        {step === 1 && (
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Bootstrap" hint="Seed capital and initial basket"><Input type="date" value={cfg.window.bootstrap} onChange={(e) => set("window.bootstrap", e.target.value)} /></Field>
            <Field label="Start"><Input type="date" value={cfg.window.start} onChange={(e) => set("window.start", e.target.value)} /></Field>
            <Field label="End"><Input type="date" value={cfg.window.end} onChange={(e) => set("window.end", e.target.value)} /></Field>
          </div>
        )}
        {step === 2 && (
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Rule" hint="EFFECTIVE_DATE trades the third-Friday closing auction; HANDOFF decides Monday, fills Tuesday">
              <Select className="h-9 w-full" value={cfg.timing.rule} onChange={(e) => set("timing.rule", e.target.value)}>
                <option value="EFFECTIVE_DATE">EFFECTIVE_DATE</option>
                <option value="HANDOFF">HANDOFF</option>
              </Select>
            </Field>
            <Field label="Decision offset (sessions before effective)"><Input type="number" min={1} max={20} value={cfg.timing.decision_offset_sessions} onChange={(e) => set("timing.decision_offset_sessions", Number(e.target.value))} /></Field>
            <Field label="Initial execution offset (sessions)"><Input type="number" min={0} max={5} value={cfg.timing.initial_execution_offset} onChange={(e) => set("timing.initial_execution_offset", Number(e.target.value))} /></Field>
            <Field label="Benchmark">
              <Select className="h-9 w-full" value={cfg.benchmark} onChange={(e) => set("benchmark", e.target.value)}>
                <option value="CRSP_VWRETD">(a) CRSP cap-weighted with dividends</option>
                <option value="SP500_TR">(b) official S&P 500 TR</option>
              </Select>
            </Field>
            <Field label="Exclusion policy">
              <Select className="h-9 w-full" value={cfg.exclusion_policy} onChange={(e) => set("exclusion_policy", e.target.value)}>
                <option value="exclude_securities">exclude_securities</option>
                <option value="keep_securities">keep_securities</option>
              </Select>
            </Field>
            <Field label="Provisional dividend rule">
              <Select className="h-9 w-full" value={String(cfg.provisional_dividends)} onChange={(e) => set("provisional_dividends", e.target.value === "true")}>
                <option value="true">on</option>
                <option value="false">off</option>
              </Select>
            </Field>
          </div>
        )}
        {step === 3 && (
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Preset">
              <Select className="h-9 w-full" value={cfg.flows.preset} onChange={(e) => {
                const p = e.target.value;
                if (p === "none") set("flows", { ...cfg.flows, preset: "none", enabled: false, cash_sweep: false, cash_buffer_bps: 0 });
                else if (p === "notebook_seeded") set("flows", { ...cfg.flows, preset: p, enabled: true, cash_sweep: true, sigma_bps: 0.5, jump_prob: 0, phi: 0, cash_buffer_bps: 10 });
                else set("flows", { ...cfg.flows, preset: p, enabled: true, cash_sweep: true, sigma_bps: 10, jump_prob: 0.005, phi: 0.3, cash_buffer_bps: 10 });
              }}>
                <option value="default">default (σ 10 bps, jumps)</option>
                <option value="notebook_seeded">notebook seeded_flows (σ 0.5 bps)</option>
                <option value="custom">custom</option>
                <option value="none">none (handoff)</option>
              </Select>
            </Field>
            {(["mu_bps", "sigma_bps", "phi", "nu", "jump_prob", "jump_min_bps", "jump_max_bps", "cash_buffer_bps", "min_ticket_usd", "min_ticket_nav_bps"] as const).map((k) => (
              <Field key={k} label={k}><Input type="number" step="any" value={cfg.flows[k]} onChange={(e) => { set(`flows.${k}`, Number(e.target.value)); set("flows.preset", "custom"); }} /></Field>
            ))}
            <Field label="Netting of flow vs rebalance orders">
              <Select className="h-9 w-full" value={String(cfg.flows.netting)} onChange={(e) => set("flows.netting", e.target.value === "true")}><option value="true">on</option><option value="false">off</option></Select>
            </Field>
          </div>
        )}
        {step === 4 && (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Desk policy" hint="YAML rules table; edit in Settings">
              <Select className="h-9 w-full" value={cfg.desk_policy} onChange={(e) => set("desk_policy", e.target.value)}>
                {presets.data.desk_policies.map((p: string) => <option key={p}>{p}</option>)}
              </Select>
            </Field>
            <Field label="MOC share of expected auction volume (override)" hint="blank = policy (5%); sensitivity sweeps 2–10%">
              <Input type="number" step="0.01" value={cfg.cost.moc_auction_share ?? ""} onChange={(e) => set("cost.moc_auction_share", e.target.value === "" ? null : Number(e.target.value))} />
            </Field>
          </div>
        )}
        {step === 5 && (
          <div className="space-y-4">
            <div className="grid gap-2 sm:grid-cols-4">
              {[["T0", "Close proxy, zero cost (handoff baseline)"], ["T1", "Daily BMLL data + impact models"], ["T2", "Tick replay against interval prints"], ["T3", "bmll2.simulation (Data Lab only)"]].map(([m, d]) => (
                <button key={m} onClick={() => set("execution_model", m)} className={cn("rounded-lg border p-3 text-left", cfg.execution_model === m ? "border-accent bg-accent/10" : "border-line hover:border-ink-3")}>
                  <div className="text-sm font-semibold">{m}</div>
                  <div className="mt-1 text-xs text-ink-2">{d}</div>
                </button>
              ))}
            </div>
            <div className="grid gap-4 sm:grid-cols-3">
              {(["Y", "kappa", "beta", "lam", "auction_cap", "t2_interval_cap"] as const).map((k) => (
                <Field key={k} label={k}><Input type="number" step="any" value={cfg.cost[k]} onChange={(e) => set(`cost.${k}`, Number(e.target.value))} /></Field>
              ))}
            </div>
          </div>
        )}
        {step === 6 && (
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-2">Data-availability check</h3>
              {check.isLoading && <Skeleton className="h-40" />}
              {check.error && <ErrorBox error={check.error} />}
              <ul className="space-y-2">
                {(check.data?.checks ?? []).map((c: any) => (
                  <li key={c.name} className="flex items-start gap-2 text-sm">
                    {c.ok ? <Check className="mt-0.5 h-4 w-4 text-good" /> : <XCircle className="mt-0.5 h-4 w-4 text-critical" />}
                    <div>
                      <div className="font-medium">{c.name}</div>
                      <div className="text-xs text-ink-2">{c.detail}</div>
                      {c.fix && <div className="text-xs text-accent">Fix: {c.fix}</div>}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-2">Config</h3>
              <pre className="max-h-[420px] overflow-auto rounded-md border border-line bg-raised p-3 font-mono text-[11px] text-ink-2">{JSON.stringify(cfg, null, 2)}</pre>
            </div>
          </div>
        )}
      </Card>
      <div className="flex items-center justify-between">
        <Button disabled={step === 0} onClick={() => setStep(step - 1)}><ChevronLeft className="h-4 w-4" /> Back</Button>
        {step < STEPS.length - 1 ? (
          <Button onClick={() => setStep(step + 1)}>Next <ChevronRight className="h-4 w-4" /></Button>
        ) : (
          <MovingBorderButton onClick={submit} disabled={busy || !check.data?.ok} data-testid="submit-run">
            <Play className="h-4 w-4" /> Run simulation
          </MovingBorderButton>
        )}
      </div>
    </div>
  );
}
