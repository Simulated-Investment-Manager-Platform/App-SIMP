"""Data jobs + review queues, settings (users, brokers, venues, desk policy), exports, auth, overview."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import time
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
import yaml
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from simp.api.deps import COOKIE, User, current_user, one, require, rows, serializer
from simp.api.schemas import FundIn, FundOut, Me, ValidateOut
from simp.data.jobs import create_job
from simp.db import schema as S
from simp.db.session import get_engine
from simp.desk.policy import DeskPolicy
from simp.domain.ids import ulid_from
from simp.settings import get_settings

router = APIRouter()


# ------------------------------------------------------------------ auth
@router.get("/me", response_model=Me, tags=["auth"])
def me(u: User = Depends(current_user)) -> Me:
    return Me(username=u.username, role=u.role, auth_mode=get_settings().auth_mode)


@router.post("/auth/dev-login", tags=["auth"])
def dev_login(body: dict[str, str], response: Response) -> dict[str, str]:
    if get_settings().auth_mode != "dev":
        raise HTTPException(403, "dev login disabled; use OAuth")
    u = one("select username, role from users where username=:u", u=body.get("username", ""))
    if not u:
        raise HTTPException(403, "not on the allow-list")
    response.set_cookie(COOKIE, serializer().dumps({"u": u["username"]}), httponly=True, samesite="lax")
    return {"username": u["username"], "role": u["role"]}


@router.post("/auth/logout", tags=["auth"])
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(COOKIE)
    return {"ok": True}


@router.get("/auth/login", tags=["auth"])
def oauth_login() -> RedirectResponse:
    s = get_settings()
    if not s.oauth_client_id:
        raise HTTPException(501, "OAuth is not configured (SIMP_OAUTH_CLIENT_ID)")
    q = urlencode({"client_id": s.oauth_client_id, "scope": "read:user", "allow_signup": "false"})
    return RedirectResponse(f"https://github.com/login/oauth/authorize?{q}")


@router.get("/auth/callback", tags=["auth"])
def oauth_callback(code: str) -> RedirectResponse:
    s = get_settings()
    if not (s.oauth_client_id and s.oauth_client_secret):
        raise HTTPException(501, "OAuth is not configured")
    tok = httpx.post("https://github.com/login/oauth/access_token", headers={"Accept": "application/json"}, timeout=20,
                     data={"client_id": s.oauth_client_id, "client_secret": s.oauth_client_secret.get_secret_value(), "code": code}).json()
    login = httpx.get("https://api.github.com/user", headers={"Authorization": f"Bearer {tok.get('access_token')}"}, timeout=20).json().get("login")
    if not one("select username from users where username=:u", u=login or ""):
        raise HTTPException(403, f"{login} is not on the SIMP allow-list")
    r = RedirectResponse(s.web_origin)
    r.set_cookie(COOKIE, serializer().dumps({"u": login}), httponly=True, samesite="lax")
    return r


# ------------------------------------------------------------------ funds & overview
@router.get("/funds", response_model=list[FundOut], tags=["funds"])
def funds(_: User = Depends(current_user)) -> list[dict[str, Any]]:
    return rows("select * from fund order by inception_date")


@router.post("/funds", response_model=FundOut, tags=["funds"])
def create_fund(f: FundIn, _: User = Depends(require("researcher"))) -> dict[str, Any]:
    with get_engine().begin() as c:
        c.execute(pg_insert(S.fund).values(**f.model_dump()).on_conflict_do_update(index_elements=["fund_id"], set_=f.model_dump()))
    return f.model_dump()


@router.get("/overview", tags=["funds"])
def overview(_: User = Depends(current_user)) -> dict[str, Any]:
    fs = rows("select * from fund order by inception_date")
    out = []
    for f in fs:
        last = one("select run_id, name, status, finished_at, summary, data_label from run where fund_id=:f and status='DONE' "
                   "and study_id is null order by finished_at desc limit 1", f=f["fund_id"]) or \
            one("select run_id, name, status, finished_at, summary, data_label from run where fund_id=:f and status='DONE' "
                "order by finished_at desc limit 1", f=f["fund_id"])
        latest_any = one("select run_id, status, stage, progress from run where fund_id=:f order by submitted_at desc limit 1", f=f["fund_id"])
        spark = []
        if last:
            spark = [float(x["unit_nav"]) for x in rows("select unit_nav from nav_daily where run_id=:r and extract(dow from date)=5 order by date",
                                                          r=last["run_id"])]
        fund = (last or {}).get("summary", {}) or {}
        fr = fund.get("fund") or {}
        tca = (fund.get("tca") or {}).get("exec") or {}
        out.append({**f, "last_run": {k: v for k, v in (last or {}).items() if k != "summary"} or None, "latest": latest_any,
                    "sparkline": spark, "td_net_bps": fr.get("td_net_bps"), "te_bps": fr.get("te_bps"), "aum": fr.get("ending_nav"),
                    "exec_cost_bps": tca.get("cost_bps"), "orders": (fund.get("counts") or {}).get("pm_orders")})
    recent = rows("select run_id, fund_id, name, status, stage, progress, config_hash, submitted_at, finished_at, data_label "
                  "from run order by submitted_at desc limit 12")
    from simp.api.routers.market import coverage

    cov = coverage(_)
    jobs = rows("select job_id, kind, source, status, created_at, finished_at from data_job order by created_at desc limit 1")
    return {"funds": out, "recent_runs": recent,
            "health": {"data_label": cov["data_label"], "data_source": cov["data_source"], "wrds": cov["wrds"],
                       "bmll": {"configured": cov["bmll"]["configured"],
                                "last_pull": (cov["bmll"]["daily_analytics"] or {}).get("pulled_at")},
                       "mapping": cov["mapping"], "last_job": jobs[0] if jobs else None,
                       "snapshot": cov["snapshots"][-1]["snapshot_hash"] if cov["snapshots"] else None}}


# ------------------------------------------------------------------ data jobs & review queues
@router.get("/data/jobs", tags=["data"])
def jobs(_: User = Depends(current_user)) -> list[dict[str, Any]]:
    return rows("select * from data_job order by created_at desc limit 50")


@router.post("/data/pull", tags=["data"])
def pull(body: dict[str, Any], u: User = Depends(require("researcher"))) -> dict[str, str]:
    src = body.get("source") or get_settings().data_source
    if src not in ("synthetic", "live"):
        raise HTTPException(422, "source must be synthetic or live")
    return {"job_id": create_job("pull", src, body, u.username)}


@router.get("/data/mapping-review", tags=["data"])
def mapping_review(_: User = Depends(current_user)) -> list[dict[str, Any]]:
    return rows("select * from mapping_review order by status, permno")


class MappingFix(BaseModel):
    action: str  # FIX | EXCLUDE | REOPEN
    listing_id: int | None = None


@router.post("/data/mapping-review/{permno}/{valid_from}", tags=["data"])
def fix_mapping(permno: int, valid_from: date, body: MappingFix, u: User = Depends(require("researcher"))) -> dict[str, Any]:
    st = {"FIX": "FIXED", "EXCLUDE": "EXCLUDED", "REOPEN": "OPEN"}.get(body.action)
    if st is None:
        raise HTTPException(422, "action must be FIX, EXCLUDE or REOPEN")
    if st == "FIXED" and not body.listing_id:
        raise HTTPException(422, "FIX needs a listing_id")
    with get_engine().begin() as c:
        n = c.execute(update(S.mapping_review).where(S.mapping_review.c.permno == permno, S.mapping_review.c.valid_from == valid_from)
                      .values(status=st, fixed_listing_id=body.listing_id, reviewed_by=u.username)).rowcount
    if not n:
        raise HTTPException(404, "review item not found")
    return {"permno": permno, "status": st, "note": "applied to the next data snapshot build"}


@router.get("/data/corporate-actions", tags=["data"])
def corporate_actions(status: str | None = None, type: str | None = None, limit: int = 500, _: User = Depends(current_user)) -> list[dict[str, Any]]:
    w, p = ["1=1"], {"l": limit}
    if status:
        w.append("review_status=:s")
        p["s"] = status
    if type:
        w.append("type=:t")
        p["t"] = type
    return rows(f"select * from corporate_action where {' and '.join(w)} order by (review_status='APPROVED'), ex_date limit :l", **p)  # noqa: S608


@router.get("/data/corporate-actions/summary", tags=["data"])
def ca_summary(_: User = Depends(current_user)) -> list[dict[str, Any]]:
    return rows("select type, review_status, count(*) n from corporate_action group by type, review_status order by type")


class Review(BaseModel):
    status: str
    note: str | None = None


@router.post("/data/corporate-actions/{permno}/{ex_date}/{type}", tags=["data"])
def review_ca(permno: int, ex_date: date, type: str, body: Review, u: User = Depends(require("researcher"))) -> dict[str, Any]:
    if body.status not in ("APPROVED", "REJECTED", "PENDING", "UNSUPPORTED"):
        raise HTTPException(422, "bad status")
    if type == "MG" and body.status == "APPROVED":
        raise HTTPException(422, "mergers cannot be approved: SIMP never invents a merger payoff")
    with get_engine().begin() as c:
        n = c.execute(update(S.corporate_action).where(S.corporate_action.c.permno == permno, S.corporate_action.c.ex_date == ex_date,
                                                       S.corporate_action.c.type == type)
                      .values(review_status=body.status, reviewed_by=u.username, note=body.note)).rowcount
    if not n:
        raise HTTPException(404, "corporate action not found")
    return {"permno": permno, "ex_date": ex_date, "type": type, "review_status": body.status,
            "note": "review set is hashed into each run's data snapshot"}


# ------------------------------------------------------------------ settings
@router.get("/users", tags=["settings"])
def users(_: User = Depends(current_user)) -> list[dict[str, Any]]:
    return rows("select username, role, display_name, created_at from users order by username")


@router.post("/users", tags=["settings"])
def upsert_user(body: dict[str, str], _: User = Depends(require("admin"))) -> dict[str, str]:
    if body.get("role") not in ("viewer", "researcher", "admin") or not body.get("username"):
        raise HTTPException(422, "username and role (viewer|researcher|admin) required")
    v = {"username": body["username"], "role": body["role"], "display_name": body.get("display_name") or body["username"],
         "created_at": datetime.now(UTC)}
    with get_engine().begin() as c:
        c.execute(pg_insert(S.users).values(**v).on_conflict_do_update(index_elements=["username"], set_={"role": v["role"]}))
    return {"username": v["username"], "role": v["role"]}


@router.get("/brokers", tags=["settings"])
def brokers(_: User = Depends(current_user)) -> list[dict[str, Any]]:
    return rows("select * from broker order by broker_id")


@router.get("/venues", tags=["settings"])
def venues(_: User = Depends(current_user)) -> list[dict[str, Any]]:
    return rows("select * from venue order by kind, mic")


@router.get("/desk-policies", tags=["settings"])
def policies(_: User = Depends(current_user)) -> list[dict[str, Any]]:
    return rows("select name, yaml, updated_by, updated_at from desk_policy order by name")


def _validate(text: str) -> tuple[DeskPolicy | None, list[str]]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return None, [f"YAML: {e}"]
    try:
        return DeskPolicy.model_validate(data), []
    except ValidationError as e:
        return None, [f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()]


@router.post("/desk-policies/validate", response_model=ValidateOut, tags=["settings"])
def validate_policy(body: dict[str, str], _: User = Depends(current_user)) -> ValidateOut:
    p, errs = _validate(body.get("yaml", ""))
    return ValidateOut(ok=p is not None, errors=errs, rules=len(p.rules) if p else None)


@router.put("/desk-policies/{name}", response_model=ValidateOut, tags=["settings"])
def save_policy(name: str, body: dict[str, str], u: User = Depends(require("researcher"))) -> ValidateOut:
    p, errs = _validate(body.get("yaml", ""))
    if p is None:
        raise HTTPException(422, errs)
    with get_engine().begin() as c:
        c.execute(pg_insert(S.desk_policy).values(name=name, yaml=body["yaml"], updated_by=u.username, updated_at=datetime.now(UTC))
                  .on_conflict_do_update(index_elements=["name"], set_={"yaml": body["yaml"], "updated_by": u.username,
                                                                         "updated_at": datetime.now(UTC)}))
    return ValidateOut(ok=True, errors=[], rules=len(p.rules))


# ------------------------------------------------------------------ exports
EXPORTABLE = {
    # derived, order-level and aggregate tables only. Raw CRSP / BMLL rows are never exportable.
    "nav_daily": "select date, nav, units, unit_nav, fund_return, gross_return, flow_usd, cash_weight, bench_return_tr from nav_daily where run_id=:r order by date",
    "tca_order": "select * from tca_order where run_id=:r order by date, exec_notional desc",
    "reprice": "select * from reprice where run_id=:r order by date",
    "pm_order": "select pm_order_id, decision_id, permno, side, quantity, filled_qty, reason, instruction, decision_ts, decision_price, exec_date, status from pm_order where run_id=:r",
    "trader_order": "select trader_order_id, pm_order_id, strategy, broker_id, quantity, filled_qty, start_ts, end_ts, arrival_price, status from trader_order where run_id=:r",
    "fill": "select fill_id, trader_order_id, pm_order_id, permno, side, venue_mic, broker_id, exec_ts, price, quantity, liquidity, fees_usd, commission_usd, fill_model from fill where run_id=:r",
    "flow": "select date, amount_usd, units, unit_nav_used, is_jump from flow where run_id=:r order by date",
    "netting": "select * from netting where run_id=:r",
    "tca_buckets": None,
}
REFUSED = {"market_daily", "dsf_v2", "daily_analytics", "classified_trades", "auction_imbalance", "reference_listing", "stkdistributions",
           "stksecurityinfohist", "position_daily_prices", "security_map"}


class ExportIn(BaseModel):
    run_id: str
    table: str
    format: str = "csv"


@router.post("/exports", tags=["exports"])
def export(body: ExportIn, u: User = Depends(current_user)) -> StreamingResponse:
    if body.table in REFUSED or body.table not in EXPORTABLE:
        raise HTTPException(403, f"'{body.table}' is not exportable: exports carry derived aggregates and order-level results only "
                                 f"(allowed: {sorted(EXPORTABLE)})")
    run = one("select run_id, data_label, config_hash from run where run_id=:r", r=body.run_id)
    if not run:
        raise HTTPException(404, "run not found")
    if body.table == "tca_buckets":
        from simp.api.routers.tca import tca as tca_ep

        g = tca_ep(body.run_id, "size_bucket", "ACTUAL", "exec", None, None, u)
        data = [{"size_bucket": x["key"].get("size_bucket"), **{k: v for k, v in x.items() if k != "key"}} for x in g["groups"]]
    else:
        data = rows(EXPORTABLE[body.table] or "", r=body.run_id)
    label = run["data_label"] or "UNKNOWN"
    src = "SYNTHETIC data (not real CRSP/BMLL)" if label == "SYNTHETIC" else "derived from CRSP (WRDS) and BMLL; licensed — not for redistribution"
    mark = f"SIMP export | run {body.run_id} | config {run['config_hash'][:12]} | {src} | {datetime.now(UTC).isoformat()} | {u.username}"
    eid = ulid_from(datetime.now(UTC), int(hashlib.sha256(f"{body.run_id}{time.time_ns()}".encode()).hexdigest()[:20], 16))
    with get_engine().begin() as c:
        c.execute(S.export.insert().values(export_id=eid, run_id=body.run_id, table_name=body.table, format=body.format, rows=len(data),
                                           watermark=mark, created_by=u.username, created_at=datetime.now(UTC)))
    fname = f"simp_{body.table}_{body.run_id}.{body.format}"
    if body.format == "parquet":
        import polars as pl

        buf = io.BytesIO()
        df = pl.DataFrame([{k: (json.dumps(v) if isinstance(v, dict | list) else v) for k, v in r.items()} for r in data], infer_schema_length=None)
        num = [c for c, d in df.schema.items() if str(d).startswith("Decimal")]
        df = df.with_columns([pl.col(c).cast(pl.Float64) for c in num])
        import pyarrow.parquet as pq

        t = df.to_arrow().replace_schema_metadata({"simp_watermark": mark})
        pq.write_table(t, buf)
        return StreamingResponse(iter([buf.getvalue()]), media_type="application/octet-stream",
                                 headers={"Content-Disposition": f'attachment; filename="{fname}"', "X-SIMP-Watermark": mark})
    out = io.StringIO()
    out.write(f"# {mark}\n")
    if data:
        w = csv.DictWriter(out, fieldnames=list(data[0].keys()))
        w.writeheader()
        for r in data:
            w.writerow({k: (json.dumps(v, default=str) if isinstance(v, dict | list) else v) for k, v in r.items()})
    return StreamingResponse(iter([out.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{fname}"', "X-SIMP-Watermark": mark})


__all__ = ["router", "Request"]
