"use client";

/* Plain, data-dense base components (shadcn/ui-style, Radix where behaviour matters). */
import * as DialogP from "@radix-ui/react-dialog";
import * as TooltipP from "@radix-ui/react-tooltip";
import { AlertTriangle, CheckCircle2, CircleDashed, Info, Loader2, X, XCircle } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { STATUS_TONE, type Tone } from "@/lib/colors";
import { cn } from "@/lib/cn";

export function Button({
  variant = "default",
  size = "md",
  className,
  ...p
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "default" | "primary" | "ghost" | "danger"; size?: "sm" | "md" }) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
        size === "sm" ? "h-7 px-2.5 text-xs" : "h-9 px-3.5 text-sm",
        variant === "default" && "border border-line bg-raised text-ink hover:bg-line/50",
        variant === "primary" && "bg-accent text-white hover:bg-accent/90",
        variant === "ghost" && "text-ink-2 hover:bg-line/50 hover:text-ink",
        variant === "danger" && "border border-critical/40 text-critical hover:bg-critical/10",
        className,
      )}
      {...p}
    />
  );
}

export function Card({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-lg border border-line bg-surface", className)} {...p} />;
}

export function CardHeader({ title, subtitle, right, className }: { title: React.ReactNode; subtitle?: React.ReactNode; right?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("flex items-start justify-between gap-3 border-b border-line px-4 py-3", className)}>
      <div className="min-w-0">
        <h3 className="text-sm font-semibold text-ink">{title}</h3>
        {subtitle && <p className="mt-0.5 text-xs text-ink-3">{subtitle}</p>}
      </div>
      {right && <div className="flex shrink-0 items-center gap-2">{right}</div>}
    </div>
  );
}

const TONE: Record<Tone, string> = {
  good: "bg-good/15 text-good border-good/30",
  warn: "bg-warn/15 text-warn border-warn/30",
  serious: "bg-serious/15 text-serious border-serious/30",
  critical: "bg-critical/15 text-critical border-critical/30",
  neutral: "bg-line/50 text-ink-2 border-line",
};
const ICON: Record<Tone, React.ComponentType<{ className?: string }>> = {
  good: CheckCircle2,
  warn: Loader2,
  serious: AlertTriangle,
  critical: XCircle,
  neutral: CircleDashed,
};

export function Badge({ tone = "neutral", className, children }: { tone?: Tone; className?: string; children: React.ReactNode }) {
  return (
    <span className={cn("inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-medium leading-none", TONE[tone], className)}>
      {children}
    </span>
  );
}

/** Status always ships icon + label, never colour alone. */
export function Status({ value, className }: { value: string | null | undefined; className?: string }) {
  const v = value ?? "—";
  const tone = STATUS_TONE[v] ?? "neutral";
  const I = ICON[tone];
  return (
    <Badge tone={tone} className={className}>
      <I className={cn("h-3 w-3", tone === "warn" && v !== "PARTIAL" && "animate-spin")} />
      {v}
    </Badge>
  );
}

export function Side({ side }: { side: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1 text-xs font-semibold", side === "BUY" ? "text-buy" : "text-sell")}>
      <span aria-hidden className={cn("h-2 w-2 rounded-sm", side === "BUY" ? "bg-buy" : "bg-sell")} />
      {side}
    </span>
  );
}

export function Stat({ label, value, sub, className, hint }: { label: string; value: React.ReactNode; sub?: React.ReactNode; className?: string; hint?: string }) {
  return (
    <div className={cn("rounded-lg border border-line bg-surface px-4 py-3", className)} title={hint}>
      <div className="text-[11px] font-medium uppercase tracking-wide text-ink-3">{label}</div>
      <div className="mt-1 text-xl font-semibold text-ink">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-ink-2">{sub}</div>}
    </div>
  );
}

export function Input({ className, ...p }: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "h-9 w-full rounded-md border border-line bg-raised px-2.5 text-sm text-ink placeholder:text-ink-3 focus:outline-none focus:ring-2 focus:ring-accent/50",
        className,
      )}
      {...p}
    />
  );
}

export function Select({ className, children, ...p }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn("h-8 rounded-md border border-line bg-raised px-2 text-sm text-ink focus:outline-none focus:ring-2 focus:ring-accent/50", className)}
      {...p}
    >
      {children}
    </select>
  );
}

export function Field({ label, hint, children, className }: { label: string; hint?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <label className={cn("block", className)}>
      <span className="mb-1 block text-xs font-medium text-ink-2">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-ink-3">{hint}</span>}
    </label>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-md bg-line/60", className)} />;
}

/** Empty and error states say what is missing and offer the fix. */
export function Empty({ title, detail, action, href }: { title: string; detail?: React.ReactNode; action?: string; href?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-line px-6 py-10 text-center">
      <Info className="h-5 w-5 text-ink-3" />
      <div className="text-sm font-medium text-ink">{title}</div>
      {detail && <div className="max-w-lg text-xs text-ink-2">{detail}</div>}
      {action && href && (
        <Link href={href} className="mt-1 text-xs font-medium text-accent hover:underline">
          {action} →
        </Link>
      )}
    </div>
  );
}

export function ErrorBox({ error, title = "Something failed" }: { error: unknown; title?: string }) {
  const msg = error instanceof Error ? error.message : typeof error === "string" ? error : JSON.stringify(error);
  return (
    <div role="alert" className="flex items-start gap-2 rounded-lg border border-critical/40 bg-critical/10 px-4 py-3 text-sm">
      <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-critical" />
      <div>
        <div className="font-medium text-ink">{title}</div>
        <div className="mt-0.5 break-words font-mono text-xs text-ink-2">{msg}</div>
      </div>
    </div>
  );
}

export function Tip({ content, children }: { content: React.ReactNode; children: React.ReactNode }) {
  return (
    <TooltipP.Provider delayDuration={200}>
      <TooltipP.Root>
        <TooltipP.Trigger asChild>{children}</TooltipP.Trigger>
        <TooltipP.Portal>
          <TooltipP.Content sideOffset={6} className="z-50 max-w-xs rounded-md border border-line bg-raised px-2.5 py-1.5 text-xs text-ink-2 shadow-card">
            {content}
          </TooltipP.Content>
        </TooltipP.Portal>
      </TooltipP.Root>
    </TooltipP.Provider>
  );
}

export function Drawer({
  open,
  onOpenChange,
  title,
  subtitle,
  children,
  width = "max-w-4xl",
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  children: React.ReactNode;
  width?: string;
}) {
  return (
    <DialogP.Root open={open} onOpenChange={onOpenChange}>
      <DialogP.Portal>
        <DialogP.Overlay className="fixed inset-0 z-40 bg-black/50 data-[state=open]:animate-in" />
        <DialogP.Content
          className={cn("fixed inset-y-0 right-0 z-50 flex w-full flex-col border-l border-line bg-page shadow-2xl focus:outline-none", width)}
        >
          <div className="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
            <div>
              <DialogP.Title className="text-base font-semibold text-ink">{title}</DialogP.Title>
              {subtitle && <DialogP.Description className="mt-0.5 text-xs text-ink-3">{subtitle}</DialogP.Description>}
            </div>
            <DialogP.Close className="rounded p-1 text-ink-3 hover:bg-line/50 hover:text-ink" aria-label="Close">
              <X className="h-4 w-4" />
            </DialogP.Close>
          </div>
          <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
        </DialogP.Content>
      </DialogP.Portal>
    </DialogP.Root>
  );
}

/** Persistent label for every number that comes from the synthetic source. */
export function DataLabel({ label }: { label?: string | null }) {
  if (!label) return null;
  const synthetic = label === "SYNTHETIC";
  return (
    <Tip
      content={
        synthetic
          ? "Schema-identical synthetic CRSP/BMLL data. Not real market data. Switch to live WRDS + BMLL pulls on the Data page."
          : "Derived from licensed CRSP (WRDS) and BMLL data. Do not redistribute raw rows."
      }
    >
      <span>
        <Badge tone={synthetic ? "serious" : "good"} className="uppercase tracking-wide">
          {synthetic ? "Synthetic data" : label}
        </Badge>
      </span>
    </Tip>
  );
}

export function SectionTitle({ children, right }: { children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="mb-3 flex items-end justify-between gap-3">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-2">{children}</h2>
      {right}
    </div>
  );
}

export function KV({ items }: { items: [string, React.ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1.5 text-xs">
      {items.map(([k, v]) => (
        <React.Fragment key={k}>
          <dt className="text-ink-3">{k}</dt>
          <dd className="num min-w-0 break-words text-ink">{v}</dd>
        </React.Fragment>
      ))}
    </dl>
  );
}

export function Seg<T extends string>({ value, options, onChange, className }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; className?: string }) {
  return (
    <div className={cn("inline-flex rounded-md border border-line bg-raised p-0.5", className)} role="radiogroup">
      {options.map((o) => (
        <button
          key={o.value}
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            "rounded px-2.5 py-1 text-xs font-medium transition-colors",
            value === o.value ? "bg-accent text-white" : "text-ink-2 hover:text-ink",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Cost cell: bps with the diverging tint; positive = worse. */
export function CostCell({ v, max = 30 }: { v: number | null | undefined; max?: number }) {
  if (v === null || v === undefined || !Number.isFinite(v)) return <span className="text-ink-3">—</span>;
  const k = Math.max(-1, Math.min(1, v / max));
  return (
    <span
      className="num inline-block min-w-[3.5rem] rounded px-1.5 text-right"
      style={{ background: k > 0 ? `rgb(var(--critical) / ${Math.abs(k) * 0.35})` : `rgb(var(--accent) / ${Math.abs(k) * 0.35})` }}
    >
      {v > 0 ? "+" : v < 0 ? "−" : ""}
      {Math.abs(v).toFixed(1)}
    </span>
  );
}
