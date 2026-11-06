"""Load a pinned lake snapshot into dense (session x security) arrays for the engine.

The engine never queries the lake mid-run: everything it may read is materialised here once,
and a look-ahead guard (``MarketData.guard``) rejects any read dated after the decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache

import numpy as np
import polars as pl

from simp.accounting.calendar import SessionCalendar
from simp.data.lake.store import Lake
from simp.data.mapping.isin import exch_to_mic
from simp.errors import LookAheadError

# Distribution types the engine understands: CD cash dividend, FS forward split, SO spin-off,
# RI rights, MG merger/delisting. CRSP CIZ ``distype`` codes are mapped here; anything not
# mapped is classified from the amounts, and an unclassifiable event becomes "UNK", which stops
# the run if the fund holds the security (never silently ignored). Extend DISTYPE_MAP when a
# release uses new codes.
DISTYPE_MAP = {
    "CD": "CD",
    "FS": "FS",
    "SO": "SO",
    "RI": "RI",
    "MG": "MG",
    # CRSP CIZ v2 codes
    "SD": "CD",  # special (cash) dividend
    "ROC": "CD",  # return of capital, paid in cash
    "FRS": "FS",  # forward or reverse split; share factor is 1 + disfacshr
}
# CIZ cash (CP) and stock (SP) payments with disfacshr = -1 retire the security: merger or
# acquisition proceeds on the delisting date.
TERMINATING_PAYMENTS = ("CP", "SP")
# CIZ leaves disdeclaredt empty on those delistings (and on some spin-offs, or dates it on/after
# the ex-date). Such deals are public well before they complete, so an unsupported event (SO, RI,
# MG) without a usable declaration date is treated as declared this many sessions before the
# ex-date: enough for the strategy to exclude the name and exit before the event.
IMPUTED_NOTICE_SESSIONS = 5
UNSUPPORTED_KINDS = ("SO", "RI", "MG")


def classify_distribution(r: dict[str, object]) -> str:
    code = str(r.get("distype") or "").strip().upper()
    amt = float(r.get("disdivamt") or 0.0)  # type: ignore[arg-type]
    fac = float(r.get("disfacshr") or 0.0)  # type: ignore[arg-type]
    if code in TERMINATING_PAYMENTS and fac <= -1.0 + 1e-9:
        return "MG"
    if code == "SP":  # stock of another company, security survives: a spin-off
        return "SO"
    if code in DISTYPE_MAP:
        return DISTYPE_MAP[code]
    if fac > 0 and amt == 0:
        return "FS"
    if amt > 0 and fac == 0:
        return "CD"
    return "UNK"


VENUES = ["XNYS", "XNAS", "ARCX", "BATS", "EDGX", "IEXG", "MEMX", "DARK"]


@dataclass
class CorpAction:
    j: int
    permno: int
    kind: str  # CD cash dividend, FS split, SO spin-off, RI rights, MG merger
    ex_i: int
    ex: date
    pay: date | None
    declared: date | None
    amount: float
    factor: float
    provisional: bool = False
    declared_imputed: bool = False


@dataclass
class MarketData:
    snapshot: str
    data_label: str
    cal: SessionCalendar
    permnos: np.ndarray
    tickers: list[str]
    primary: list[str]
    listing_id: np.ndarray
    prc: np.ndarray
    ret: np.ndarray
    retx: np.ndarray
    shrout: np.ndarray
    volume: np.ndarray
    opn: np.ndarray
    member: np.ndarray
    vwretd: np.ndarray
    vwretx: np.ndarray
    sp_tr: np.ndarray
    vwap: np.ndarray
    twap: np.ndarray
    auc_price: np.ndarray
    auc_vol: np.ndarray
    auc_med20: np.ndarray
    imbalance: np.ndarray
    adv20: np.ndarray
    adv60: np.ndarray
    spread_bps: np.ndarray
    vol_intraday: np.ndarray
    sweep_bps: np.ndarray
    dislocation_bps: np.ndarray
    auc_price_fb: np.ndarray
    auc_vol_fb: np.ndarray
    venue_share: np.ndarray  # (N, len(VENUES))
    actions: list[CorpAction]
    mapped: np.ndarray  # bool (N,) — has a BMLL mapping for the whole window it trades
    data_seed: int | None = None  # synthetic generator seed (intraday paths); None for live data
    mbr_start: dict[int, list[tuple[date, date]]] = field(default_factory=dict)
    pos: dict[int, int] = field(default_factory=dict)
    # CRSP stkdelists: permno -> (delisting date, delisting return or None)
    delisted: dict[int, tuple[date, float | None]] = field(default_factory=dict)
    _horizon: int | None = None

    @property
    def sessions(self) -> list[date]:
        return self.cal.sessions

    @property
    def D(self) -> int:
        return len(self.cal)

    @property
    def N(self) -> int:
        return int(self.permnos.shape[0])

    def j(self, permno: int) -> int:
        return self.pos[int(permno)]

    # ---------- look-ahead guard
    def set_horizon(self, i: int | None) -> None:
        self._horizon = i

    def guard(self, i: int) -> None:
        if self._horizon is not None and i > self._horizon:
            raise LookAheadError(
                f"read of session {self.sessions[i]} after decision horizon {self.sessions[self._horizon]}",
                session=self.sessions[self._horizon],
            )


def _matrix(
    df: pl.DataFrame, col: str, di: np.ndarray, ji: np.ndarray, shape: tuple[int, int], fill: float = np.nan
) -> np.ndarray:
    m = np.full(shape, fill)
    m[di, ji] = df[col].to_numpy().astype(float)
    return m


@lru_cache(maxsize=4)
def load_market(snapshot: str, lake_root: str | None = None) -> MarketData:
    from pathlib import Path

    lake = Lake(Path(lake_root) if lake_root else None)
    m0 = lake.manifest_for(snapshot, "crsp", "dsf_v2")
    label = m0["data_label"]
    data_seed = m0["query_args"].get("seed") if label == "SYNTHETIC" else None
    dsf = lake.read(snapshot, "crsp", "dsf_v2")
    sessions = sorted(set(dsf["dlycaldt"].to_list()))
    cal = SessionCalendar(sessions)
    permnos = np.array(sorted(set(dsf["permno"].to_list())), dtype=np.int64)
    pos = {int(p): k for k, p in enumerate(permnos)}
    D, N = len(sessions), len(permnos)
    dpos = {d: i for i, d in enumerate(sessions)}

    di = np.array([dpos[d] for d in dsf["dlycaldt"].to_list()])
    ji = np.array([pos[int(p)] for p in dsf["permno"].to_list()])
    shp = (D, N)
    prc = _matrix(dsf, "dlyprc", di, ji, shp)
    ret = _matrix(dsf, "dlyret", di, ji, shp)
    retx = _matrix(dsf, "dlyretx", di, ji, shp)
    shrout = _matrix(dsf, "shrout", di, ji, shp) * 1e3
    volume = _matrix(dsf, "dlyvol", di, ji, shp)
    opn = _matrix(dsf, "dlyopen", di, ji, shp)

    mbr = lake.read(snapshot, "crsp", "dsp500list_v2")
    member = np.zeros(shp, bool)
    sarr = np.array(sessions, dtype="datetime64[D]")
    mbr_start: dict[int, list[tuple[date, date]]] = {}
    for p, a, b in mbr.with_columns(pl.col("mbrenddt").fill_null(date(9999, 12, 31))).iter_rows():
        if int(p) not in pos:
            continue
        mask = (sarr >= np.datetime64(a)) & (sarr <= np.datetime64(b))
        member[mask, pos[int(p)]] = True
        mbr_start.setdefault(int(p), []).append((a, b))
    member &= ~np.isnan(prc)

    bench = lake.read(snapshot, "crsp", "dsp500").sort("caldt")
    bmap = {d: (a, b) for d, a, b in bench.select("caldt", "vwretd", "vwretx").iter_rows()}
    vwretd = np.array([bmap.get(d, (np.nan, np.nan))[0] for d in sessions], dtype=float)
    vwretx = np.array([bmap.get(d, (np.nan, np.nan))[1] for d in sessions], dtype=float)
    if "vendor/sp500_tr" in lake.snapshot(snapshot)["manifests"]:
        tr = lake.read(snapshot, "vendor", "sp500_tr")
        tmap = dict(tr.select("caldt", "tr_return").iter_rows())
        sp_tr = np.array([tmap.get(d, np.nan) for d in sessions], dtype=float)
    else:  # benchmark (b) not licensed in this snapshot; figures using it are reported as unavailable
        sp_tr = np.full(D, np.nan)

    md = lake.read(snapshot, "derived", "market_daily")
    md = md.filter(pl.col("permno").is_in(list(pos)) & pl.col("date").is_in(sessions))
    mdi = np.array([dpos[d] for d in md["date"].to_list()])
    mji = np.array([pos[int(p)] for p in md["permno"].to_list()])

    def M(c: str, fill: float = np.nan) -> np.ndarray:
        return _matrix(md, c, mdi, mji, shp, fill)

    # Days a traded name has no BMLL row (a gap in its listing map) price at the CRSP close,
    # as derived/market_daily already does for missing values inside a row.
    vwap, twap = M("vwap"), M("twap")
    gap = np.isfinite(prc)
    vwap = np.where(np.isfinite(vwap) | ~gap, vwap, prc)
    twap = np.where(np.isfinite(twap) | ~gap, twap, prc)

    smap = lake.read(snapshot, "derived", "security_map")
    listing = np.zeros(N, np.int64)
    mapped = np.zeros(N, bool)
    for p, lid in smap.select("permno", "listing_id").iter_rows():
        if int(p) in pos and lid is not None:
            listing[pos[int(p)]] = int(lid)
            mapped[pos[int(p)]] = True

    secinfo = lake.read(snapshot, "crsp", "stksecurityinfohist").sort("secinfostartdt")
    tick = {int(p): t for p, t in secinfo.select("permno", "ticker").iter_rows()}
    # latest listing venue the desk can route to; CIZ marks delisted rows "X" (not listed)
    prim: dict[int, str] = {}
    for p, e in secinfo.select("permno", "primaryexch").iter_rows():
        mic = exch_to_mic(e)
        if mic in VENUES and mic != "DARK":
            prim[int(p)] = mic
    tickers = [tick.get(int(p), str(p)) for p in permnos]
    primary = [prim.get(int(p), "XNYS") for p in permnos]

    vs = lake.read(snapshot, "derived", "venue_share")
    vshare = np.zeros((N, len(VENUES)))
    for p, v, s in vs.iter_rows():
        if int(p) in pos and v in VENUES:
            vshare[pos[int(p)], VENUES.index(v)] = s
    rs = vshare.sum(1, keepdims=True)
    vshare = np.where(rs > 0, vshare / np.where(rs > 0, rs, 1), 1.0 / len(VENUES))

    dist = lake.read(snapshot, "crsp", "stkdistributions")
    actions: list[CorpAction] = []
    for r in dist.iter_rows(named=True):
        p = int(r["permno"])
        if p not in pos or r["disexdt"] not in dpos:
            continue
        kind = classify_distribution(r)
        declared = r.get("disdeclaredt")
        ex_i = dpos[r["disexdt"]]
        # usable only if the fund can still trade out before the ex-date (decide at a close at
        # least two sessions ahead, exit at the next session's close)
        imputed = kind in UNSUPPORTED_KINDS and (
            declared is None or ex_i < 2 or declared > sessions[ex_i - 2]
        )
        if imputed:
            declared = sessions[max(ex_i - IMPUTED_NOTICE_SESSIONS, 0)]
        actions.append(
            CorpAction(
                j=pos[p],
                permno=p,
                kind=kind,
                ex_i=dpos[r["disexdt"]],
                ex=r["disexdt"],
                pay=r["dispaydt"],
                declared=declared,
                amount=float(r["disdivamt"] or 0.0),
                factor=float(r["disfacshr"] or 0.0) + 1.0 if kind == "FS" else 1.0,
                provisional=kind == "CD" and r["dispaydt"] is None,
                declared_imputed=imputed,
            )
        )
    actions.sort(key=lambda a: (a.ex_i, a.permno, a.kind))

    delisted: dict[int, tuple[date, float | None]] = {}
    if "crsp/stkdelists" in lake.snapshot(snapshot)["manifests"]:
        dl = lake.read(snapshot, "crsp", "stkdelists")
        for p, d, r in dl.select("permno", "delistingdt", "delret").iter_rows():
            if int(p) in pos and d is not None:
                delisted[int(p)] = (d, None if r is None or not np.isfinite(r) else float(r))

    return MarketData(
        snapshot=snapshot,
        data_label=label,
        cal=cal,
        permnos=permnos,
        tickers=tickers,
        primary=primary,
        listing_id=listing,
        prc=prc,
        ret=ret,
        retx=retx,
        shrout=shrout,
        volume=volume,
        opn=opn,
        member=member,
        vwretd=vwretd,
        vwretx=vwretx,
        sp_tr=sp_tr,
        vwap=vwap,
        twap=twap,
        auc_price=M("close_auction_price"),
        auc_vol=M("auction_volume", 0.0),
        auc_med20=M("auc_median20"),
        imbalance=M("imbalance_qty", 0.0),
        adv20=M("adv20"),
        adv60=M("adv60"),
        spread_bps=M("spread_bps", 5.0),
        vol_intraday=M("vol_intraday", 0.015),
        sweep_bps=M("sweep_100k_bps", 10.0),
        dislocation_bps=M("dislocation_bps", 0.0),
        auc_price_fb=M("auction_price_fallback", 0.0) > 0,
        auc_vol_fb=M("auction_volume_fallback", 0.0) > 0,
        venue_share=vshare,
        actions=actions,
        mapped=mapped,
        mbr_start=mbr_start,
        pos=pos,
        delisted=delisted,
        data_seed=data_seed,
    )
