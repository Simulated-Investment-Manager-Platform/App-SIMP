"use client";

import { useQuery } from "@tanstack/react-query";
import { BarChart3, Boxes, Database, FlaskConical, LayoutGrid, LineChart, ListTree, Moon, Play, Search, Settings, Sun } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import * as React from "react";

import { MobileNav, type NavItem, Sidebar, VanishInput } from "@/components/aceternity";
import { DataLabel } from "@/components/ui";
import { getJson } from "@/lib/api/client";
import { useMe } from "@/lib/hooks";

const NAV: NavItem[] = [
  { href: "/", label: "Overview", icon: <LayoutGrid /> },
  { href: "/funds/SP500_INDEX", label: "Fund", icon: <LineChart /> },
  { href: "/runs", label: "Runs", icon: <Play /> },
  { href: "/blotter", label: "Blotter", icon: <ListTree /> },
  { href: "/tca", label: "TCA", icon: <BarChart3 /> },
  { href: "/market", label: "Market explorer", icon: <Boxes /> },
  { href: "/studies", label: "Studies", icon: <FlaskConical /> },
  { href: "/data", label: "Data", icon: <Database /> },
  { href: "/settings", label: "Settings", icon: <Settings /> },
];

function ThemeToggle() {
  const [t, setT] = React.useState<"light" | "dark">("dark");
  React.useEffect(() => setT(document.documentElement.dataset.theme === "light" ? "light" : "dark"), []);
  const flip = () => {
    const n = t === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = n;
    try {
      localStorage.setItem("simp-theme", n);
    } catch {
      /* storage unavailable: theme still applies for this page view */
    }
    setT(n);
  };
  return (
    <button onClick={flip} aria-label="Toggle light/dark theme" className="rounded-md p-2 text-ink-2 hover:bg-line/50 hover:text-ink">
      {t === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
    </button>
  );
}

function GlobalSearch() {
  const [q, setQ] = React.useState("");
  const router = useRouter();
  const res = useQuery({ queryKey: ["search", q], enabled: q.length >= 1, queryFn: () => getJson("/search", { q }) });
  const [open, setOpen] = React.useState(false);
  const go = (v: string) => {
    const s = res.data?.securities?.[0];
    const o = res.data?.orders?.[0];
    const r = res.data?.runs?.[0];
    if (r) router.push(`/runs/${r.run_id}`);
    else if (o) router.push(`/blotter?run=${o.run_id}&order=${o.pm_order_id}`);
    else if (s) router.push(`/market?permno=${s.permno}`);
    else if (/^\d+$/.test(v)) router.push(`/market?permno=${v}`);
    setQ("");
    setOpen(false);
  };
  const hits = [
    ...(res.data?.runs ?? []).map((r: any) => ({ k: `r${r.run_id}`, href: `/runs/${r.run_id}`, label: `Run ${r.run_id}`, sub: r.name })),
    ...(res.data?.orders ?? []).map((o: any) => ({ k: `o${o.pm_order_id}`, href: `/blotter?run=${o.run_id}&order=${o.pm_order_id}`, label: o.pm_order_id, sub: `${o.side} ${o.reason} ${o.exec_date}` })),
    ...(res.data?.securities ?? []).map((s: any) => ({ k: `s${s.permno}`, href: `/market?permno=${s.permno}`, label: s.ticker, sub: `PERMNO ${s.permno}` })),
  ];
  return (
    <div className="relative w-full max-w-md" onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)}>
      <Search className="pointer-events-none absolute left-3 top-2.5 z-10 h-4 w-4 text-ink-3" />
      <div className="[&_input]:pl-9 [&_span]:pl-5">
        <VanishInput value={q} onChange={setQ} onSubmit={go} placeholders={["Search a ticker…", "…a PERMNO like 10001", "…a PM order id", "…a run id"]} />
      </div>
      {open && q && hits.length > 0 && (
        <div className="absolute left-0 right-0 top-11 z-50 overflow-hidden rounded-lg border border-line bg-raised shadow-card">
          {hits.slice(0, 12).map((h) => (
            <Link key={h.k} href={h.href} className="flex items-baseline justify-between gap-3 px-3 py-2 text-sm hover:bg-line/50" onClick={() => setQ("")}>
              <span className="truncate font-medium text-ink">{h.label}</span>
              <span className="truncate text-xs text-ink-3">{h.sub}</span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname() ?? "/";
  const me = useMe();
  const cov = useQuery({ queryKey: ["coverage-label"], queryFn: () => getJson("/data/coverage"), staleTime: 120_000 });
  return (
    <div className="flex min-h-screen">
      <Sidebar items={NAV} active={path} />
      <div className="flex min-w-0 flex-1 flex-col">
        <MobileNav items={NAV} active={path} />
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-line bg-page/85 px-4 backdrop-blur md:px-6">
          <GlobalSearch />
          <div className="ml-auto flex items-center gap-2">
            <DataLabel label={cov.data?.data_label} />
            <ThemeToggle />
            {me.data && (
              <Link href="/settings" className="flex items-center gap-2 rounded-md px-2 py-1 text-xs text-ink-2 hover:bg-line/50">
                <span className="grid h-6 w-6 place-items-center rounded-full bg-line text-[11px] font-semibold uppercase text-ink">{me.data.username[0]}</span>
                <span className="hidden sm:inline">
                  {me.data.username} · {me.data.role}
                </span>
              </Link>
            )}
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1500px] flex-1 px-4 py-6 md:px-6">{children}</main>
      </div>
    </div>
  );
}
