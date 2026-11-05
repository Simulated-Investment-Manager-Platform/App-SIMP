"""TCA aggregates, heatmap, scatter, method comparison, outliers and the one-date study."""

from __future__ import annotations

import math
from datetime import date, time
from typing import Any

import numpy as np
import polars as pl
from fastapi import APIRouter, Depends, HTTPException, Query

from simp.analytics import aggregate as A
from simp.api.deps import User, current_user, one, rows
from simp.db.session import clean_json, get_engine

router = APIRouter(tags=["tca"])


def _frames(run_id: str, on: date | None, reason: str | None) -> tuple[pl.DataFrame, pl.DataFrame]:
    w, p = "run_id=:r", {"r": run_id}
    if on:
        w += " and date=:d"
        p["d"] = on
    reasons = [x for x in (reason or "").split(",") if x]
    with get_engine().connect() as c:
        from sqlalchemy import text

        t = c.execute(text(f"select * from tca_order where {w}"), p).mappings().all()  # noqa: S608
        r = c.execute(text(f"select * from reprice where {w}"), p).mappings().all()  # noqa: S608
    tca = pl.DataFrame([dict(x) for x in t], infer_schema_length=None) if t else pl.DataFrame()
    rep = pl.DataFrame([dict(x) for x in r], infer_schema_length=None) if r else pl.DataFrame({"pm_order_id": []})
    if tca.height:
        num = [c for c, d in tca.schema.items() if str(d).startswith("Decimal")]
        tca = tca.with_columns([pl.col(c).cast(pl.Float64) for c in num])
        if reasons:
            tca = tca.filter(pl.col("reason").is_in(reasons))
    if rep.height:
        num = [c for c, d in rep.schema.items() if str(d).startswith("Decimal")]
        rep = rep.with_columns([pl.col(c).cast(pl.Float64) for c in num])
    return tca, rep


def _td(run_id: str) -> dict[str, Any] | None:
    r = one("select summary from run where run_id=:r", r=run_id)
    f = ((r or {}).get("summary") or {}).get("fund") or {}
    if not f.get("available"):
        return None
    return {"td_net_bps": f["td_net_bps"], "td_gross_bps": f["td_gross_bps"], "te_bps": f["te_bps"], "benchmark": f["benchmark_label"]}


@router.get("/runs/{run_id}/tca")
def tca(run_id: str, group_by: str = "size_bucket", method: str = "ACTUAL", metric: str = "exec",
        on: date | None = Query(None, alias="date"), reason: str | None = None, _: User = Depends(current_user)) -> dict[str, Any]:
    if method not in A.METHODS:
        raise HTTPException(422, f"method must be one of {A.METHODS}")
    t, r = _frames(run_id, on, reason)
    label = (one("select data_label from run where run_id=:r", r=run_id) or {}).get("data_label")
    if t.height == 0:
        return {"method": method, "metric": metric, "weighting": "notional", "groups": [], "total": None,
                "fallbacks": {}, "td": _td(run_id), "data_label": label, "empty": "no TCA rows for this selection"}
    df = A.cost_frame(t, r, method, metric)
    by = [g for g in group_by.split(",") if g]
    return clean_json({"method": method, "metric": metric, "weighting": "notional", "groups": A.group(df, by),  # type: ignore[no-any-return]
                       "total": {"key": {}, **A.summarise(df)}, "fallbacks": A.fallback_report(t), "td": _td(run_id), "data_label": label})


@router.get("/runs/{run_id}/tca/heatmap")
def heatmap(run_id: str, method: str = "ACTUAL", metric: str = "exec", on: date | None = Query(None, alias="date"),
            reason: str | None = None, _: User = Depends(current_user)) -> dict[str, Any]:
    t, r = _frames(run_id, on, reason)
    if t.height == 0:
        return {"x": [], "y": [], "cells": []}
    return clean_json({**A.heatmap(A.cost_frame(t, r, method, metric)), "weighting": "notional"})  # type: ignore[no-any-return]


@router.get("/runs/{run_id}/tca/scatter")
def scatter(run_id: str, method: str = "ACTUAL", metric: str = "exec", x: str = "pct_auction", on: date | None = Query(None, alias="date"),
            reason: str | None = None, _: User = Depends(current_user)) -> list[dict[str, Any]]:
    t, r = _frames(run_id, on, reason)
    if t.height == 0:
        return []
    return clean_json(A.scatter(A.cost_frame(t, r, method, metric), x if x in ("pct_auction", "pct_adv", "pct_exp_auction") else "pct_auction"))  # type: ignore[no-any-return]


@router.get("/runs/{run_id}/tca/methods")
def methods(run_id: str, metric: str = "vs_close", on: date | None = Query(None, alias="date"), reason: str | None = None,
            _: User = Depends(current_user)) -> dict[str, Any]:
    t, r = _frames(run_id, on, reason)
    if t.height == 0:
        return {"methods": []}
    return clean_json({"metric": metric, "methods": A.method_comparison(t, r, metric)})  # type: ignore[no-any-return]


@router.get("/runs/{run_id}/tca/outliers")
def outliers(run_id: str, on: date | None = Query(None, alias="date"), n: int = 20, _: User = Depends(current_user)) -> list[dict[str, Any]]:
    t, r = _frames(run_id, on, None)
    if t.height == 0 or r.height == 0:
        return []
    return clean_json(A.outliers(t, r, n))  # type: ignore[no-any-return]


@router.get("/runs/{run_id}/tca/dates")
def tca_dates(run_id: str, _: User = Depends(current_user)) -> list[dict[str, Any]]:
    return rows("select date, count(*) orders, sum(exec_notional) notional, "
                "sum(case when reason in ('INDEX_REBALANCE','INDEX_ADD','INDEX_DELETE') then 1 else 0 end) rebalance_orders "
                "from tca_order where run_id=:r group by date order by date", r=run_id)


@router.get("/runs/{run_id}/tca/concentration")
def concentration(run_id: str, _: User = Depends(current_user)) -> list[dict[str, Any]]:
    r = one("select summary from run where run_id=:r", r=run_id)
    return list(((r or {}).get("summary") or {}).get("concentration") or [])


@router.get("/runs/{run_id}/netting")
def netting(run_id: str, _: User = Depends(current_user)) -> dict[str, Any]:
    r = one("select summary from run where run_id=:r", r=run_id)
    ev = rows("select date, sum(saved_notional) saved, sum(crossed_shares) shares, count(*) names from netting where run_id=:r "
              "group by date order by date", r=run_id)
    return {"summary": ((r or {}).get("summary") or {}).get("netting"), "by_date": ev}


@router.get("/runs/{run_id}/study/one-date")
def one_date(run_id: str, on: date = Query(date(2026, 3, 20), alias="date"), _: User = Depends(current_user)) -> dict[str, Any]:
    """Phase 3 deliverable: one row per PM order on one rebalance date — BMLL market figures, size
    against liquidity, three repricings, the handoff T0 fill, weighted totals, scatter, the
    orders where methods disagree most, and a T2 tick replay of the top ten by notional."""
    from simp.api.routers.market import market_data

    t, r = _frames(run_id, on, None)
    if t.height == 0:
        dates = rows("select distinct date from tca_order where run_id=:r and reason='INDEX_REBALANCE' order by date", r=run_id)
        raise HTTPException(404, {"message": f"no orders executed on {on} in this run", "rebalance_dates": [str(d["date"]) for d in dates]})
    md = market_data()
    j = t.join(r, on="pm_order_id", how="left", suffix="_r")
    pms = {x["pm_order_id"]: x for x in rows("select pm_order_id, decision_ts, quantity_adjustment_factor from pm_order where run_id=:r and exec_date=:d",
                                             r=run_id, d=on)}
    out = []
    i = md.cal.index(on) if md is not None and md.cal.is_session(on) else None
    for x in j.sort("exec_notional", descending=True).iter_rows(named=True):
        row: dict[str, Any] = {k: x.get(k) for k in ("pm_order_id", "ticker", "permno", "side", "quantity", "notional", "reason",
                                                    "decision_price", "exec_notional", "pct_adv", "pct_auction", "pct_exp_auction",
                                                    "spread_bps", "vol_intraday", "imbalance_side", "imbalance_notional", "vwap_day",
                                                    "close_price", "is_bps", "exec_cost_bps", "main_strategy",
                                                    "t0_bps", "moc_price", "moc_bps", "moc_vs_close_bps", "vwap_price", "vwap_bps",
                                                    "arrival_price_r", "arrival_bps", "handoff_price", "handoff_bps")}
        row["decision_ts"] = pms.get(x["pm_order_id"], {}).get("decision_ts")
        if md is not None and i is not None and x["permno"] in md.pos:
            jj = md.j(x["permno"])
            row.update(auction_price=float(md.auc_price[i, jj]), auction_volume=float(md.auc_vol[i, jj]),
                       auction_notional=float(md.auc_vol[i, jj] * md.prc[i, jj]), imbalance_qty=float(md.imbalance[i, jj]),
                       adv20=float(md.adv20[i, jj]), adv60=float(md.adv60[i, jj]))
        out.append(row)
    totals = []
    for m in ("T0", "MOC", "VWAP", "ARRIVAL", "HANDOFF", "ACTUAL"):
        df = A.cost_frame(t, r, m, "is" if m == "ACTUAL" else "vs_decision")
        s = A.summarise(df, n_boot=500)
        df2 = A.cost_frame(t, r, m, "exec" if m == "ACTUAL" else "vs_close")
        s2 = A.summarise(df2, n_boot=500)
        totals.append({"method": m, "vs_decision_bps": s["cost_bps"], "vs_decision_usd": s["cost_usd"], "vs_close_bps": s2["cost_bps"],
                       "vs_close_usd": s2["cost_usd"], "ci90_vs_close": s2["ci90"], "notional": s["notional"]})
    t2 = _t2_top10(run_id, on, out[:10]) if md is not None and i is not None else []
    largest_rebal = max((o for o in out if o["reason"] == "INDEX_REBALANCE"), key=lambda o: o["exec_notional"] or 0, default=None)
    largest_initial = one("select t.pm_order_id, t.ticker, t.exec_notional, t.date, t.exec_cost_bps, t.is_bps from tca_order t "
                          "where run_id=:r and reason='INITIAL' order by exec_notional desc limit 1", r=run_id)
    largest_any = one("select t.pm_order_id, t.ticker, t.exec_notional, t.date, t.exec_cost_bps from tca_order t where run_id=:r "
                      "and reason in ('INDEX_REBALANCE','INDEX_ADD','INDEX_DELETE') order by exec_notional desc limit 1", r=run_id)
    return clean_json({  # type: ignore[no-any-return]
        "date": str(on), "orders": out, "totals": totals, "weighting": "notional",
        "scatter": A.scatter(A.cost_frame(t, r, "MOC", "vs_close"), "pct_auction", 2000),
        "disagreement": A.outliers(t, r, 15), "t2_top10": t2,
        "callouts": {"largest_rebalance_order_on_date": largest_rebal, "largest_rebalance_order_in_run": largest_any,
                     "largest_initial_order": largest_initial},
        "fallbacks": A.fallback_report(t),
    })


def _t2_top10(run_id: str, on: date, top: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reprice the top-ten orders by notional with the T2 tick-replay model (VWAP 09:30-15:50,
    10% interval cap), without touching the stored run."""
    from simp.accounting.calendar import ny_ts
    from simp.api.routers.market import market_data
    from simp.config.run_config import CostModelParams
    from simp.domain.enums import ExecutionModel, side_sign
    from simp.domain.ids import IdFactory
    from simp.domain.records import TraderOrder
    from simp.errors import ExecutionModelUnavailable
    from simp.execution.intraday import IntradayService
    from simp.execution.models import Executor

    md = market_data()
    assert md is not None  # noqa: S101 - caller checked
    intra = IntradayService(md)
    try:
        ex = Executor(md, intra, ExecutionModel.T2, CostModelParams(), IdFactory("t2-" + run_id), run_id, 1)
    except ExecutionModelUnavailable as e:
        return [{"error": str(e)}]
    i = md.cal.index(on)
    out = []
    for o in top:
        if o.get("permno") not in md.pos:
            continue
        f = 1.0
        q = float(o["quantity"])
        to = TraderOrder("T2", o["pm_order_id"], run_id, "DESK", "ALGO_VWAP", None, "BRK_A", q * f, ny_ts(on, time(9, 30)),
                         ny_ts(on, time(15, 50)), 0.0, {}, "NEW", 1.5, permno=int(o["permno"]), side=str(o["side"]))
        res = ex.execute(i, [to])
        qx = sum(x.quantity for x in res.fills)
        px = sum(x.quantity * x.price for x in res.fills) / qx if qx else float("nan")
        s = side_sign(str(o["side"]))
        pd_ = float(o["decision_price"])
        close = float(md.prc[i, md.j(int(o["permno"]))])
        out.append({"pm_order_id": o["pm_order_id"], "ticker": o["ticker"], "notional": o["exec_notional"], "t2_avg_price": px,
                    "t2_fill_rate": qx / q if q else None, "t2_vs_decision_bps": s * (px - pd_) / pd_ * 1e4 if qx else None,
                    "t2_vs_close_bps": s * (px - close) / close * 1e4 if qx else None, "moc_vs_close_bps": o.get("moc_vs_close_bps"),
                    "fills": len(res.fills), "pct_adv": o.get("pct_adv")})
    return out


def _finite(x: Any) -> bool:
    return isinstance(x, int | float) and math.isfinite(x)


__all__ = ["router", "np"]
