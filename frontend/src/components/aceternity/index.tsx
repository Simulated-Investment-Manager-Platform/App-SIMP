"use client";

/* Showcase components in the Aceternity UI style (copy-in, built on `motion`).
 * Rule: animate navigation and arrival, never numbers people are reading.
 * Every effect honours prefers-reduced-motion. */
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Check, Loader2, XCircle } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { cn } from "@/lib/cn";

/* ---------------------------------------------------------------- Sidebar (animated, collapsible) */
export type NavItem = { href: string; label: string; icon: React.ReactNode };

export function Sidebar({ items, active, footer }: { items: NavItem[]; active: string; footer?: React.ReactNode }) {
  const [open, setOpen] = React.useState(false);
  const reduce = useReducedMotion();
  return (
    <motion.aside
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      animate={{ width: open ? 220 : 60 }}
      transition={reduce ? { duration: 0 } : { type: "spring", stiffness: 300, damping: 32 }}
      className="sticky top-0 z-30 hidden h-screen shrink-0 flex-col border-r border-line bg-surface py-3 md:flex"
    >
      <Link href="/" className="mb-4 flex h-9 items-center gap-2.5 px-[18px]">
        <span className="grid h-6 w-6 shrink-0 place-items-center rounded-md bg-accent text-[11px] font-bold text-white">S</span>
        <AnimatePresence>
          {open && (
            <motion.span initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="whitespace-nowrap text-sm font-semibold">
              SIMP
            </motion.span>
          )}
        </AnimatePresence>
      </Link>
      <nav className="flex flex-1 flex-col gap-0.5 px-2">
        {items.map((it) => {
          const on = it.href === "/" ? active === "/" : active.startsWith(it.href);
          return (
            <Link
              key={it.href}
              href={it.href}
              aria-label={it.label}
              title={it.label}
              className={cn(
                "relative flex h-9 items-center gap-3 rounded-md px-[10px] text-sm text-ink-2 transition-colors hover:text-ink",
                on && "text-ink",
              )}
            >
              {on && (
                <motion.span layoutId="nav-active" className="absolute inset-0 rounded-md bg-line/60" transition={reduce ? { duration: 0 } : undefined} />
              )}
              <span className="relative z-10 shrink-0 [&>svg]:h-[18px] [&>svg]:w-[18px]">{it.icon}</span>
              <AnimatePresence>
                {open && (
                  <motion.span initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }} className="relative z-10 whitespace-nowrap">
                    {it.label}
                  </motion.span>
                )}
              </AnimatePresence>
            </Link>
          );
        })}
      </nav>
      <div className="px-2">{footer}</div>
    </motion.aside>
  );
}

export function MobileNav({ items, active }: { items: NavItem[]; active: string }) {
  return (
    <nav className="sticky top-0 z-30 flex gap-1 overflow-x-auto border-b border-line bg-surface px-3 py-2 md:hidden">
      {items.map((it) => (
        <Link
          key={it.href}
          href={it.href}
          className={cn(
            "flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs text-ink-2 [&>svg]:h-4 [&>svg]:w-4",
            (it.href === "/" ? active === "/" : active.startsWith(it.href)) && "bg-line/60 text-ink",
          )}
        >
          {it.icon}
          {it.label}
        </Link>
      ))}
    </nav>
  );
}

/* ---------------------------------------------------------------- Spotlight + Background Beams (overview header only) */
export function Spotlight({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden
      className={cn("pointer-events-none absolute -top-40 left-0 h-[169%] w-[138%] animate-spotlight opacity-0 lg:w-[84%]", className)}
      viewBox="0 0 3787 2842"
      fill="none"
    >
      <g filter="url(#spot)">
        <ellipse cx="1924.71" cy="273.501" rx="1924.71" ry="273.501" transform="matrix(-0.822377 -0.568943 -0.568943 0.822377 3631.88 2291.09)" fill="rgb(var(--accent))" fillOpacity="0.16" />
      </g>
      <defs>
        <filter id="spot" x="0.860352" y="0.838989" width="3785.16" height="2840.26" filterUnits="userSpaceOnUse" colorInterpolationFilters="sRGB">
          <feFlood floodOpacity="0" result="BackgroundImageFix" />
          <feBlend mode="normal" in="SourceGraphic" in2="BackgroundImageFix" result="shape" />
          <feGaussianBlur stdDeviation="151" result="effect1_foregroundBlur" />
        </filter>
      </defs>
    </svg>
  );
}

export function BackgroundBeams({ className }: { className?: string }) {
  const reduce = useReducedMotion();
  const paths = Array.from({ length: 14 }, (_, i) => `M${-380 + i * 22} ${-189 - i * 8}C${-380 + i * 22} ${-189 - i * 8} ${-312 + i * 22} ${216 - i * 8} ${152 + i * 22} ${343 - i * 8}C${616 + i * 22} ${470 - i * 8} ${684 + i * 22} ${875 - i * 8} ${684 + i * 22} ${875 - i * 8}`);
  return (
    <div aria-hidden className={cn("pointer-events-none absolute inset-0 overflow-hidden [mask-image:radial-gradient(ellipse_at_top,white,transparent_70%)]", className)}>
      <svg className="absolute h-full w-full" viewBox="0 0 696 316" fill="none" preserveAspectRatio="none">
        {paths.map((d, i) => (
          <motion.path
            key={i}
            d={d}
            stroke="rgb(var(--accent))"
            strokeOpacity={0.12}
            strokeWidth={0.6}
            initial={{ pathLength: 0 }}
            animate={{ pathLength: 1 }}
            transition={reduce ? { duration: 0 } : { duration: 3 + (i % 5), ease: "easeInOut", delay: i * 0.08 }}
          />
        ))}
      </svg>
    </div>
  );
}

/* ---------------------------------------------------------------- Bento grid + hover-effect cards */
export function BentoGrid({ className, children }: { className?: string; children: React.ReactNode }) {
  return <div className={cn("grid auto-rows-[minmax(11rem,auto)] grid-cols-1 gap-3 md:grid-cols-3", className)}>{children}</div>;
}

export function BentoItem({ className, children, href }: { className?: string; children: React.ReactNode; href?: string }) {
  const [hover, setHover] = React.useState(false);
  const reduce = useReducedMotion();
  const body = (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      className={cn("group relative h-full overflow-hidden rounded-xl border border-line bg-surface p-4 transition-colors", href && "cursor-pointer hover:border-accent/50", className)}
    >
      <AnimatePresence>
        {hover && href && (
          <motion.span
            className="pointer-events-none absolute inset-0 bg-accent/[0.05]"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1, transition: { duration: reduce ? 0 : 0.15 } }}
            exit={{ opacity: 0, transition: { duration: reduce ? 0 : 0.15 } }}
          />
        )}
      </AnimatePresence>
      <div className="relative h-full">{children}</div>
    </div>
  );
  return href ? (
    <Link href={href} className={cn("block", className?.match(/(md:)?(col|row)-span-\S+/g)?.join(" "))}>
      {body}
    </Link>
  ) : (
    body
  );
}

/** Card Hover Effect: a list of cards where the hover highlight glides between items. */
export function HoverCards<T>({ items, render, href, keyOf, className }: { items: T[]; render: (t: T) => React.ReactNode; href: (t: T) => string; keyOf: (t: T) => string; className?: string }) {
  const [hovered, setHovered] = React.useState<number | null>(null);
  const reduce = useReducedMotion();
  return (
    <div className={cn("grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3", className)}>
      {items.map((it, i) => (
        <Link key={keyOf(it)} href={href(it)} className="relative block p-1" onMouseEnter={() => setHovered(i)} onMouseLeave={() => setHovered(null)}>
          <AnimatePresence>
            {hovered === i && (
              <motion.span
                layoutId="hover-card"
                className="absolute inset-0 block rounded-xl bg-line/50"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1, transition: { duration: reduce ? 0 : 0.15 } }}
                exit={{ opacity: 0, transition: { duration: reduce ? 0 : 0.15, delay: 0.1 } }}
              />
            )}
          </AnimatePresence>
          <div className="relative z-10 h-full rounded-lg border border-line bg-surface p-3">{render(it)}</div>
        </Link>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- Multi Step Loader (run stages, driven by SSE) */
export type StepState = "pending" | "active" | "done" | "failed";

export function MultiStepLoader({ steps }: { steps: { label: string; detail?: React.ReactNode; state: StepState }[] }) {
  const reduce = useReducedMotion();
  return (
    <ol className="space-y-2">
      {steps.map((s, i) => (
        <motion.li
          key={s.label}
          initial={reduce ? false : { opacity: 0, y: -4 }}
          animate={{ opacity: s.state === "pending" ? 0.45 : 1, y: 0 }}
          transition={{ delay: reduce ? 0 : i * 0.05 }}
          className="flex items-start gap-3"
        >
          <span
            className={cn(
              "mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full border",
              s.state === "done" && "border-good bg-good/15 text-good",
              s.state === "active" && "border-warn text-warn",
              s.state === "failed" && "border-critical bg-critical/15 text-critical",
              s.state === "pending" && "border-line text-ink-3",
            )}
          >
            {s.state === "done" && <Check className="h-3 w-3" />}
            {s.state === "active" && <Loader2 className="h-3 w-3 animate-spin" />}
            {s.state === "failed" && <XCircle className="h-3 w-3" />}
            {s.state === "pending" && <span className="h-1.5 w-1.5 rounded-full bg-ink-3" />}
          </span>
          <div className="min-w-0">
            <div className={cn("text-sm", s.state === "active" ? "font-semibold text-ink" : "text-ink-2")}>{s.label}</div>
            {s.detail && <div className="mt-0.5 text-xs text-ink-3">{s.detail}</div>}
          </div>
        </motion.li>
      ))}
    </ol>
  );
}

/* ---------------------------------------------------------------- Timeline (order lineage) */
export function Timeline({ items }: { items: { key: string; time: React.ReactNode; title: React.ReactNode; body?: React.ReactNode; tone?: string }[] }) {
  const reduce = useReducedMotion();
  return (
    <div className="relative pl-6">
      <div className="absolute bottom-1 left-[7px] top-1 w-px bg-line" />
      <motion.div
        className="absolute left-[7px] top-1 w-px bg-accent"
        initial={{ height: 0 }}
        animate={{ height: "calc(100% - 8px)" }}
        transition={reduce ? { duration: 0 } : { duration: 0.9, ease: "easeOut" }}
      />
      <ul className="space-y-3">
        {items.map((it) => (
          <li key={it.key} className="relative">
            <span className={cn("absolute -left-[22px] top-1 h-2.5 w-2.5 rounded-full border-2 border-page", it.tone ?? "bg-accent")} />
            <div className="num text-[11px] text-ink-3">{it.time}</div>
            <div className="text-sm text-ink">{it.title}</div>
            {it.body && <div className="mt-0.5 text-xs text-ink-2">{it.body}</div>}
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ---------------------------------------------------------------- Animated tabs */
export function AnimatedTabs<T extends string>({ tabs, value, onChange }: { tabs: { value: T; label: string }[]; value: T; onChange: (v: T) => void }) {
  const reduce = useReducedMotion();
  return (
    <div className="flex gap-1 overflow-x-auto border-b border-line" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.value}
          role="tab"
          aria-selected={value === t.value}
          onClick={() => onChange(t.value)}
          className={cn("relative px-3 py-2 text-sm transition-colors", value === t.value ? "text-ink" : "text-ink-3 hover:text-ink-2")}
        >
          {t.label}
          {value === t.value && (
            <motion.span layoutId="tab-underline" className="absolute inset-x-1 -bottom-px h-0.5 rounded bg-accent" transition={reduce ? { duration: 0 } : undefined} />
          )}
        </button>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- Moving-border button (the one primary action) */
export function MovingBorderButton({ children, className, ...p }: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const reduce = useReducedMotion();
  return (
    <button
      className={cn("group relative inline-flex h-9 overflow-hidden rounded-md p-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 disabled:opacity-50", className)}
      {...p}
    >
      <motion.span
        aria-hidden
        className="absolute inset-[-200%] bg-[conic-gradient(from_90deg_at_50%_50%,transparent_0%,rgb(var(--accent))_20%,transparent_40%)]"
        animate={reduce ? undefined : { rotate: 360 }}
        transition={{ repeat: Infinity, duration: 3.5, ease: "linear" }}
      />
      <span className="relative inline-flex h-full w-full items-center gap-1.5 rounded-[5px] bg-accent px-4 text-sm font-semibold text-white">{children}</span>
    </button>
  );
}

/* ---------------------------------------------------------------- Placeholders & vanish input (global search) */
export function VanishInput({ placeholders, onSubmit, onChange, value }: { placeholders: string[]; onSubmit: (v: string) => void; onChange: (v: string) => void; value: string }) {
  const [idx, setIdx] = React.useState(0);
  const [vanish, setVanish] = React.useState(false);
  const reduce = useReducedMotion();
  React.useEffect(() => {
    if (reduce) return;
    const t = setInterval(() => setIdx((i) => (i + 1) % placeholders.length), 3000);
    return () => clearInterval(t);
  }, [placeholders.length, reduce]);
  return (
    <form
      className="relative h-9 w-full"
      onSubmit={(e) => {
        e.preventDefault();
        if (!value) return;
        setVanish(true);
        setTimeout(() => {
          onSubmit(value);
          setVanish(false);
        }, reduce ? 0 : 220);
      }}
    >
      <motion.input
        aria-label="Search ticker, PERMNO or order id"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        animate={vanish ? { opacity: 0, filter: "blur(4px)", x: 12 } : { opacity: 1, filter: "blur(0px)", x: 0 }}
        className="h-9 w-full rounded-full border border-line bg-raised px-4 text-sm text-ink focus:outline-none focus:ring-2 focus:ring-accent/50"
      />
      {!value && (
        <div className="pointer-events-none absolute inset-0 flex items-center px-4">
          <AnimatePresence mode="wait">
            <motion.span
              key={idx}
              initial={reduce ? false : { y: 6, opacity: 0 }}
              animate={{ y: 0, opacity: 1 }}
              exit={reduce ? undefined : { y: -6, opacity: 0 }}
              transition={{ duration: 0.25 }}
              className="truncate text-sm text-ink-3"
            >
              {placeholders[idx]}
            </motion.span>
          </AnimatePresence>
        </div>
      )}
    </form>
  );
}
