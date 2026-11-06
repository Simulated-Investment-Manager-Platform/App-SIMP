"""Market explorer, global search and data coverage. Shows derived BMLL figures only."""

from __future__ import annotations

import math
from datetime import date
from functools import lru_cache
from typing import Any

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query

from simp.api.deps import User, current_user, rows
from simp.data.lake.store import Lake
from simp.data.market import VENUES, MarketData, load_market
from simp.errors import DataMissingError

router = APIRouter(tags=["market"])


def market_data() -> MarketData | None:
    snaps = Lake().snapshots()
    if not snaps:
        return None
    return load_market(snaps[-1]["snapshot_hash"], str(Lake().root))


@lru_cache(maxsize=1)
def _ticker_cache(snapshot: str) -> dict[int, str]:
    md = load_market(snapshot, str(Lake().root))
    return {int(p): t for p, t in zip(md.permnos, md.tickers, strict=True)}


def ticker_map() -> dict[int, str]:
    snaps = Lake().snapshots()
    return _ticker_cache(snaps[-1]["snapshot_hash"]) if snaps else {}


def _f(x: Any) -> float | None:
    v = float(x)
    return v if math.isfinite(v) else None


@router.get("/search")
def search(q: str, _: User = Depends(current_user)) -> dict[str, Any]:
    md = market_data()
    out: dict[str, Any] = {"securities": [], "orders": [], "runs": []}
    qq = q.strip().upper()
    if md is not None and qq:
        for p, t in zip(md.permnos, md.tickers, strict=True):
            if t.upper().startswith(qq) or str(p) == qq:
                out["securities"].append({"permno": int(p), "ticker": t})
            if len(out["securities"]) >= 10:
                break
    if len(qq) >= 6:
        out["orders"] = rows(
            "select pm_order_id, run_id, permno, exec_date, side, reason from pm_order where pm_order_id like :q limit 10",
            q=qq + "%",
        )
        out["runs"] = rows("select run_id, name, status from run where run_id like :q limit 5", q=qq + "%")
    return out


@router.get("/securities/default")
def default_security(_: User = Depends(current_user)) -> dict[str, Any]:
    """The security the market explorer opens on: the largest index member on the last session
    of the latest snapshot (PERMNOs differ between synthetic and live data)."""
    md = market_data()
    if md is None:
        raise HTTPException(409, "no data snapshot — Data -> Pull first")
    i = md.D - 1
    cap = np.nan_to_num(md.prc[i]) * np.nan_to_num(md.shrout[i])
    j = int(np.argmax(np.where(md.member[i], cap, 0.0) if md.member[i].any() else cap))
    return {"permno": int(md.permnos[j]), "ticker": md.tickers[j]}


@router.get("/securities/{permno}/market")
def security_market(
    permno: int,
    on: date | None = Query(None, alias="date"),
    window: int = 60,
    _: User = Depends(current_user),
) -> dict[str, Any]:
    md = market_data()
    if md is None:
        raise HTTPException(409, "no data snapshot — Data -> Pull first")
    if permno not in md.pos:
        raise HTTPException(404, f"permno {permno} not in snapshot")
    j = md.j(permno)
    d = on or md.sessions[-1]
    try:
        i = md.cal.index(md.cal.on_or_before(d))
    except KeyError as e:
        raise HTTPException(404, f"no session on or before {d}") from e
    lo = max(0, i - window)
    series = []
    for k in range(lo, min(md.D, i + 6)):
        if not math.isfinite(float(md.prc[k, j])):
            continue
        series.append(
            {
                "date": md.sessions[k],
                "close": _f(md.prc[k, j]),
                "vwap": _f(md.vwap[k, j]),
                "twap": _f(md.twap[k, j]),
                "auction_price": _f(md.auc_price[k, j]),
                "auction_volume": _f(md.auc_vol[k, j]),
                "volume": _f(md.volume[k, j]),
                "imbalance_qty": _f(md.imbalance[k, j]),
                "adv20": _f(md.adv20[k, j]),
                "adv60": _f(md.adv60[k, j]),
                "spread_bps": _f(md.spread_bps[k, j]),
                "vol_intraday": _f(md.vol_intraday[k, j]),
                "sweep_100k_bps": _f(md.sweep_bps[k, j]),
                "dislocation_bps": _f(md.dislocation_bps[k, j]),
                "member": bool(md.member[k, j]),
                "auction_price_fallback": bool(md.auc_price_fb[k, j]),
                "auction_volume_fallback": bool(md.auc_vol_fb[k, j]),
            }
        )
    lake = Lake()
    sm = lake.read(md.snapshot, "derived", "security_map")
    import polars as pl

    hist = (
        sm.filter(pl.col("permno") == permno)
        .with_columns(pl.col("valid_from", "valid_to").cast(pl.Utf8))
        .to_dicts()
    )
    from simp.execution.intraday import IntradayService

    intra = IntradayService(md)
    p = intra.paths(i)
    acts = [
        {
            "kind": a.kind,
            "ex": a.ex,
            "pay": a.pay,
            "declared": a.declared,
            "amount": a.amount,
            "factor": a.factor,
        }
        for a in md.actions
        if a.permno == permno
    ]
    return {
        "permno": permno,
        "ticker": md.tickers[j],
        "primary_venue": md.primary[j],
        "listing_id": int(md.listing_id[j]) or None,
        "mapped": bool(md.mapped[j]),
        "date": md.sessions[i],
        "data_label": md.data_label,
        "today": series[-1] if series else None,
        "series": series,
        "venue_share": [{"venue": v, "share": float(md.venue_share[j, k])} for k, v in enumerate(VENUES)],
        "mapping_history": hist,
        "corporate_actions": acts,
        "intraday": [[k, float(p[j, k])] for k in range(0, 390, 2)] if p is not None else None,
        "membership": [{"start": a, "end": b} for a, b in md.mbr_start.get(permno, [])],
    }


@router.get("/data/coverage")
def coverage(_: User = Depends(current_user)) -> dict[str, Any]:
    lake = Lake()
    ms = lake.manifests()
    latest: dict[str, dict[str, Any]] = {}
    for m in ms:
        k = f"{m['source']}/{m['dataset']}"
        if k not in latest or m["pulled_at"] > latest[k]["pulled_at"]:
            latest[k] = m
    datasets = []
    for k, m in sorted(latest.items()):
        src, ds = k.split("/")
        dates = lake.partition_dates(src, ds)
        datasets.append(
            {
                "dataset": k,
                "rows": m["row_count"],
                "label": m["data_label"],
                "pulled_at": m["pulled_at"],
                "manifest": m["manifest_hash"],
                "first": dates[0] if dates else None,
                "last": dates[-1] if dates else None,
                "partitions": len(dates),
                "sdk": m["sdk_version"],
            }
        )
    md = market_data()
    mapping = None
    if md is not None:
        cap = np.nan_to_num(md.shrout * md.prc) * md.member
        last = md.D - 1
        mapping = {
            "names": md.N,
            "mapped": int(md.mapped.sum()),
            "member_weight_mapped": float((cap[last] * md.mapped).sum() / max(cap[last].sum(), 1)),
            "unmapped": [
                {"permno": int(p), "ticker": md.tickers[k]}
                for k, p in enumerate(md.permnos)
                if not md.mapped[k]
            ],
        }
    snaps = lake.snapshots()
    from simp.settings import get_settings

    s = get_settings()
    label = datasets[0]["label"] if datasets else None
    return {
        "datasets": datasets,
        "snapshots": snaps[-10:],
        "mapping": mapping,
        "data_source": s.data_source,
        "data_label": label,
        "wrds": {
            "configured": bool(s.wrds_username and s.wrds_password),
            "release": next(
                (f"{d['first']}..{d['last']}" for d in datasets if d["dataset"] == "crsp/dsf_v2"), None
            ),
        },
        "bmll": {
            "configured": bool(s.bmll_key_path),
            "daily_analytics": next((d for d in datasets if d["dataset"] == "bmll/daily_analytics"), None),
        },
    }


@router.get("/data/manifests")
def manifests(_: User = Depends(current_user)) -> list[dict[str, Any]]:
    return [
        {
            k: m[k]
            for k in (
                "source",
                "dataset",
                "row_count",
                "data_label",
                "pulled_at",
                "manifest_hash",
                "sdk_version",
                "query_args",
            )
        }
        | {"files": len(m["files"])}
        for m in Lake().manifests()
    ]


__all__ = ["router", "DataMissingError"]
