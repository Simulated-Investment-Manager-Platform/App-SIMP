"use client";

import { useQuery } from "@tanstack/react-query";
import { Activity, Database, GitBranch, Play } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { BackgroundBeams, BentoGrid, BentoItem, HoverCards, MovingBorderButton, Spotlight } from "@/components/aceternity";
import { Sparkline } from "@/components/charts";
import { Badge, DataLabel, Empty, ErrorBox, Skeleton, Status } from "@/components/ui";
import { getJson } from "@/lib/api/client";
import { ago, bps, pct, usd } from "@/lib/format";

export default function Overview() {
  const router = useRouter();
  const q = useQuery({ queryKey: ["overview"], queryFn: () => getJson("/overview"), refetchInterval: 10_000 });
  const d = q.data;
  return (
    <div className="space-y-6">
      <section className="relative overflow-hidden rounded-2xl border border-line bg-surface px-6 py-10 md:px-10">
        <Spotlight className="-left-10 -top-40 md:-left-32" />
        <BackgroundBeams />
        <div className="relative max-w-3xl">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <Badge>Simulated Investment Manager Platform</Badge>
            <DataLabel label={d?.health?.data_label} />
          </div>
          <h1 className="text-2xl font-semibold tracking-tight text-ink md:text-4xl">A synthetic asset manager, end to end.</h1>
          <p className="mt-3 max-w-2xl text-sm text-ink-2 md:text-base">
            Funds decide on CRSP data. Decisions become PM orders, trader orders and broker child orders, filled against BMLL auction and intraday
            data, and measured with notional-weighted fund and order-level analytics.
          </p>
          <div className="mt-6 flex flex-wrap items-center gap-3">
            <MovingBorderButton onClick={() => router.push("/runs/new")}>
              <Play className="h-4 w-4" /> Run simulation
            </MovingBorderButton>
            <Link href="/tca" className="text-sm font-medium text-ink-2 hover:text-ink">
              Open TCA →
            </Link>
          </div>
        </div>
      </section>

      {q.error && <ErrorBox error={q.error} title="Could not load the overview" />}
      <HealthStrip health={d?.health} loading={q.isLoading} />

      {q.isLoading ? (
        <div className="grid gap-3 md:grid-cols-3">
          <Skeleton className="h-44 md:col-span-2" />
          <Skeleton className="h-44" />
        </div>
      ) : (
        <BentoGrid>
          {(d?.funds ?? []).map((f: any, i: number) => (
            <BentoItem key={f.fund_id} href={`/funds/${f.fund_id}${f.last_run ? `?run=${f.last_run.run_id}` : ""}`} className={i === 0 ? "md:col-span-2" : ""}>
              <FundTile f={f} big={i === 0} />
            </BentoItem>
          ))}
          <BentoItem className="md:row-span-2">
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-sm font-semibold">Recent runs</h3>
              <Link href="/runs" className="text-xs text-accent hover:underline">
                All runs
              </Link>
            </div>
            {(d?.recent_runs ?? []).length === 0 && <Empty title="No runs yet" detail="Submit the index fund run to populate the platform." action="New run" href="/runs/new" />}
            <ul className="divide-y divide-line">
              {(d?.recent_runs ?? []).map((r: any) => (
                <li key={r.run_id}>
                  <Link href={`/runs/${r.run_id}`} className="flex items-center justify-between gap-2 py-2 hover:text-accent">
                    <div className="min-w-0">
                      <div className="truncate text-xs font-medium text-ink">{r.name}</div>
                      <div className="font-mono text-[10px] text-ink-3">
                        {r.run_id.slice(0, 12)} · {r.config_hash.slice(0, 8)} · {ago(r.submitted_at)}
                      </div>
                    </div>
                    <Status value={r.status} />
                  </Link>
                </li>
              ))}
            </ul>
          </BentoItem>
          <BentoItem href="/studies">
            <div className="flex h-full flex-col justify-between">
              <div>
                <GitBranch className="mb-2 h-5 w-5 text-accent" />
                <h3 className="text-sm font-semibold">Counterfactual chains & sensitivity</h3>
                <p className="mt-1 text-xs text-ink-2">Timing, flows, costs and exclusions switched on one at a time; dividend rule, cost parameters and flow regimes swept.</p>
              </div>
              <span className="text-xs font-medium text-accent">Open studies →</span>
            </div>
          </BentoItem>
          <BentoItem href="/blotter">
            <div className="flex h-full flex-col justify-between">
              <div>
                <Activity className="mb-2 h-5 w-5 text-accent" />
                <h3 className="text-sm font-semibold">One order lineage</h3>
                <p className="mt-1 text-xs text-ink-2">Every share traced fill → child order → trader order → PM order → fund decision, with benchmark prices at each level.</p>
              </div>
              <span className="text-xs font-medium text-accent">Open blotter →</span>
            </div>
          </BentoItem>
        </BentoGrid>
      )}

      {(d?.funds ?? []).length > 0 && (
        <section>
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-ink-2">Funds</h2>
          <HoverCards
            items={d.funds}
            keyOf={(f: any) => f.fund_id}
            href={(f: any) => `/funds/${f.fund_id}`}
            render={(f: any) => (
              <div>
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium">{f.name}</span>
                  <Badge>{f.strategy_type}</Badge>
                </div>
                <div className="mt-1 text-xs text-ink-3">
                  {f.fund_id} · inception {f.inception_date} · fee {f.fee_bps} bps · buffer {f.cash_buffer_bps} bps
                </div>
              </div>
            )}
          />
        </section>
      )}
    </div>
  );
}

function FundTile({ f, big }: { f: any; big: boolean }) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-ink">{f.name}</h3>
          <div className="text-xs text-ink-3">
            {f.strategy_type} · benchmark {f.benchmark_id}
          </div>
        </div>
        {f.latest ? <Status value={f.latest.status} /> : <Badge>no runs</Badge>}
      </div>
      {f.last_run ? (
        <>
          <div className={`mt-3 grid gap-3 ${big ? "grid-cols-2 md:grid-cols-4" : "grid-cols-2"}`}>
            <Kpi label="AUM" value={usd(f.aum)} />
            <Kpi label="TD net (bps/yr)" value={bps(f.td_net_bps)} />
            <Kpi label="TE (bps)" value={bps(f.te_bps)} />
            <Kpi label="Exec cost (bps, notional-wtd)" value={bps(f.exec_cost_bps)} />
          </div>
          <Sparkline values={f.sparkline ?? []} className="mt-auto h-14 w-full pt-3" />
          <div className="text-[10px] text-ink-3">Unit NAV, weekly · last run {ago(f.last_run.finished_at)}</div>
        </>
      ) : (
        <div className="mt-4 text-xs text-ink-2">No finished run yet. Start one from Runs → New run.</div>
      )}
    </div>
  );
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-ink-3">{label}</div>
      <div className="num text-lg font-semibold text-ink">{value}</div>
    </div>
  );
}

function HealthStrip({ health, loading }: { health: any; loading: boolean }) {
  if (loading) return <Skeleton className="h-14" />;
  if (!health) return null;
  const m = health.mapping;
  return (
    <div className="grid grid-cols-1 gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-2 lg:grid-cols-4">
      <HealthCell title="CRSP (WRDS)" ok={!!health.wrds?.release} detail={health.wrds?.release ? `release ${health.wrds.release}` : "not pulled"} sub={health.data_label === "SYNTHETIC" ? "synthetic CIZ-schema stand-in" : health.wrds?.configured ? "credentials configured" : "credentials missing"} />
      <HealthCell title="BMLL pull" ok={!!health.bmll?.last_pull} detail={health.bmll?.last_pull ? `daily analytics ${ago(health.bmll.last_pull)}` : "not pulled"} sub={health.data_label === "SYNTHETIC" ? "synthetic Data Lab-schema stand-in" : health.bmll?.configured ? "SSH key configured" : "BMLL_KEY_PATH not set"} />
      <HealthCell title="Mapping coverage" ok={(m?.member_weight_mapped ?? 0) >= 0.995} detail={m ? `${pct(m.member_weight_mapped, 2)} of index weight` : "—"} sub={m ? `${m.mapped}/${m.names} names mapped · ${m.unmapped.length} in review queue` : ""} />
      <HealthCell title="Data snapshot" ok={!!health.snapshot} detail={health.snapshot ? health.snapshot.slice(0, 16) : "none"} sub={health.last_job ? `last pull job ${health.last_job.status}` : "runs pin snapshots by hash"} href="/data" />
    </div>
  );
}

function HealthCell({ title, ok, detail, sub, href = "/data" }: { title: string; ok: boolean; detail: string; sub?: string; href?: string }) {
  return (
    <Link href={href} className="flex items-start gap-3 bg-surface px-4 py-3 hover:bg-raised">
      <Database className={`mt-0.5 h-4 w-4 ${ok ? "text-good" : "text-serious"}`} />
      <div className="min-w-0">
        <div className="text-[11px] uppercase tracking-wide text-ink-3">
          {title} · <span className={ok ? "text-good" : "text-serious"}>{ok ? "OK" : "attention"}</span>
        </div>
        <div className="num truncate text-sm text-ink">{detail}</div>
        {sub && <div className="truncate text-[11px] text-ink-3">{sub}</div>}
      </div>
    </Link>
  );
}
