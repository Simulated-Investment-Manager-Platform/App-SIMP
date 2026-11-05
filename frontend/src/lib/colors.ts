/** Validated reference palette: categorical order (fixed, never cycled), a blue<->red diverging
 * cost scale with a neutral grey midpoint, and reserved status colours. */

export const CATEGORICAL = {
  light: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
  dark: ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
};

export const INK = {
  light: { primary: "#0b0b0b", secondary: "#52514e", muted: "#898781", grid: "#e1e0d9", axis: "#c3c2b7", surface: "#fcfcfb" },
  dark: { primary: "#f0efec", secondary: "#c3c2b7", muted: "#898781", grid: "#2c2c2a", axis: "#383835", surface: "#1a1a19" },
};

const DIV = {
  light: { neg: "#2a78d6", mid: "#f0efec", pos: "#e34948" },
  dark: { neg: "#3987e5", mid: "#383835", pos: "#e66767" },
};

export type Theme = "light" | "dark";

export function currentTheme(): Theme {
  if (typeof document === "undefined") return "dark";
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

export function series(i: number, t: Theme = currentTheme()): string {
  return CATEGORICAL[t][i % 8];
}

export const BUY = (t: Theme = currentTheme()) => CATEGORICAL[t][0];
export const SELL = (t: Theme = currentTheme()) => CATEGORICAL[t][1];

function rgb(h: string): [number, number, number] {
  const x = h.replace("#", "");
  return [parseInt(x.slice(0, 2), 16), parseInt(x.slice(2, 4), 16), parseInt(x.slice(4, 6), 16)];
}

export function mix(a: string, b: string, t: number): string {
  const x = rgb(a);
  const y = rgb(b);
  return `rgb(${x.map((v, i) => Math.round(v + (y[i] - v) * t)).join(",")})`;
}

/** Cost colour: negative = saving (blue arm), positive = cost (red arm), 0 = neutral grey. */
export function costColor(v: number | null | undefined, max = 30, t: Theme = currentTheme()): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "transparent";
  const k = Math.max(-1, Math.min(1, v / max));
  const d = DIV[t];
  return mix(d.mid, k > 0 ? d.pos : d.neg, Math.abs(k));
}

export function divergingStops(t: Theme = currentTheme()): string[] {
  const d = DIV[t];
  return [d.neg, mix(d.neg, d.mid, 0.5), d.mid, mix(d.mid, d.pos, 0.5), d.pos];
}

export type Tone = "good" | "warn" | "serious" | "critical" | "neutral";

export const STATUS_TONE: Record<string, Tone> = {
  DONE: "good",
  RUNNING: "warn",
  ANALYSING: "warn",
  PREPARING: "warn",
  QUEUED: "neutral",
  FAILED: "critical",
  OPEN: "serious",
  FIXED: "good",
  EXCLUDED: "neutral",
  APPROVED: "good",
  PENDING: "serious",
  UNSUPPORTED: "critical",
  REJECTED: "neutral",
  FILLED: "good",
  EXPIRED: "serious",
  CANCELLED: "neutral",
  PARTIAL: "warn",
  WORKING: "warn",
};
