"""Order chain: paged blotter at each level, and the full lineage of one PM order."""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from simp.api.deps import User, current_user, one, rows
from simp.api.schemas import Page

router = APIRouter(tags=["orders"])

PM_SORT = {"notional", "exec_notional", "exec_cost_bps", "is_bps", "pct_adv", "pct_auction", "date", "ticker"}


def _num(v: Any) -> Any:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return f if math.isfinite(f) else None


def _clean(r: dict[str, Any]) -> dict[str, Any]:
    return {k: (_num(v) if not isinstance(v, str | bool | dict | list | date) and v is not None else v) for k, v in r.items()}


@router.get("/runs/{run_id}/orders", response_model=Page)
def orders(run_id: str, level: str = Query("pm", pattern="^(pm|trader|child|fill)$"), on: date | None = Query(None, alias="date"),
           permno: int | None = None, reason: str | None = None, strategy: str | None = None, broker: str | None = None,
           venue: str | None = None, size_bucket: str | None = None, side: str | None = None, pm_order_id: str | None = None,
           trader_order_id: str | None = None, sort: str = "exec_notional", desc: bool = True, limit: int = Query(100, le=5000),
           offset: int = 0, _: User = Depends(current_user)) -> dict[str, Any]:
    p: dict[str, Any] = {"r": run_id, "l": limit, "o": offset}
    w = ["p.run_id=:r"]
    if on:
        w.append("p.exec_date=:d")
        p["d"] = on
    if permno:
        w.append("p.permno=:pn")
        p["pn"] = permno
    if reason:
        w.append("p.reason=:rs")
        p["rs"] = reason
    if side:
        w.append("p.side=:sd")
        p["sd"] = side
    if size_bucket:
        w.append("t.size_bucket=:sb")
        p["sb"] = size_bucket
    if pm_order_id:
        w.append("p.pm_order_id=:pm")
        p["pm"] = pm_order_id
    if level == "pm":
        if strategy:
            w.append("t.main_strategy=:st")
            p["st"] = strategy
        if broker:
            w.append("t.main_broker=:br")
            p["br"] = broker
        if venue:
            w.append("t.main_venue=:vn")
            p["vn"] = venue
        where = " and ".join(w)
        s = sort if sort in PM_SORT else "exec_notional"
        s = f"t.{s}" if s not in ("date", "ticker") else ("p.exec_date" if s == "date" else "t.ticker")
        base = f"from pm_order p left join tca_order t on t.pm_order_id=p.pm_order_id where {where}"
        total = one(f"select count(*) n {base}", **p)["n"]  # type: ignore[index]  # noqa: S608
        data = rows(
            "select p.pm_order_id, p.exec_date as date, p.permno, t.ticker, p.side, p.quantity, p.filled_qty, p.reason, p.instruction, "
            "p.status, p.decision_ts, p.decision_price, p.deadline_ts, p.urgency, p.quantity_adjustment_factor, p.flags, "
            "t.notional, t.exec_notional, t.avg_price, t.arrival_price, t.close_price, t.pct_adv, t.pct_auction, t.size_bucket, "
            "t.exec_cost_bps, t.is_bps, t.delay_bps, t.slip_benchmark_bps, t.main_strategy, t.main_broker, t.main_venue, "
            f"(select count(*) from trader_order x where x.pm_order_id=p.pm_order_id) n_trader {base} "  # noqa: S608
            f"order by {s} {'desc' if desc else 'asc'} nulls last limit :l offset :o", **p)
        return {"total": int(total), "limit": limit, "offset": offset, "rows": [_clean(r) for r in data]}
    if level == "trader":
        if strategy:
            w.append("x.strategy=:st")
            p["st"] = strategy
        if broker:
            w.append("x.broker_id=:br")
            p["br"] = broker
        if trader_order_id:
            w.append("x.trader_order_id=:tid")
            p["tid"] = trader_order_id
        where = " and ".join(w)
        base = f"from trader_order x join pm_order p on p.pm_order_id=x.pm_order_id left join tca_order t on t.pm_order_id=p.pm_order_id where {where}"
        total = one(f"select count(*) n {base}", **p)["n"]  # type: ignore[index]  # noqa: S608
        data = rows(
            "select x.trader_order_id, x.pm_order_id, p.exec_date as date, p.permno, t.ticker, p.side, x.strategy, x.broker_id, "
            "x.quantity, x.filled_qty, x.start_ts, x.end_ts, x.arrival_price, x.status, x.commission_bps, x.program_id, x.params, "
            "f.avg_price, f.notional, t.close_price / p.quantity_adjustment_factor as close_price, "
            "t.vwap_interval / p.quantity_adjustment_factor as vwap_interval, "
            "(select count(*) from child_order c where c.trader_order_id=x.trader_order_id) n_child "
            f"{base.replace('where', 'left join lateral (select sum(price*quantity)/nullif(sum(quantity),0) avg_price, sum(price*quantity) notional from fill ff where ff.trader_order_id=x.trader_order_id) f on true where', 1)} "
            "order by f.notional desc nulls last limit :l offset :o", **p)  # noqa: S608
        for r in data:
            s_ = 1 if r["side"] == "BUY" else -1
            ap, avg = _num(r["arrival_price"]), _num(r["avg_price"])
            bench = {"MOC": "close_price", "LOC": "close_price", "PORTFOLIO_TRADE": "close_price", "INTERNAL_CROSS": "close_price",
                     "ALGO_IS": "arrival_price", "HIGH_TOUCH": "arrival_price"}.get(r["strategy"], "vwap_interval")
            b = _num(r[bench])
            r["benchmark"] = bench.replace("_price", "").replace("_interval", "")
            r["slip_benchmark_bps"] = s_ * (avg - b) / b * 1e4 if avg and b else None
            r["slip_arrival_bps"] = s_ * (avg - ap) / ap * 1e4 if avg and ap else None
        return {"total": int(total), "limit": limit, "offset": offset, "rows": [_clean(r) for r in data]}
    if level == "child":
        if trader_order_id:
            w.append("c.trader_order_id=:tid")
            p["tid"] = trader_order_id
        if venue:
            w.append("c.venue_mic=:vn")
            p["vn"] = venue
        where = " and ".join(w)
        base = (f"from child_order c join trader_order x on x.trader_order_id=c.trader_order_id join pm_order p on "
                f"p.pm_order_id=x.pm_order_id left join tca_order t on t.pm_order_id=p.pm_order_id where {where}")
        total = one(f"select count(*) n {base}", **p)["n"]  # type: ignore[index]  # noqa: S608
        data = rows("select c.*, p.permno, p.side, t.ticker, x.strategy, "
                    "(select sum(quantity) from fill f where f.child_order_id=c.child_order_id) filled_qty "
                    f"{base} order by c.sent_ts limit :l offset :o", **p)  # noqa: S608
        return {"total": int(total), "limit": limit, "offset": offset, "rows": [_clean(r) for r in data]}
    # fills
    if trader_order_id:
        w.append("f.trader_order_id=:tid")
        p["tid"] = trader_order_id
    if venue:
        w.append("f.venue_mic=:vn")
        p["vn"] = venue
    if broker:
        w.append("f.broker_id=:br")
        p["br"] = broker
    where = " and ".join(w).replace("p.run_id", "f.run_id")
    base = f"from fill f join pm_order p on p.pm_order_id=f.pm_order_id left join tca_order t on t.pm_order_id=p.pm_order_id where {where}"
    total = one(f"select count(*) n {base}", **p)["n"]  # type: ignore[index]  # noqa: S608
    data = rows(f"select f.*, t.ticker {base} order by f.exec_ts limit :l offset :o", **p)  # noqa: S608
    return {"total": int(total), "limit": limit, "offset": offset, "rows": [_clean(r) for r in data]}


@router.get("/orders/{pm_order_id}/lineage")
def lineage(pm_order_id: str, _: User = Depends(current_user)) -> dict[str, Any]:
    """Full tree for one PM order with fills, reference prices and an intraday price series."""
    pm = one("select * from pm_order where pm_order_id=:p", p=pm_order_id)
    if not pm:
        raise HTTPException(404, "PM order not found")
    dec = one("select * from decision where decision_id=:d", d=pm["decision_id"])
    tca = one("select * from tca_order where pm_order_id=:p", p=pm_order_id)
    rep = one("select * from reprice where pm_order_id=:p", p=pm_order_id)
    traders = rows("select * from trader_order where pm_order_id=:p order by start_ts", p=pm_order_id)
    tids = [t["trader_order_id"] for t in traders]
    children = rows("select * from child_order where trader_order_id = any(:t) order by sent_ts", t=tids) if tids else []
    fills = rows("select * from fill where pm_order_id=:p order by exec_ts", p=pm_order_id)
    prog = None
    pids = [t["program_id"] for t in traders if t["program_id"]]
    if pids:
        prog = one("select * from program where program_id=:p", p=pids[0])
    for t in traders:
        t["children"] = [c for c in children if c["trader_order_id"] == t["trader_order_id"]]
        for c in t["children"]:
            c["fills"] = [f for f in fills if f["child_order_id"] == c["child_order_id"]]
    timeline = [{"ts": pm["decision_ts"], "kind": "decision", "label": f"{dec['decision_type'] if dec else ''} decision",
                 "price": pm["decision_price"]}]
    for t in traders:
        timeline.append({"ts": t["start_ts"], "kind": "trader", "label": f"{t['strategy']} via {t['broker_id']}", "price": t["arrival_price"],
                         "quantity": t["quantity"]})
    for f in fills[:200]:
        timeline.append({"ts": f["exec_ts"], "kind": "fill", "label": f"{f['venue_mic']} {f['liquidity']}", "price": f["price"],
                         "quantity": f["quantity"]})
    timeline.append({"ts": pm["deadline_ts"], "kind": "close", "label": "deadline (close)", "price": tca["close_price"] if tca else None})
    timeline.sort(key=lambda x: x["ts"])
    chart = _intraday(pm, tca)
    return {"pm_order": _clean(pm), "decision": dec, "tca": _clean(tca) if tca else None, "reprice": _clean(rep) if rep else None,
            "program": prog, "trader_orders": [_clean(t) for t in traders], "fills": [_clean(f) for f in fills],
            "timeline": timeline, "chart": chart}


def _intraday(pm: dict[str, Any], tca: dict[str, Any] | None) -> dict[str, Any] | None:
    from simp.api.routers.market import market_data
    from simp.execution.intraday import IntradayService

    md = market_data()
    if md is None or pm["permno"] not in md.pos:
        return None
    i = md.cal.index(pm["exec_date"]) if md.cal.is_session(pm["exec_date"]) else None
    if i is None:
        return None
    j = md.j(pm["permno"])
    intra = IntradayService(md)
    p = intra.paths(i)
    f = float(pm["quantity_adjustment_factor"] or 1.0)
    series = [[k, float(p[j, k])] for k in range(0, 390, 2)] if p is not None else []
    return {"minutes": series, "basis": "execution-date shares", "open": float(md.opn[i, j]), "close": float(md.prc[i, j]),
            "vwap": float(md.vwap[i, j]), "decision_price_exec_basis": float(pm["decision_price"]) / f,
            "arrival_price": float(tca["arrival_price"]) / f if tca and tca.get("arrival_price") else None,
            "synthetic_intraday": intra.synthetic}
