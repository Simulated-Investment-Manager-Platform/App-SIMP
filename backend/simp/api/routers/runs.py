"""Runs: submit, status, SSE progress, NAV, benchmark, flows, holdings, compare, studies."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from simp.analytics.fund import fund_report
from simp.api.deps import User, current_user, one, require, rows
from simp.api.schemas import NavPoint, RunDetail, RunOut, SubmitOut
from simp.config.run_config import PRESETS, RunConfig
from simp.data.lake.store import Lake
from simp.db import schema as S
from simp.db.session import get_engine
from simp.desk.policy import policy_names
from simp.errors import DataMissingError
from simp.runs import orchestrator as orch

router = APIRouter(tags=["runs"])
RUN_COLS = (
    "run_id, fund_id, name, status, stage, progress, config_hash, code_version, data_label, data_snapshot_hashes, "
    "config->>'execution_model' as execution_model, "
    "submitted_by, submitted_at, started_at, finished_at, output_hash, study_id, study_step, error"
)


def _run(run_id: str) -> dict[str, Any]:
    r = one("select * from run where run_id=:r", r=run_id)
    if r is None:
        raise HTTPException(404, f"run {run_id} not found")
    return r


def _f(x: Any) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0


@router.get("/presets")
def presets(_: User = Depends(current_user)) -> dict[str, Any]:
    return {
        "presets": {k: RunConfig.model_validate(v).model_dump(mode="json") for k, v in PRESETS.items()},
        "default": RunConfig().model_dump(mode="json"),
        "desk_policies": policy_names(),
        "flow_presets": ["default", "notebook_seeded", "none", "custom"],
        "execution_models": ["T0", "T1", "T2", "T3"],
    }


@router.post("/runs/check")
def check(cfg: dict[str, Any], _: User = Depends(current_user)) -> dict[str, Any]:
    """Data-availability check before submit: config validity, snapshot, window coverage,
    model availability, mapping coverage."""
    out: dict[str, Any] = {"ok": True, "checks": []}

    def add(name: str, ok: bool, detail: str, fix: str | None = None) -> None:
        out["checks"].append({"name": name, "ok": ok, "detail": detail, "fix": fix})
        out["ok"] = out["ok"] and ok

    try:
        c = RunConfig.model_validate(cfg)
    except ValidationError as e:
        add("config", False, "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()))
        return out
    add("config", True, f"valid, hash {c.config_hash()[:12]}")
    try:
        snap = c.data_snapshot or orch.latest_snapshot()
        s = Lake().snapshot(snap)
        add("snapshot", True, f"snapshot {snap[:12]} with {len(s['manifests'])} datasets")
    except DataMissingError as e:
        add("snapshot", False, str(e), "Data -> Pull")
        return out
    lake = Lake()
    dates = lake.partition_dates("crsp", "dsf_v2")
    if dates:
        cov = dates[0] <= c.window.bootstrap and dates[-1] >= c.window.end
        add(
            "window",
            cov,
            f"CRSP sessions {dates[0]}..{dates[-1]}, run asks {c.window.bootstrap}..{c.window.end}",
            None if cov else "Pull a wider window",
        )
    label = lake.manifest_for(snap, "crsp", "dsf_v2")["data_label"]
    if c.execution_model == "T3":
        add(
            "execution model",
            False,
            "T3 runs inside the BMLL Data Lab only (bmll2.simulation)",
            "Use `simp lab t3-job`",
        )
    elif c.execution_model == "T2" and label != "SYNTHETIC":
        has = "bmll/trades_plus_minute" in lake.snapshot(snap)["manifests"]
        if has:
            n_bar = len(lake.manifest_for(snap, "bmll", "trades_plus_minute")["files"])
            add(
                "execution model",
                True,
                f"T2 replays BMLL Trades Plus minute bars ({n_bar} sessions); orders "
                "without bars fall back to daily figures, flagged INTRADAY_FALLBACK_DAILY",
            )
        else:
            add(
                "execution model",
                False,
                "T2 needs BMLL Trades Plus minute bars in the snapshot",
                "Run T1 first, then: simp data export-t2-needs <run> -> puller.py --t2-needs -> "
                "simp data register-bmll DIR --only trades_plus_minute -> simp data pull --source live --reuse-crsp",
            )
    else:
        add("execution model", True, f"{c.execution_model} available on {label} data")
    md_dates = lake.partition_dates("derived", "market_daily")
    add(
        "BMLL daily figures",
        bool(md_dates),
        f"{len(md_dates)} sessions of market_daily" if md_dates else "missing",
        None if md_dates else "Data -> Pull",
    )
    q = rows("select count(*) n from mapping_review where status='OPEN'")
    n = int(q[0]["n"]) if q else 0
    add(
        "mapping review",
        True,
        f"{n} unmapped name(s) in the review queue; policy '{c.unmapped_policy}'"
        + (" will stop the run" if n and c.unmapped_policy == "stop" else ""),
    )
    out["data_label"] = label
    return out


@router.post("/runs", response_model=SubmitOut)
def submit(cfg: dict[str, Any], user: User = Depends(require("researcher"))) -> SubmitOut:
    try:
        c = RunConfig.model_validate(cfg)
    except ValidationError as e:
        raise HTTPException(422, e.errors()) from e
    try:
        rid = orch.submit(c, user.username)
    except DataMissingError as e:
        raise HTTPException(409, str(e)) from e
    return SubmitOut(run_id=rid, config_hash=_run(rid)["config_hash"])


@router.get("/runs", response_model=list[RunOut])
def list_runs(
    fund_id: str | None = None,
    status: str | None = None,
    study_id: str | None = None,
    limit: int = 100,
    _: User = Depends(current_user),
) -> list[dict[str, Any]]:
    w: list[str] = []
    p: dict[str, Any] = {"l": limit}
    if fund_id:
        w.append("fund_id=:f")
        p["f"] = fund_id
    if status:
        w.append("status=:s")
        p["s"] = status
    if study_id:
        w.append("study_id=:st")
        p["st"] = study_id
    where = ("where " + " and ".join(w)) if w else ""
    return rows(f"select {RUN_COLS} from run {where} order by submitted_at desc limit :l", **p)  # noqa: S608


@router.get("/runs/compare")
def compare(ids: str, _: User = Depends(current_user)) -> dict[str, Any]:
    """Side-by-side metrics; for a counterfactual chain each step's change is attributed to that step."""
    out = []
    for rid in [x for x in ids.split(",") if x]:
        r = _run(rid)
        s = r["summary"] or {}
        f = s.get("fund") or {}
        t = (s.get("tca") or {}).get("exec") or {}
        out.append(
            {
                "run_id": rid,
                "name": r["name"],
                "status": r["status"],
                "study_step": r["study_step"],
                "config_hash": r["config_hash"],
                "output_hash": r["output_hash"],
                "execution_model": r["config"]["execution_model"],
                "timing": r["config"]["timing"]["rule"],
                "td_net_bps": f.get("td_net_bps"),
                "td_gross_bps": f.get("td_gross_bps"),
                "te_bps": f.get("te_bps"),
                "annual_return": f.get("annual_return"),
                "ending_nav": f.get("ending_nav"),
                "cash_drag_bps": f.get("cash_drag_bps"),
                "exec_cost_bps": t.get("cost_bps"),
                "exec_cost_usd": t.get("cost_usd"),
                "notional": t.get("notional"),
                "netting_saved": (s.get("netting") or {}).get("saved_notional"),
                "provisional_dividends": s.get("provisional_dividends"),
            }
        )
    out.sort(key=lambda x: x["study_step"] if x["study_step"] is not None else 0)
    steps = []
    for a, b in zip(out, out[1:], strict=False):
        if a["td_net_bps"] is not None and b["td_net_bps"] is not None:
            steps.append(
                {
                    "from": a["run_id"],
                    "to": b["run_id"],
                    "label": b["name"],
                    "delta_td_bps": b["td_net_bps"] - a["td_net_bps"],
                    "delta_te_bps": (b["te_bps"] or 0) - (a["te_bps"] or 0),
                    "delta_nav": (b["ending_nav"] or 0) - (a["ending_nav"] or 0),
                }
            )
    return {"runs": out, "steps": steps, "note": "Effects interact; the order of steps is fixed and printed."}


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: str, _: User = Depends(current_user)) -> dict[str, Any]:
    return _run(run_id)


@router.get("/runs/{run_id}/events")
async def events(run_id: str, request: Request, after: int = 0) -> EventSourceResponse:
    """Server-sent events: stage / progress / log / error / done."""
    _run(run_id)

    async def gen() -> AsyncIterator[dict[str, str]]:
        last = after
        idle = 0
        while True:
            if await request.is_disconnected():
                return
            evs = rows(
                "select id, ts, kind, payload from run_event where run_id=:r and id>:a order by id limit 200",
                r=run_id,
                a=last,
            )
            for e in evs:
                last = int(e["id"])
                yield {
                    "id": str(last),
                    "event": e["kind"],
                    "data": json.dumps({"ts": e["ts"].isoformat(), **(e["payload"] or {})}, default=str),
                }
                if e["kind"] in ("done", "error"):
                    return
            idle = 0 if evs else idle + 1
            if idle > 2400:
                return
            await asyncio.sleep(0.5)

    return EventSourceResponse(gen())


@router.get("/runs/{run_id}/log")
def run_log(run_id: str, _: User = Depends(current_user)) -> list[dict[str, Any]]:
    return rows(
        "select id, ts, kind, payload from run_event where run_id=:r and kind<>'progress' order by id",
        r=run_id,
    )


@router.get("/runs/{run_id}/nav", response_model=list[NavPoint])
def nav(run_id: str, _: User = Depends(current_user)) -> list[dict[str, Any]]:
    r = _run(run_id)
    start = date.fromisoformat(r["config"]["window"]["start"])
    data = rows("select * from nav_daily where run_id=:r order by date", r=run_id)
    out, bl, fl = [], 1.0, 1.0
    for x in data:
        if x["date"] > start:
            bl *= 1 + _f(x["bench_return_tr"])
            fl *= 1 + _f(x["fund_return"])
        out.append(
            {
                **{k: _f(v) if k not in ("date",) else v for k, v in x.items() if k in NavPoint.model_fields},
                "bench_return_official": x["bench_return_official"],
                "bench_level": bl,
                "fund_level": fl,
                "active_return": _f(x["fund_return"]) - _f(x["bench_return_tr"])
                if x["date"] > start
                else 0.0,
            }
        )
    return out


@router.get("/runs/{run_id}/benchmark")
def benchmark(run_id: str, benchmark: str | None = None, _: User = Depends(current_user)) -> dict[str, Any]:
    r = _run(run_id)
    navs = rows("select * from nav_daily where run_id=:r order by date", r=run_id)
    for x in navs:
        for k, v in list(x.items()):
            if k != "date" and v is not None and not isinstance(v, str):
                x[k] = float(v)
    rep = fund_report(
        navs, date.fromisoformat(r["config"]["window"]["start"]), benchmark or r["config"]["benchmark"]
    )
    start = date.fromisoformat(r["config"]["window"]["start"])
    act = [
        {"date": x["date"], "active": x["fund_return"] - x["bench_return_tr"]}
        for x in navs
        if x["date"] > start
    ]
    roll = []
    arr = np.array([x["active"] for x in act])
    for n in range(62, len(arr)):
        roll.append(
            {
                "date": act[n]["date"],
                "te_63d_bps": float(np.std(arr[n - 62 : n + 1], ddof=1) * math.sqrt(252) * 1e4),
            }
        )
    s = r["summary"] or {}
    decomposition = None
    if r["study_id"]:
        st = one("select kind from study where study_id=:s", s=r["study_id"])
        if st and st["kind"] == "counterfactual_chain":
            sib = rows(
                "select run_id from run where study_id=:s and status='DONE' order by study_step",
                s=r["study_id"],
            )
            decomposition = compare(",".join(x["run_id"] for x in sib), _)["steps"]
    single = None
    if rep.get("available"):
        single = [
            {"label": "Benchmark", "bps": 0.0},
            {"label": "Cash drag", "bps": rep["cash_drag_bps"]},
            {"label": "Explicit trading costs", "bps": -rep["explicit_costs_bps_pa"]},
            {"label": "Management fee", "bps": -rep["fees_bps_pa"]},
            {
                "label": "Implicit costs, exclusions, timing & residual",
                "bps": rep["td_net_bps"]
                - rep["cash_drag_bps"]
                + rep["explicit_costs_bps_pa"]
                + rep["fees_bps_pa"],
            },
            {"label": "Fund (TD net)", "bps": rep["td_net_bps"], "total": True},
        ]
    return {
        "report": rep,
        "active": act,
        "rolling_te": roll,
        "decomposition_chain": decomposition,
        "decomposition_single": single,
        "excluded_weight": s.get("excluded_weight"),
        "data_label": r["data_label"],
    }


@router.get("/runs/{run_id}/flows")
def flows(run_id: str, _: User = Depends(current_user)) -> dict[str, Any]:
    _run(run_id)
    fl = rows(
        "select date, amount_usd, units, unit_nav_used, is_jump from flow where run_id=:r order by date",
        r=run_id,
    )
    cash = rows(
        "select date, cash, receivables, payables, nav, cash_weight from nav_daily where run_id=:r order by date",
        r=run_id,
    )
    r = _run(run_id)
    return {
        "flows": fl,
        "cash": cash,
        "buffer_bps": r["config"]["flows"]["cash_buffer_bps"],
        "config": r["config"]["flows"],
    }


@router.get("/runs/{run_id}/holdings")
def holdings(run_id: str, on: date | None = None, _: User = Depends(current_user)) -> dict[str, Any]:
    _run(run_id)
    d = on or (one("select max(date) d from position_daily where run_id=:r", r=run_id) or {}).get("d")
    if d is None:
        return {"date": None, "rows": [], "excluded": []}
    d = (one("select max(date) d from position_daily where run_id=:r and date<=:d", r=run_id, d=d) or {}).get(
        "d"
    )
    navr = one("select nav from nav_daily where run_id=:r and date=:d", r=run_id, d=d)
    dec = one(
        "select decision_id, effective_date from decision where run_id=:r and target_weights_ref is not null and effective_date<=:d "
        "order by effective_date desc limit 1",
        r=run_id,
        d=d,
    )
    tw = (
        {
            x["permno"]: x
            for x in rows("select * from target_weight where decision_id=:x", x=dec["decision_id"])
        }
        if dec
        else {}
    )
    from simp.api.routers.market import ticker_map

    tick = ticker_map()
    nav_v = float(navr["nav"]) if navr else 1.0
    pos = rows(
        "select permno, shares, close_price, market_value from position_daily where run_id=:r and date=:d order by market_value desc",
        r=run_id,
        d=d,
    )
    out = []
    for p in pos:
        t = tw.get(p["permno"])
        w = float(p["market_value"]) / nav_v
        out.append(
            {
                "permno": p["permno"],
                "ticker": tick.get(p["permno"]),
                "shares": float(p["shares"]),
                "price": float(p["close_price"]),
                "market_value": float(p["market_value"]),
                "weight": w,
                "target_weight": t["weight"] if t else None,
                "active_weight_bps": (w - t["weight"]) * 1e4 if t else None,
            }
        )
    excl = [
        {"permno": k, "ticker": tick.get(k), "reason": v["exclusion_reason"], "index_weight": None}
        for k, v in tw.items()
        if v["excluded_flag"]
    ]
    return {"date": d, "decision": dec, "nav": nav_v, "rows": out, "excluded": excl}


# ---------------------------------------------------------------------- studies
@router.post("/studies")
def create_study(body: dict[str, Any], user: User = Depends(require("researcher"))) -> dict[str, Any]:
    kind = body.get("kind", "counterfactual_chain")
    base = RunConfig.model_validate(body.get("base") or PRESETS["index_t1_flows"])
    try:
        sid, runs = orch.submit_study(kind, base, user.username, body.get("name"))
    except (ValueError, DataMissingError) as e:
        raise HTTPException(422, str(e)) from e
    return {"study_id": sid, "runs": runs}


@router.get("/studies")
def list_studies(_: User = Depends(current_user)) -> list[dict[str, Any]]:
    return rows(
        "select s.study_id, s.kind, s.name, s.created_by, s.created_at, count(r.run_id) runs, "
        "sum(case when r.status='DONE' then 1 else 0 end) done from study s left join run r on r.study_id=s.study_id "
        "group by s.study_id order by s.created_at desc"
    )


@router.get("/studies/{study_id}")
def get_study(study_id: str, _: User = Depends(current_user)) -> dict[str, Any]:
    s = one("select * from study where study_id=:s", s=study_id)
    if not s:
        raise HTTPException(404, "study not found")
    rs = rows(f"select {RUN_COLS} from run where study_id=:s order by study_step", s=study_id)  # noqa: S608
    done = [x["run_id"] for x in rs if x["status"] == "DONE"]
    return {"study": s, "runs": rs, "compare": compare(",".join(done), _) if done else None}


@router.delete("/runs/{run_id}")
def delete_run(run_id: str, _: User = Depends(require("admin"))) -> dict[str, str]:
    _run(run_id)
    orch.delete_run(run_id)
    return {"deleted": run_id}


@router.get("/funds/{fund_id}/runs/latest")
def latest_run(fund_id: str, _: User = Depends(current_user)) -> dict[str, Any] | None:
    with get_engine().connect() as c:
        r = c.execute(
            select(S.run.c.run_id)
            .where(S.run.c.fund_id == fund_id, S.run.c.status == "DONE")
            .order_by(S.run.c.finished_at.desc())
            .limit(1)
        ).first()
    return {"run_id": r[0]} if r else None
