"use client";

/** Apache ECharts wrapper: themed from the design tokens, resizes with its container,
 * re-themes on light/dark switch. Charts never animate numbers people are reading. */
import type { EChartsOption } from "echarts";
import * as echarts from "echarts";
import { useEffect, useRef, useState } from "react";

import { currentTheme, INK, type Theme } from "@/lib/colors";
import { cn } from "@/lib/cn";

export function useTheme(): Theme {
  const [t, setT] = useState<Theme>("dark");
  useEffect(() => {
    setT(currentTheme());
    const obs = new MutationObserver(() => setT(currentTheme()));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);
  return t;
}

export function baseOption(t: Theme): EChartsOption {
  const ink = INK[t];
  return {
    animation: false,
    backgroundColor: "transparent",
    textStyle: { color: ink.secondary, fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif", fontSize: 11 },
    grid: { left: 56, right: 16, top: 28, bottom: 32, containLabel: false },
    tooltip: {
      backgroundColor: t === "dark" ? "#222220" : "#ffffff",
      borderColor: ink.grid,
      textStyle: { color: ink.primary, fontSize: 12 },
      extraCssText: "box-shadow: 0 8px 24px -12px rgba(0,0,0,.45); border-radius: 6px;",
    },
    legend: { textStyle: { color: ink.secondary }, icon: "roundRect", itemWidth: 10, itemHeight: 10, top: 0, right: 0 },
  };
}

export function axisStyle(t: Theme) {
  const ink = INK[t];
  return {
    axisLine: { lineStyle: { color: ink.axis } },
    axisTick: { show: false },
    axisLabel: { color: ink.muted, fontSize: 11 },
    splitLine: { lineStyle: { color: ink.grid, width: 1 } },
    nameTextStyle: { color: ink.muted, fontSize: 11 },
  };
}

export function EChart({
  option,
  className,
  height = 280,
  onClick,
  ariaLabel,
}: {
  option: (t: Theme) => EChartsOption;
  className?: string;
  height?: number;
  onClick?: (p: any) => void;
  ariaLabel: string;
}) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const t = useTheme();
  useEffect(() => {
    if (!el.current) return;
    chart.current = echarts.init(el.current, undefined, { renderer: "canvas" });
    const ro = new ResizeObserver(() => chart.current?.resize());
    ro.observe(el.current);
    return () => {
      ro.disconnect();
      chart.current?.dispose();
      chart.current = null;
    };
  }, []);
  useEffect(() => {
    if (!chart.current) return;
    const o = option(t);
    const b = baseOption(t);
    const merge = (k: "tooltip" | "legend" | "grid" | "textStyle"): any =>
      o[k] === undefined ? b[k] : { ...(b[k] as object), ...(o[k] as object) };
    // legends only where a chart asks for one (>= 2 series); a single series is named by its title
    const legend = o.legend === undefined ? { show: false } : { ...(b.legend as object), show: true, ...(o.legend as object) };
    chart.current.setOption({ ...b, ...o, tooltip: merge("tooltip"), legend, grid: merge("grid"), textStyle: merge("textStyle") }, true);
  }, [option, t]);
  useEffect(() => {
    if (!chart.current || !onClick) return;
    chart.current.on("click", onClick);
    return () => {
      chart.current?.off("click", onClick);
    };
  }, [onClick]);
  return <div ref={el} role="img" aria-label={ariaLabel} className={cn("w-full", className)} style={{ height }} />;
}
