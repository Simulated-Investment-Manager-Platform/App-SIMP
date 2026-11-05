"""Fund vs benchmark (spec: Analytics -> Fund vs benchmark)."""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import numpy as np

from simp.analytics.weighted import concentration_curve


def ann_return(r: np.ndarray) -> float:
    r = r[np.isfinite(r)]
    if r.size == 0:
        return float("nan")
    return float(np.prod(1 + r) ** (252 / r.size) - 1)


def max_drawdown(level: np.ndarray) -> float:
    peak = np.maximum.accumulate(level)
    return float(np.min(level / peak - 1))


def fund_report(nav: list[dict[str, Any]], start: date, benchmark: str = "CRSP_VWRETD") -> dict[str, Any]:
    rows = [r for r in nav if r["date"] >= start]
    if len(rows) < 2:
        return {"available": False, "reason": "fewer than two sessions in window"}
    rf = np.array([r["fund_return"] for r in rows], float)
    rg = np.array([r["gross_return"] for r in rows], float)
    ra = np.array([r["bench_return_tr"] for r in rows], float)
    rb_raw = [r.get("bench_return_official") for r in rows]
    rb = np.array([x if x is not None else np.nan for x in rb_raw], float)
    rbx = np.array([r["bench_return_pr"] for r in rows], float)
    bench = rb if benchmark == "SP500_TR" and np.isfinite(rb).all() else ra
    bench_label = (
        "S&P 500 TR (official, vendor)"
        if bench is rb
        else "CRSP S&P 500 cap-weighted with dividends (vwretd)"
    )
    un = np.array([r["unit_nav"] for r in rows], float)
    active = rf - bench
    cashw = np.array([r["cash_weight"] for r in rows], float)
    vol = float(np.std(rf, ddof=1) * math.sqrt(252))
    beta = float(np.cov(rf, bench)[0, 1] / np.var(bench, ddof=1)) if np.var(bench) > 0 else float("nan")
    avg_nav = float(np.mean([r["nav"] for r in rows]))
    years = len(rows) / 252
    worst = np.argsort(rf)[:10]
    cash_drag = float(-np.mean(np.r_[cashw[0], cashw[:-1]] * bench) * 252)
    costs_usd = float(sum(r["trading_cost_usd"] or 0 for r in rows))
    fees_usd = float(sum(r["fees_usd"] or 0 for r in rows))
    return {
        "available": True,
        "window": {"start": str(rows[0]["date"]), "end": str(rows[-1]["date"]), "sessions": len(rows)},
        "benchmark": benchmark,
        "benchmark_label": bench_label,
        "annual_return": ann_return(rf),
        "annual_return_gross": ann_return(rg),
        "benchmark_annual_return": ann_return(bench),
        "td_net_bps": (ann_return(rf) - ann_return(bench)) * 1e4,
        "td_gross_bps": (ann_return(rg) - ann_return(bench)) * 1e4,
        "te_bps": float(np.std(active, ddof=1) * math.sqrt(252) * 1e4),
        "volatility": vol,
        "max_drawdown": max_drawdown(un),
        "sharpe_rf0": ann_return(rf) / vol if vol else float("nan"),
        "beta": beta,
        "cum_return": float(un[-1] / (un[0] / (1 + rf[0])) - 1),
        "ending_nav": float(rows[-1]["nav"]),
        "ending_unit_nav": float(un[-1]),
        "worst_days": [
            {"date": str(rows[k]["date"]), "fund_return": float(rf[k]), "bench_return": float(bench[k])}
            for k in worst
        ],
        "cash_drag_bps": cash_drag * 1e4,
        "avg_cash_weight": float(cashw.mean()),
        "explicit_costs_bps_pa": costs_usd / avg_nav / years * 1e4,
        "fees_bps_pa": fees_usd / avg_nav / years * 1e4,
        "proxy_gap_bps": (ann_return(ra) - ann_return(rb)) * 1e4 if np.isfinite(rb).all() else None,
        "price_vs_total_bps": (ann_return(ra) - ann_return(rbx)) * 1e4,
        "flows_net_usd": float(sum(r["flow_usd"] or 0 for r in rows)),
        "flow_days": int(sum(1 for r in rows if (r["flow_usd"] or 0) != 0)),
    }


def concentration_by_date(
    tca: list[dict[str, Any]], reasons: tuple[str, ...] = ("INDEX_REBALANCE", "INDEX_ADD", "INDEX_DELETE")
) -> list[dict[str, Any]]:
    by: dict[date, list[float]] = {}
    for r in tca:
        if r["reason"] in reasons and r["exec_notional"]:
            by.setdefault(r["date"], []).append(float(r["exec_notional"]))
    out = []
    for d, ns in sorted(by.items()):
        if len(ns) < 10:
            continue
        xs, ys, top10 = concentration_curve(np.array(ns))
        out.append(
            {
                "date": str(d),
                "orders": len(ns),
                "notional": float(sum(ns)),
                "top10_share": top10,
                "curve": [[float(a), float(b)] for a, b in zip(xs, ys, strict=True)],
            }
        )
    return out
