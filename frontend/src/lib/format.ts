/** Number formatting. Costs are always bps with $ beside them; positive cost = worse. */
export const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function usd(v: unknown, digits = 1): string {
  if (!isNum(v)) return "—";
  const a = Math.abs(v);
  const s = v < 0 ? "−" : "";
  if (a >= 1e9) return `${s}$${(a / 1e9).toFixed(2)}bn`;
  if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(digits)}m`;
  if (a >= 1e3) return `${s}$${(a / 1e3).toFixed(0)}k`;
  return `${s}$${a.toFixed(0)}`;
}

export function bps(v: unknown, digits = 1): string {
  if (!isNum(v)) return "—";
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(digits)}`;
}

export function pct(v: unknown, digits = 2): string {
  return isNum(v) ? `${(v * 100).toFixed(digits)}%` : "—";
}

export function num(v: unknown, digits = 0): string {
  return isNum(v) ? v.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits }) : "—";
}

export function px(v: unknown): string {
  return isNum(v) ? v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 }) : "—";
}

const NY: Intl.DateTimeFormatOptions = { timeZone: "America/New_York", hour12: false };

export function dt(v: unknown): string {
  if (!v) return "—";
  const d = new Date(String(v));
  return `${d.toLocaleDateString("en-CA", NY)} ${d.toLocaleTimeString("en-GB", NY)} ET`;
}

export function clock(v: unknown): string {
  if (!v) return "—";
  return new Date(String(v)).toLocaleTimeString("en-GB", NY);
}

export function day(v: unknown): string {
  return v ? String(v).slice(0, 10) : "—";
}

export function short(id: unknown, n = 10): string {
  return id ? String(id).slice(0, n) : "—";
}

export function ago(v: unknown): string {
  if (!v) return "—";
  const s = (Date.now() - new Date(String(v)).getTime()) / 1000;
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}
