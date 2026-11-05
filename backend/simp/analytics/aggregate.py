"""Notional-weighted TCA aggregates over stored ``tca_order`` / ``reprice`` rows.

Methods: ACTUAL (as executed by the run's model) or a repricing (T0, MOC, VWAP, ARRIVAL,
HANDOFF). Metrics for ACTUAL: is, exec (execution + explicit), delay, explicit,
slip_benchmark, slip_close, slip_vwap, slip_arrival. Repricings report cost vs decision
(``vs_decision``) or vs the T0 close on the same orders (``vs_close``).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl

from simp.analytics.weighted import AUC_LABELS, SIZE_LABELS, block_bootstrap_ci, wmean, wquantile

ACTUAL_METRICS = {
    "is": "is_bps", "exec": "exec_cost_bps", "delay": "delay_bps", "explicit": "explicit_bps",
    "execution": "execution_bps", "opportunity": "opportunity_bps", "slip_benchmark": "slip_benchmark_bps",
    "slip_close": "slip_close_bps", "slip_vwap": "slip_vwap_bps", "slip_arrival": "slip_arrival_bps",
}
METHODS = ["ACTUAL", "T0", "MOC", "VWAP", "ARRIVAL", "HANDOFF"]
GROUPS = {"date", "size_bucket", "auction_bucket", "main_strategy", "main_broker", "main_venue", "reason", "side", "instruction"}


def cost_frame(tca: pl.DataFrame, rep: pl.DataFrame, method: str, metric: str) -> pl.DataFrame:
    """One cost column ``c`` (bps) and weight ``w`` (executed notional) per order."""
    if method == "ACTUAL":
        col = ACTUAL_METRICS.get(metric, "exec_cost_bps")
        return tca.with_columns(pl.col(col).alias("c"), pl.col("exec_notional").alias("w"))
    m = method.lower()
    j = tca.join(rep.select(list(dict.fromkeys(["pm_order_id", f"{m}_bps", "t0_bps"]))), on="pm_order_id", how="inner")
    c = pl.col(f"{m}_bps") - pl.col("t0_bps") if metric == "vs_close" else pl.col(f"{m}_bps")
    return j.with_columns(c.alias("c"), pl.col("notional").alias("w"))


def summarise(df: pl.DataFrame, *, n_boot: int = 2000) -> dict[str, Any]:
    if df.height == 0:
        return {"orders": 0, "notional": 0.0, "cost_bps": None, "cost_usd": 0.0, "ci90": [None, None],
                "eq_weighted_bps": None, "p50": None, "p90": None}
    c, w = df["c"].to_numpy().astype(float), df["w"].to_numpy().astype(float)
    blocks = df["date"].cast(pl.Utf8).to_numpy()
    lo, hi = block_bootstrap_ci(c, w, blocks, n_boot=n_boot)
    m = np.isfinite(c) & np.isfinite(w)
    q = wquantile(c, w, [0.5, 0.9])
    return {
        "orders": int(df.height), "notional": float(np.nansum(w)), "cost_bps": wmean(c, w),
        "cost_usd": float(np.nansum(c[m] * w[m]) * 1e-4), "ci90": [lo, hi],
        "eq_weighted_bps": float(np.nanmean(c)) if m.any() else None, "p50": float(q[0]), "p90": float(q[1]),
    }


def group(df: pl.DataFrame, by: list[str], *, n_boot: int = 2000) -> list[dict[str, Any]]:
    by = [b for b in by if b in GROUPS]
    if not by:
        return [{"key": {}, **summarise(df, n_boot=n_boot)}]
    total = float(df["w"].sum()) or 1.0
    out = []
    for keys, part in df.group_by(by, maintain_order=False):
        s = summarise(part, n_boot=n_boot)
        s["notional_share"] = s["notional"] / total
        out.append({"key": {k: (str(v) if v is not None else None) for k, v in zip(by, keys, strict=True)}, **s})
    order = {"size_bucket": SIZE_LABELS + ["n/a"], "auction_bucket": AUC_LABELS + ["n/a"]}

    def sk(r: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(order[b].index(r["key"][b]) if b in order and r["key"][b] in order[b] else str(r["key"][b]) for b in by)

    return sorted(out, key=sk)


def heatmap(df: pl.DataFrame) -> dict[str, Any]:
    cells = []
    for (sb, ab), part in df.group_by(["size_bucket", "auction_bucket"]):
        c, w = part["c"].to_numpy().astype(float), part["w"].to_numpy().astype(float)
        cells.append({"size_bucket": sb, "auction_bucket": ab, "cost_bps": wmean(c, w), "orders": part.height,
                      "notional": float(np.nansum(w))})
    return {"x": AUC_LABELS + ["n/a"], "y": SIZE_LABELS + ["n/a"], "cells": cells}


def method_comparison(tca: pl.DataFrame, rep: pl.DataFrame, metric: str) -> list[dict[str, Any]]:
    out = []
    for m in METHODS:
        df = cost_frame(tca, rep, m, "exec" if m == "ACTUAL" and metric in ("vs_close", "vs_decision") else metric)
        if m == "ACTUAL" and metric == "vs_decision":
            df = cost_frame(tca, rep, m, "is")
        out.append({"method": m, **summarise(df, n_boot=500)})
    return out


def outliers(tca: pl.DataFrame, rep: pl.DataFrame, n: int = 20) -> list[dict[str, Any]]:
    """Orders where the repricing methods disagree most (spread of MOC/VWAP/ARRIVAL), by $."""
    j = tca.select("pm_order_id", "date", "ticker", "side", "reason", "exec_notional", "pct_adv", "pct_auction").join(
        rep.select("pm_order_id", "moc_bps", "vwap_bps", "arrival_bps", "t0_bps", "notional"), on="pm_order_id")
    j = j.with_columns(
        (pl.max_horizontal("moc_bps", "vwap_bps", "arrival_bps") - pl.min_horizontal("moc_bps", "vwap_bps", "arrival_bps")).alias("spread_bps")
    ).with_columns((pl.col("spread_bps") * pl.col("notional") * 1e-4).alias("spread_usd"))
    return j.sort("spread_usd", descending=True).head(n).with_columns(pl.col("date").cast(pl.Utf8)).to_dicts()


def scatter(df: pl.DataFrame, x: str = "pct_auction", limit: int = 4000) -> list[dict[str, Any]]:
    d = df.filter(pl.col(x).is_finite() & pl.col("c").is_finite()).sort("w", descending=True).head(limit)
    return d.select(pl.col(x).alias("x"), pl.col("c").alias("y"), pl.col("w").alias("notional"), "ticker", "side",
                    "pm_order_id", pl.col("date").cast(pl.Utf8)).to_dicts()


def fallback_report(tca: pl.DataFrame) -> dict[str, Any]:
    tot = float(tca["exec_notional"].sum() or 0.0)
    out: dict[str, Any] = {"total_notional": tot, "flags": {}}
    if tca.height == 0:
        out["provisional"] = False
        return out
    ex = tca.select("exec_notional", pl.col("flags")).explode("flags").drop_nulls("flags")
    fb = 0.0
    for (flag,), part in ex.group_by(["flags"]):
        n = float(part["exec_notional"].sum())
        out["flags"][flag] = {"orders": part.height, "notional": n, "share": n / tot if tot else 0.0}
        if "FALLBACK" in str(flag):
            fb += n
    out["fallback_share"] = fb / tot if tot else 0.0
    out["provisional"] = out["fallback_share"] > 0.01
    return out
