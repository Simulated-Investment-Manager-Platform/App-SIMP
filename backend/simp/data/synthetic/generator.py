"""Synthetic, schema-identical CRSP + BMLL data.

Used when WRDS / BMLL cannot be reached (or for tests). Every table matches the CIZ (v2)
and BMLL Data Lab column names in ``simp.data.lake.contracts`` and is labelled SYNTHETIC
in its manifest, in the API and in the UI. Nothing here is real market data.

The world it builds:
* ~580 securities, ~503 S&P members at any time, heavy-tailed caps (top name ~7%).
* Market + sector + idiosyncratic returns; quarterly dividends; integer splits; a few
  spin-offs, rights issues and mergers (the "unsupported corporate actions" of the handoff),
  each with a declaration date so exclusions never need look-ahead.
* Quarterly adds/deletes at the third-Friday effective date, off-cycle deletes on mergers.
* Share-count updates at each quarterly reference date.
* BMLL: primary listing (OPOL) on XNYS/XNAS/ARCX/BATS plus an @SIP listing per instrument;
  daily VWAP/TWAP from a minute-level intraday path; closing/opening auction volumes
  (much larger on index-event dates); signed closing imbalance; spreads; venue mix.
* Mapping edge cases: share classes, a dotted ticker, ISINs missing in BMLL reference
  (ticker fallback) and one name that cannot be mapped (review queue).
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import polars as pl

from simp.accounting.calendar import SessionCalendar, nyse_sessions, quarterly_effective_dates
from simp.data.mapping.isin import isin_from_cusip9
from simp.rng import component_rng

DATA_START = date(2022, 1, 3)
DATA_END = date(2026, 7, 31)
N_MINUTES = 390
LIT = ["XNYS", "XNAS", "ARCX", "BATS", "EDGX", "IEXG", "MEMX"]
SECTORS = ["TECH", "HLTH", "FINL", "COND", "COMM", "INDU", "CONS", "ENRG", "UTIL", "REAL", "MATR"]


@dataclass
class SyntheticWorld:
    tables: dict[tuple[str, str], pl.DataFrame]
    sessions: list[date]
    permnos: np.ndarray
    seed: int


def _tickers(rng: np.random.Generator, n: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    letters = np.array(list(string.ascii_uppercase))
    while len(out) < n:
        k = int(rng.choice([2, 3, 3, 4, 4, 4]))
        t = "".join(rng.choice(letters, k))
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _cusip_check(c8: str) -> str:
    total = 0
    for i, ch in enumerate(c8):
        v = int(ch) if ch.isdigit() else ord(ch) - 55
        if i % 2 == 1:
            v *= 2
        total += v // 10 + v % 10
    return str((10 - total % 10) % 10)


def _intraday_shape() -> np.ndarray:
    x = np.linspace(0, 1, N_MINUTES)
    w = 1.0 + 2.2 * (1 - x) ** 6 + 3.0 * x**8
    w[-10:] *= 1.6
    return w / w.sum()


VOLUME_CURVE = _intraday_shape()


def intraday_paths(
    seed: int, day: date, opens: np.ndarray, last_cont: np.ndarray, sig_d: np.ndarray
) -> np.ndarray:
    """Minute mid-price paths (N, 390): Brownian bridge in log space from open to the last
    continuous price, with intraday vol ``sig_d``. Deterministic in (seed, day)."""
    rng = component_rng(seed, "intraday", day)
    n = opens.shape[0]
    z = rng.standard_normal((n, N_MINUTES))
    step = (sig_d / np.sqrt(N_MINUTES))[:, None]
    w = np.cumsum(z * step, axis=1)
    t = np.linspace(1.0 / N_MINUTES, 1.0, N_MINUTES)[None, :]
    bridge = w - t * w[:, -1:]
    lo, lc = np.log(opens)[:, None], np.log(last_cont)[:, None]
    out: np.ndarray = np.exp(lo + t * (lc - lo) + bridge)
    return out


def generate(seed: int = 20260321, start: date = DATA_START, end: date = DATA_END) -> SyntheticWorld:
    sessions = nyse_sessions(start, end)
    cal = SessionCalendar(sessions)
    D = len(sessions)
    rng = component_rng(seed, "universe")

    # ------------------------------------------------------------------ universe
    n_init, n_pool = 503, 77
    N = n_init + n_pool
    permnos = np.arange(10001, 10001 + N * 7, 7)[:N].astype(np.int64)
    tickers = _tickers(rng, N)
    sector = rng.integers(0, len(SECTORS), N)
    logcap = rng.normal(np.log(32e9), 1.05, N)
    order = np.argsort(-logcap)
    mega = np.log(np.array([3.3e12, 3.0e12, 2.7e12, 1.9e12, 1.5e12, 1.15e12, 0.95e12, 0.8e12]))
    logcap[order[: len(mega)]] = mega
    # pool names (not members at start) sit near the bottom of the index
    member0 = np.zeros(N, bool)
    member0[order[:n_init]] = True
    pool_idx = np.where(~member0)[0]
    logcap[pool_idx] = rng.normal(np.log(14e9), 0.35, len(pool_idx))
    cap0 = np.exp(logcap)
    price0 = np.exp(rng.normal(np.log(110), 0.75, N)).clip(12, 1200)
    shrout0 = cap0 / price0 / 1e3  # CRSP shrout is in thousands of shares

    # share-class pair (same issuer, two permnos) and a dotted ticker
    sc_a, sc_b = order[3], order[60]
    tickers[sc_b] = tickers[sc_a] + "L"
    dotted = order[120]
    tickers[dotted] = tickers[dotted][:3] + ".B"
    primary = rng.choice(["XNYS", "XNAS", "ARCX", "BATS"], N, p=[0.55, 0.42, 0.02, 0.01])

    # ------------------------------------------------------------------ returns
    rmk = component_rng(seed, "market")
    mkt_mu = np.full(D, 0.25 / 252)
    for i, d in enumerate(sessions):  # a 2022 drawdown, then recovery (stylised)
        if date(2022, 4, 1) <= d <= date(2022, 10, 12):
            mkt_mu[i] = -0.38 / 252
        elif date(2025, 2, 20) <= d <= date(2025, 4, 8):
            mkt_mu[i] = -0.95 / 252
    mkt = mkt_mu + rmk.standard_normal(D) * (0.145 / np.sqrt(252))
    beta = rng.normal(1.0, 0.25, N).clip(0.3, 1.9)
    sec_f = component_rng(seed, "sector").standard_normal((D, len(SECTORS))) * (0.07 / np.sqrt(252))
    idio_vol = rng.uniform(0.16, 0.36, N) / np.sqrt(252)
    eps = component_rng(seed, "idio").standard_normal((D, N)) * idio_vol[None, :]
    rtot = beta[None, :] * mkt[:, None] + sec_f[:, sector] + eps  # total return incl. dividends

    # ------------------------------------------------------------------ corporate actions
    rca = component_rng(seed, "corp_actions")
    dist_rows: list[dict[str, object]] = []
    div_cash = np.zeros((D, N))  # cash dividend per share on ex-date (pre-split basis of that day)
    split_fac = np.ones((D, N))  # shares multiply by this on ex-date
    nontrade_value = np.zeros((D, N))  # spin-off / rights value per share on ex-date
    payers = rca.random(N) < 0.78
    yld = rca.uniform(0.005, 0.04, N)
    month_off = rca.integers(0, 3, N)
    day_off = rca.integers(1, 25, N)
    for j in np.where(payers)[0]:
        for y in range(start.year, end.year + 1):
            for q in range(4):
                m = 1 + q * 3 + int(month_off[j])
                try:
                    ex = cal.on_or_after(date(y, m, int(day_off[j])))
                except KeyError:
                    continue
                if not (start + timedelta(days=10) <= ex <= end):
                    continue
                i = cal.index(ex)
                amt = round(float(price0[j] * yld[j] / 4 * rca.uniform(0.9, 1.1)), 4)
                div_cash[i, j] += amt
                provisional = rca.random() < 0.03
                pay = None if provisional else ex + timedelta(days=int(rca.integers(12, 35)))
                rec = ex if ex >= date(2024, 5, 28) else cal.offset(ex, 1) if i + 1 < D else ex
                dist_rows.append(
                    dict(permno=int(permnos[j]), disexdt=ex, dispaydt=pay, disrecorddt=rec,
                         disdeclaredt=ex - timedelta(days=int(rca.integers(20, 45))),
                         disdivamt=amt, disfacpr=0.0, disfacshr=0.0, distype="CD")
                )
    # integer splits
    split_names = rca.choice(np.where(member0)[0], 12, replace=False)
    for j in split_names:
        ex = sessions[int(rca.integers(60, D - 40))]
        f = float(rca.choice([2, 3, 4, 10, 20], p=[0.45, 0.2, 0.2, 0.1, 0.05]))
        i = cal.index(ex)
        if div_cash[i, j] > 0:  # never same-day split + dividend in the synthetic world
            ex = cal.offset(ex, 1)
            i += 1
        split_fac[i, j] = f
        dist_rows.append(
            dict(permno=int(permnos[j]), disexdt=ex, dispaydt=ex, disrecorddt=ex,
                 disdeclaredt=ex - timedelta(days=30), disdivamt=0.0, disfacpr=f - 1, disfacshr=f - 1,
                 distype="FS")
        )

    # ------------------------------------------------------------------ membership schedule
    eff_dates = quarterly_effective_dates(cal, date(2022, 3, 1), end)
    member = np.zeros((D, N), bool)
    member[:, :] = member0[None, :]
    start_dt = {j: sessions[0] - timedelta(days=int(rng.integers(400, 9000))) for j in np.where(member0)[0]}
    orig_start = dict(start_dt)
    end_dt: dict[int, date] = {}
    available_pool = list(pool_idx)
    rng.shuffle(available_pool)
    delist_rows: list[dict[str, object]] = []
    alive_until = np.full(N, D - 1)

    def do_delete(j: int, last: date) -> None:
        end_dt[j] = last
        member[cal.index(last) + 1 :, j] = False

    def do_add(j: int, first: date) -> None:
        start_dt[j] = first
        member[cal.index(first) :, j] = True

    unsupported: list[tuple[int, date, str]] = []
    for q, eff in enumerate(eff_dates):
        ei = cal.index(eff)
        if ei + 1 >= D:
            break
        current = np.where(member[ei])[0]
        k = int(rng.integers(3, 6))
        caps_now = cap0[current] * np.exp(np.cumsum(rtot[: ei + 1, current], axis=0)[-1])
        smallest = current[np.argsort(caps_now)[:40]]
        dels = rng.choice(smallest, min(k, len(smallest)), replace=False)
        adds = [available_pool.pop() for _ in range(min(k, len(available_pool)))]
        for j in dels:
            do_delete(int(j), eff)
            available_pool.insert(0, int(j))
        for j in adds:
            do_add(int(j), cal.offset(eff, 1))
        # unsupported corporate actions declared before next quarter, ex inside next quarter
        if q + 1 < len(eff_dates):
            nxt = cal.index(eff_dates[q + 1]) if eff_dates[q + 1] in cal._pos else D - 1
            members_after = np.where(member[min(ei + 1, D - 1)])[0]
            caps_after = cap0[members_after] * np.exp(np.cumsum(rtot[: ei + 1, members_after], axis=0)[-1])
            members_after = members_after[np.argsort(-caps_after)[30:]]  # never the mega caps
            for _ in range(int(rng.integers(1, 4))):
                j = int(rng.choice(members_after))
                exi = int(rng.integers(ei + 8, max(ei + 9, nxt - 2)))
                kind = str(rng.choice(["SPINOFF", "RIGHTS", "MERGER"], p=[0.45, 0.2, 0.35]))
                unsupported.append((j, sessions[min(exi, D - 1)], kind))

    merged: set[int] = set()
    for j, ex, kind in unsupported:
        i = cal.index(ex)
        declared = cal.offset(ex, -int(rca.integers(12, 30)))
        if kind in ("SPINOFF", "RIGHTS"):
            frac = rca.uniform(0.04, 0.14) if kind == "SPINOFF" else rca.uniform(0.005, 0.02)
            # value per share in that day's prices (approximation; adjusted below with level)
            nontrade_value[i, j] = -frac  # placeholder fraction, resolved in price loop
            dist_rows.append(
                dict(permno=int(permnos[j]), disexdt=ex, dispaydt=ex + timedelta(days=10), disrecorddt=ex,
                     disdeclaredt=declared, disdivamt=float("nan"), disfacpr=0.0, disfacshr=0.0,
                     distype="SO" if kind == "SPINOFF" else "RI")
            )
        elif j not in merged and alive_until[j] == D - 1 and member[i, j]:
            merged.add(j)
            last = cal.offset(ex, -1)
            alive_until[j] = cal.index(last)
            if member[cal.index(last), j]:
                do_delete(j, last)
                if available_pool:
                    do_add(available_pool.pop(), ex)
            dist_rows.append(
                dict(permno=int(permnos[j]), disexdt=ex, dispaydt=ex, disrecorddt=last, disdeclaredt=declared,
                     disdivamt=0.0, disfacpr=-1.0, disfacshr=-1.0, distype="MG")
            )
            delist_rows.append(
                dict(permno=int(permnos[j]), delistingdt=ex, delret=float(rca.uniform(0.0, 0.03)),
                     delactiontype="MER")
            )

    # ------------------------------------------------------------------ prices
    prc = np.zeros((D, N))
    retx = np.zeros((D, N))
    ret = np.zeros((D, N))
    shr = np.zeros((D, N))
    p = price0.copy()
    s = shrout0.copy()
    ref_dates = {cal.offset(e, -5) for e in eff_dates if cal.index(e) >= 5}
    rshr = component_rng(seed, "shares")
    distamt_fill: dict[tuple[int, int], float] = {}
    for i in range(D):
        if sessions[i] in ref_dates:
            s = s * np.exp(rshr.normal(0, 0.012, N))
        f = split_fac[i]
        p_prev_adj = p / f
        s = s * f
        r = rtot[i]
        div = div_cash[i] / 1.0
        ntv_frac = np.where(nontrade_value[i] < 0, -nontrade_value[i], 0.0)
        ntv = ntv_frac * p_prev_adj
        for j in np.where(ntv > 0)[0]:
            distamt_fill[(i, int(j))] = round(float(ntv[j]), 4)
        p_new = p_prev_adj * (1 + r) - div - ntv
        p_new = np.maximum(p_new, 1.0)
        retx[i] = p_new / p_prev_adj - 1
        ret[i] = (p_new + div + ntv) / p_prev_adj - 1
        if i == 0:
            retx[i] = ret[i] = 0.0
        p = np.round(p_new, 2)
        prc[i] = p
        shr[i] = np.round(s, 0)
    for row in dist_rows:
        if row["distype"] in ("SO", "RI"):
            j = int(np.searchsorted(permnos, row["permno"]))
            i = cal.index(row["disexdt"])  # type: ignore[arg-type]
            row["disdivamt"] = distamt_fill.get((i, j), 0.0)

    alive = np.arange(D)[:, None] <= alive_until[None, :]
    vol_turn = rng.uniform(0.004, 0.011, N)
    rvol = component_rng(seed, "volume")
    eff_idx = {cal.index(e) for e in eff_dates}
    third_fri = {i for i, d in enumerate(sessions) if d.weekday() == 4 and 15 <= d.day <= 21}
    event_mult = np.ones(D)
    for i in third_fri:
        event_mult[i] = 1.35
    for i in eff_idx:
        event_mult[i] = 2.4
    vol = (shr * 1e3) * vol_turn[None, :] * np.exp(rvol.normal(0, 0.3, (D, N))) * (1 + 18 * np.abs(eps))
    vol *= event_mult[:, None]
    vol = np.round(vol, 0)

    # open, bid/ask
    gap = component_rng(seed, "open_gap").normal(0, 0.35, (D, N)) * np.abs(rtot)
    prev_close = np.vstack([prc[:1], prc[:-1]]) / split_fac
    opens = np.round(np.maximum(prev_close * (1 + gap + 0.4 * rtot), 1.0), 2)
    cap_now = prc * shr * 1e3
    spread_bps = (0.8 + 12.0 * (cap_now / 1e9) ** -0.3) * np.exp(component_rng(seed, "spread").normal(0, 0.15, (D, N)))
    half = prc * spread_bps / 2e4
    bid = np.round(prc - half, 2)
    ask = np.round(prc + half, 2)

    # ------------------------------------------------------------------ CRSP tables
    di, ji = np.where(alive)
    dsf = pl.DataFrame(
        {
            "permno": permnos[ji],
            "dlycaldt": pl.Series([sessions[i] for i in di], dtype=pl.Date),
            "dlyprc": prc[di, ji],
            "dlyret": ret[di, ji],
            "dlyretx": retx[di, ji],
            "dlyvol": vol[di, ji].astype(float),
            "dlycap": cap_now[di, ji] / 1e3,
            "shrout": shr[di, ji],
            "dlyfacprc": np.cumprod(split_fac, axis=0)[di, ji],
            "dlyopen": opens[di, ji],
            "dlyclose": prc[di, ji],
            "dlybid": bid[di, ji],
            "dlyask": ask[di, ji],
        }
    )

    cusip8 = []
    for j in range(N):
        base = f"{(permnos[j] * 37) % 1000000:06d}" + f"{10 + j % 90:02d}"
        cusip8.append(base)
    sec_rows = []
    ticker_change = order[200]
    for j in range(N):
        c9 = cusip8[j] + _cusip_check(cusip8[j])
        iss = f"SYNTHETIC CO {tickers[j] if j not in (sc_a, sc_b) else tickers[sc_a]}"
        if j == ticker_change:
            mid = date(2024, 9, 16)
            new8 = cusip8[j][:6] + "99"
            sec_rows.append(dict(permno=int(permnos[j]), secinfostartdt=date(1990, 1, 1), secinfoenddt=mid - timedelta(days=1),
                                 ticker=tickers[j], cusip=cusip8[j], cusip9=c9, primaryexch=primary[j],
                                 securitytype="EQTY", sharetype="NS", issuernm=iss))
            sec_rows.append(dict(permno=int(permnos[j]), secinfostartdt=mid, secinfoenddt=date(9999, 12, 31),
                                 ticker=tickers[j] + "X", cusip=new8, cusip9=new8 + _cusip_check(new8),
                                 primaryexch=primary[j], securitytype="EQTY", sharetype="NS", issuernm=iss))
        else:
            sec_rows.append(dict(permno=int(permnos[j]), secinfostartdt=date(1990, 1, 1), secinfoenddt=date(9999, 12, 31),
                                 ticker=tickers[j], cusip=cusip8[j], cusip9=c9, primaryexch=primary[j],
                                 securitytype="EQTY", sharetype="NS" if j != sc_b else "A", issuernm=iss))
    secinfo = pl.DataFrame(sec_rows).with_columns(pl.col("secinfostartdt", "secinfoenddt").cast(pl.Date))

    # exact membership intervals from the daily member matrix
    intervals = []
    for j in range(N):
        col = member[:, j] & alive[:, j]
        if not col.any():
            continue
        edges = np.diff(np.concatenate([[0], col.astype(int), [0]]))
        starts, ends = np.where(edges == 1)[0], np.where(edges == -1)[0] - 1
        for a, b in zip(starts, ends, strict=True):
            sd = orig_start.get(j, sessions[a]) if a == 0 else sessions[a]
            ed = date(9999, 12, 31) if b == D - 1 and alive_until[j] == D - 1 else sessions[b]
            intervals.append(dict(permno=int(permnos[j]), mbrstartdt=sd, mbrenddt=ed))
    mbr = pl.DataFrame(intervals).with_columns(pl.col("mbrstartdt", "mbrenddt").cast(pl.Date))

    last_alive = {int(permnos[j]): sessions[int(alive_until[j])] for j in range(N)}
    dist_rows = [r for r in dist_rows if r["disexdt"] <= last_alive[int(r["permno"])] or r["distype"] == "MG"]  # type: ignore[operator]
    dist = pl.DataFrame(dist_rows).with_columns(
        pl.col("disexdt", "dispaydt", "disrecorddt", "disdeclaredt").cast(pl.Date)
    ).sort("permno", "disexdt")
    delists = (
        pl.DataFrame(delist_rows, schema={"permno": pl.Int64, "delistingdt": pl.Date, "delret": pl.Float64, "delactiontype": pl.Utf8})
        if delist_rows else pl.DataFrame(schema={"permno": pl.Int64, "delistingdt": pl.Date, "delret": pl.Float64, "delactiontype": pl.Utf8})
    )

    # benchmark: cap-weighted over members using prior-day caps (CRSP convention)
    mem_alive = member & alive
    capm1 = np.vstack([cap_now[:1], cap_now[:-1]]) * mem_alive
    vwretd = (capm1 * ret).sum(1) / np.maximum(capm1.sum(1), 1)
    vwretx = (capm1 * retx).sum(1) / np.maximum(capm1.sum(1), 1)
    vwretd[0] = vwretx[0] = 0.0
    rb = component_rng(seed, "benchmark")
    sprtrn = vwretx + rb.normal(0, 0.8e-4, D)
    spindx = 4500 * np.cumprod(1 + sprtrn)
    dsp500 = pl.DataFrame({"caldt": pl.Series(sessions, dtype=pl.Date), "vwretd": vwretd, "vwretx": vwretx,
                           "sprtrn": sprtrn, "spindx": spindx})
    sp_tr = pl.DataFrame({"caldt": pl.Series(sessions, dtype=pl.Date),
                          "tr_return": vwretd + rb.normal(-0.15e-4 / 252, 1.2e-4, D),
                          "source": ["SYNTHETIC_SP500_TR"] * D})

    # ------------------------------------------------------------------ BMLL reference
    instrument_id = (permnos * 3 + 500000).astype(np.int64)
    ref_rows = []
    lid = 1_000_000
    primary_lid = np.zeros(N, np.int64)
    sip_lid = np.zeros(N, np.int64)
    isin_missing = {int(order[310]), int(order[420])}
    unmappable = int(order[480])
    for j in range(N):
        c9 = cusip8[j] + _cusip_check(cusip8[j])
        isin = isin_from_cusip9(c9)
        figi = f"BBG{(int(permnos[j]) * 7919) % 10**9:09d}"
        tick_b = tickers[j].replace(".", " ")  # BMLL uses a space for share-class tickers
        if j == unmappable:
            tick_b = tickers[j] + "Q"
        venues = [primary[j]] + [v for v in ["ARCX", "BATS", "EDGX", "IEXG", "MEMX", "XNAS"] if v != primary[j]][:3]
        for k, mic in enumerate(venues + ["@SIP"]):
            lid += 1
            if k == 0:
                primary_lid[j] = lid
            if mic == "@SIP":
                sip_lid[j] = lid
            vf, vt = date(2015, 1, 1), date(9999, 12, 31)
            isin_out = "" if (j in isin_missing or j == unmappable) else isin
            ref_rows.append(dict(ListingId=lid, InstrumentId=int(instrument_id[j]), ISIN=isin_out, FIGI=figi,
                                 FIGIComposite=figi[:-1] + "C", Ticker=tick_b, MIC=mic, OPOL=primary[j],
                                 IsPrimary=(k == 0), IsAlive=bool(alive_until[j] == D - 1),
                                 MarketDataLevel="L3" if mic != "@SIP" else "TRADES", ValidFrom=vf, ValidTo=vt))
        if j == ticker_change:
            # BMLL also records the new ISIN after the CUSIP change on the same listing
            new8 = cusip8[j][:6] + "99"
            for r in ref_rows[-len(venues) - 1 :]:
                r2 = dict(r)
                r["ValidTo"] = date(2024, 9, 15)
                r2["ValidFrom"] = date(2024, 9, 16)
                r2["ISIN"] = isin_from_cusip9(new8 + _cusip_check(new8))
                r2["Ticker"] = tickers[j] + "X"
                ref_rows.append(r2)
    reference = pl.DataFrame(ref_rows).with_columns(pl.col("ValidFrom", "ValidTo").cast(pl.Date))

    # ------------------------------------------------------------------ BMLL daily analytics
    sig_d = (np.sqrt(beta**2 * 0.145**2 + 0.07**2 + (idio_vol * np.sqrt(252)) ** 2) / np.sqrt(252) * 0.85)
    auc_share = np.where(np.isin(np.arange(D), list(eff_idx)), 0.27, np.where(np.isin(np.arange(D), list(third_fri)), 0.12, 0.075))
    rauc = component_rng(seed, "auction")
    auc_vol = np.round(vol * auc_share[:, None] * np.exp(rauc.normal(0, 0.25, (D, N))), 0)
    open_vol = np.round(vol * 0.012 * np.exp(rauc.normal(0, 0.3, (D, N))), 0)
    cont_vol = np.maximum(vol - auc_vol - open_vol, 0)
    imb_z = rauc.normal(0, 0.08, (D, N))
    for i in eff_idx:
        imb_z[i] = rauc.normal(0.05, 0.18, N)
    imb_qty = np.round(auc_vol * imb_z, 0)
    disloc_bps = rauc.normal(0, 2.5, (D, N)) + 30 * imb_z * sig_d[None, :] * 1e2
    last_cont = prc / (1 + disloc_bps / 1e4)

    rv = component_rng(seed, "bmll_vol")
    vol_intraday = sig_d[None, :] * np.exp(rv.normal(0, 0.2, (D, N))) * (1 + 10 * np.abs(eps))
    vwap = np.zeros((D, N))
    twap = np.zeros((D, N))
    for i in range(D):
        # paths depend only on stored columns (open, close, dislocation, intraday vol) + seed,
        # so the execution layer can rebuild them from the lake.
        paths = intraday_paths(seed, sessions[i], np.maximum(opens[i], 1.0), np.maximum(last_cont[i], 1.0), vol_intraday[i])
        vwap[i] = paths @ VOLUME_CURVE
        twap[i] = paths.mean(1)
    sweep100 = spread_bps * (1.2 + 3e9 / np.maximum(cap_now, 1e9) * 0.8)
    missing_auction = rv.random((D, N)) < 0.0005
    cap_auction = np.where(missing_auction, np.nan, prc)

    da = pl.DataFrame(
        {
            "ListingId": primary_lid[ji],
            "Date": pl.Series([sessions[i] for i in di], dtype=pl.Date),
            "MIC": primary[ji],
            "VWAP": np.round(vwap[di, ji], 4),
            "TWAP": np.round(twap[di, ji], 4),
            "ClosingAuctionPrice|Executed": cap_auction[di, ji],
            "OpeningAuctionPrice|Executed": opens[di, ji],
            "Spread|RelTWA": spread_bps[di, ji] / 1e4,
            "Spread|RelVWA": spread_bps[di, ji] * 0.92 / 1e4,
            "Spread|AbsTWA": spread_bps[di, ji] / 1e4 * prc[di, ji],
            "Volatility|Intraday": vol_intraday[di, ji],
            "SweepToFill|25kUSDTWASpread": spread_bps[di, ji] * 1.05 / 1e4,
            "SweepToFill|100kUSDTWASpread": sweep100[di, ji] / 1e4,
            "SweepToFill|250kUSDTWASpread": sweep100[di, ji] * 1.6 / 1e4,
            "AuctionOrderImbalance|Abs": np.abs(imb_qty[di, ji]),
            "AuctionOrderImbalance|Notional": imb_qty[di, ji] * prc[di, ji],
            "AuctionDislocation|ClosingLast": disloc_bps[di, ji] / 1e4,
            "Volume": vol[di, ji],
        }
    )
    imb = pl.DataFrame(
        {
            "ListingId": primary_lid[ji],
            "Date": pl.Series([sessions[i] for i in di], dtype=pl.Date),
            "ImbalanceSide": np.where(imb_qty[di, ji] >= 0, "BUY", "SELL"),
            "ImbalanceQuantity": np.abs(imb_qty[di, ji]),
            "PairedQuantity": np.maximum(auc_vol[di, ji] - np.abs(imb_qty[di, ji]), 0),
            "ReferencePrice": np.round(last_cont[di, ji], 2),
            "IndicativeUncrossingPrice": prc[di, ji],
        }
    )

    # venue mix (stable per name, small daily noise) -> classified trades by ExecutionVenue
    rmix = component_rng(seed, "venues")
    base_mix = rmix.dirichlet([8, 8, 4, 4, 3, 2, 2, 6], N)  # 7 lit + DARK
    prim_pos = np.array([LIT.index(m) for m in primary])
    base_mix[np.arange(N), prim_pos] += 0.12
    base_mix /= base_mix.sum(1, keepdims=True)
    venues_all = LIT + ["DARK"]
    parts = []
    dates_col = pl.Series([sessions[i] for i in di], dtype=pl.Date)
    p_mid = prc[di, ji]
    for k, v in enumerate(venues_all):
        vv = cont_vol[di, ji] * base_mix[ji, k]
        parts.append(pl.DataFrame({"ListingId": primary_lid[ji], "Date": dates_col,
                                   "ClassificationName": "LIT_CONTINUOUS" if v != "DARK" else "DARK",
                                   "ExecutionVenue": v, "TradeVolume": np.round(vv, 0), "TradeNotionalUSD": vv * p_mid}))
    missing_aucvol = rv.random(len(di)) < 0.0005
    av = np.where(missing_aucvol, np.nan, auc_vol[di, ji])
    parts.append(pl.DataFrame({"ListingId": primary_lid[ji], "Date": dates_col, "ClassificationName": "LIT_CLOSING_AUCTION",
                               "ExecutionVenue": primary[ji], "TradeVolume": av, "TradeNotionalUSD": av * p_mid}))
    parts.append(pl.DataFrame({"ListingId": primary_lid[ji], "Date": dates_col, "ClassificationName": "LIT_OPENING_AUCTION",
                               "ExecutionVenue": primary[ji], "TradeVolume": open_vol[di, ji], "TradeNotionalUSD": open_vol[di, ji] * opens[di, ji]}))
    ct = pl.concat(parts).filter(pl.col("TradeVolume").is_not_nan() | (pl.col("ClassificationName") != "LIT_CLOSING_AUCTION"))
    ct = ct.with_columns(pl.col("TradeVolume").fill_nan(None)).drop_nulls("TradeVolume")

    tables = {
        ("crsp", "dsf_v2"): dsf,
        ("crsp", "stksecurityinfohist"): secinfo,
        ("crsp", "stkdistributions"): dist,
        ("crsp", "stkdelists"): delists,
        ("crsp", "dsp500list_v2"): mbr,
        ("crsp", "dsp500"): dsp500,
        ("vendor", "sp500_tr"): sp_tr,
        ("bmll", "reference_listing"): reference,
        ("bmll", "daily_analytics"): da,
        ("bmll", "classified_trades"): ct,
        ("bmll", "auction_imbalance"): imb,
    }
    return SyntheticWorld(tables=tables, sessions=sessions, permnos=permnos, seed=seed)
