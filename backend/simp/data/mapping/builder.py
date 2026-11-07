"""PERMNO -> ISIN/ticker -> BMLL ListingId / InstrumentId, point in time.

1. Build ISINs from CRSP CUSIP9 per ``stksecurityinfohist`` interval.
2. Look up by ISIN; keep the primary listing on the OPOL plus the @SIP listing.
3. Fall back on Ticker + OPOL; accept only if CRSP and BMLL closes agree within 1 bp on 5 sample days.
4. Validate: exactly one mapping per (permno, date) for each holding/order date. Unmapped names
   go to a review queue, never dropped silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import polars as pl

from simp.data.mapping.isin import cusip9_of, exch_to_mic, isin_from_cusip9
from simp.errors import MappingError

OPEN_END = date(9999, 12, 31)


@dataclass
class MappingResult:
    security_map: pl.DataFrame
    review_queue: pl.DataFrame  # permno, ticker, interval, reason


def _overlap(a0: date, a1: date, b0: date, b1: date) -> tuple[date, date] | None:
    lo, hi = max(a0, b0), min(a1, b1)
    return (lo, hi) if lo <= hi else None


def _minus(ov: tuple[date, date] | None, cut: tuple[date, date]) -> list[tuple[date, date]]:
    """The parts of ``ov`` outside ``cut``."""
    if ov is None:
        return []
    out = []
    if ov[0] < cut[0]:
        out.append((ov[0], min(ov[1], cut[0] - timedelta(days=1))))
    if ov[1] > cut[1]:
        out.append((max(ov[0], cut[1] + timedelta(days=1)), ov[1]))
    return out


def _one_listing_per_day(placed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve overlapping listings matched to one CRSP interval: a later listing wins from its
    start, which comes from BMLL's traded dates, while an earlier listing's end is only as precise
    as the monthly reference snapshots (e.g. Bunge's Bermuda listing and its Swiss successor, both
    trading 2023-10-31). On equal starts the first candidate (ISIN before ticker) is kept."""
    out: list[dict[str, Any]] = []
    for x in sorted((dict(r) for r in placed), key=lambda r: r["valid_from"]):
        if out and x["valid_from"] == out[-1]["valid_from"]:
            continue
        if out and x["valid_from"] <= out[-1]["valid_to"]:
            prev = out[-1]
            if x["valid_to"] < prev["valid_to"]:  # x sits inside prev: prev resumes after it
                out.append({**prev, "valid_from": x["valid_to"] + timedelta(days=1)})
                out.sort(key=lambda r: r["valid_from"])
            prev["valid_to"] = x["valid_from"] - timedelta(days=1)
        out.append(x)
        out.sort(key=lambda r: r["valid_from"])
    return out


def build_security_map(
    secinfo: pl.DataFrame,
    reference: pl.DataFrame,
    crsp_close: pl.DataFrame,  # permno, dlycaldt, dlyprc
    bmll_close: pl.DataFrame,  # ListingId, Date, close
    universe: set[int] | None = None,
) -> MappingResult:
    ref = reference.with_columns(pl.col("ISIN").replace("", None))
    prim = ref.filter(pl.col("IsPrimary") & (pl.col("MIC") == pl.col("OPOL")))
    # An ISIN (or ticker) identifies the listing; the listing itself is stable across identifier
    # changes, so a matched listing is valid for its whole life, not only the identifier's interval.
    # Reference rows come from periodic (monthly) snapshots, so a listing that starts mid-month
    # (e.g. a new ISIN after a split) would only appear from the next snapshot; widen its life to
    # every date BMLL actually has daily data for it.
    traded = {
        int(lid): (a, b)
        for lid, a, b in bmll_close.group_by("ListingId")
        .agg(pl.col("Date").min().alias("first"), pl.col("Date").max().alias("last"))
        .iter_rows()
    }
    life: dict[int, tuple[date, date]] = {}
    for lid, a, b in (
        ref.group_by("ListingId").agg(pl.col("ValidFrom").min(), pl.col("ValidTo").max()).iter_rows()
    ):
        t = traded.get(int(lid))
        life[int(lid)] = (min(a, t[0]), max(b, t[1])) if t else (a, b)
    sip = (
        ref.filter(pl.col("MIC") == "@SIP")
        .select("InstrumentId", "ListingId", "ValidFrom", "ValidTo")
        .rename({"ListingId": "SipListingId", "ValidFrom": "SipFrom", "ValidTo": "SipTo"})
    )
    by_isin: dict[str, list[dict[str, object]]] = {}
    for r in prim.filter(pl.col("ISIN").is_not_null()).iter_rows(named=True):
        by_isin.setdefault(r["ISIN"], []).append(r)
    by_ticker: dict[str, list[dict[str, object]]] = {}
    for r in prim.iter_rows(named=True):
        by_ticker.setdefault(str(r["Ticker"]).replace(" ", "."), []).append(r)
    by_inst_mic: dict[tuple[int, str], dict[str, Any]] = {}
    for r in ref.sort("ValidTo").iter_rows(named=True):  # latest row per (instrument, venue) wins
        by_inst_mic[(int(r["InstrumentId"]), str(r["MIC"]))] = r
    sip_by_inst: dict[int, list[dict[str, object]]] = {}
    for r in sip.iter_rows(named=True):
        sip_by_inst.setdefault(int(r["InstrumentId"]), []).append(r)

    cc = {(int(p), d): float(x) for p, d, x in crsp_close.select("permno", "dlycaldt", "dlyprc").iter_rows()}
    bc = {(int(lid), d): float(x) for lid, d, x in bmll_close.iter_rows() if x is not None}
    crsp_dates: dict[int, list[date]] = {}
    for p, d in crsp_close.select("permno", "dlycaldt").iter_rows():
        crsp_dates.setdefault(int(p), []).append(d)

    rows: list[dict[str, object]] = []
    queue: list[dict[str, object]] = []
    for s in secinfo.sort("permno", "secinfostartdt").iter_rows(named=True):
        permno = int(s["permno"])
        if universe is not None and permno not in universe:
            continue
        c9 = cusip9_of(s.get("cusip9"), s.get("cusip"))
        isin = isin_from_cusip9(c9) if c9 else None  # no CUSIP: ticker fallback / review queue only
        s0, s1 = s["secinfostartdt"], s["secinfoenddt"] or OPEN_END
        mic = exch_to_mic(s["primaryexch"])

        def span(r: dict[str, Any], s0: date = s0, s1: date = s1) -> tuple[date, date] | None:
            # the listing's life (traded dates widen the snapshot interval) inside this CRSP interval
            lf, lt = life.get(int(r["ListingId"]), (r["ValidFrom"], r["ValidTo"]))
            return _overlap(s0, s1, lf, lt)

        def price_ok(r: dict[str, Any], ov: tuple[date, date], permno: int = permno) -> bool:
            # BMLL close = CRSP close within 1 bp on up to 5 sample days inside the overlap
            lid = int(r["ListingId"])
            days = [d for d in crsp_dates.get(permno, []) if ov[0] <= d <= ov[1]]
            sample = days[:: max(1, len(days) // 5)][:5]
            ok = [
                abs(bc[(lid, d)] / cc[(permno, d)] - 1) <= 1e-4
                for d in sample
                if (lid, d) in bc and (permno, d) in cc
            ]
            return len(ok) >= min(5, len(sample)) and bool(ok) and all(ok)

        def venue(r: dict[str, Any], mic: str = mic) -> dict[str, Any] | None:
            # the instrument's listing on the venue CRSP names as primary for this interval: BMLL's
            # reference keeps only a listing's latest primary flag, so after a venue move (Linde
            # NYSE -> Nasdaq, 2023-11-07) its "primary" is the new venue for the whole history
            if r["MIC"] == mic:
                return r
            return by_inst_mic.get((int(r["InstrumentId"]), mic))

        cands: list[tuple[dict[str, Any], str, float, tuple[date, date]]] = []
        isin_rows = by_isin.get(isin, []) if isin else []
        for r in isin_rows:
            ov = span(r)
            v = venue(r)
            vo = span(v) if v is not None and v is not r else None
            if v is not None and vo is not None and price_ok(v, vo):
                cands.append((v, "ISIN_CRSP_VENUE", 1.0, vo))
                cands += [(r, "ISIN", 1.0, seg) for seg in _minus(ov, vo)]
            elif ov is not None:
                cands.append((r, "ISIN", 1.0, ov))
        if not isin_rows:
            for r in by_ticker.get(s["ticker"], []):
                v = venue(r)
                vo = span(v) if v is not None else None
                if v is not None and vo is not None and price_ok(v, vo):
                    cands.append((v, "TICKER_PRICE_CHECK", 0.9, vo))
        mine: list[dict[str, object]] = []
        for r, method, conf, ov in cands:
            sips = [
                x
                for x in sip_by_inst.get(int(r["InstrumentId"]), [])
                if _overlap(ov[0], ov[1], x["SipFrom"], x["SipTo"])  # type: ignore[arg-type]
            ]
            mine.append(
                dict(
                    permno=permno,
                    listing_id=int(r["ListingId"]),
                    instrument_id=int(r["InstrumentId"]),
                    sip_listing_id=int(sips[0]["SipListingId"]) if sips else None,  # type: ignore[call-overload]
                    isin=str(r["ISIN"] or isin or ""),
                    figi=r["FIGI"],
                    ticker=s["ticker"],
                    opol=mic if r["MIC"] == mic else r["OPOL"],
                    valid_from=ov[0],
                    valid_to=ov[1],
                    method=method,
                    confidence=conf,
                )
            )
        rows.extend(_one_listing_per_day(mine))
        if not mine:
            listed = bool(by_ticker.get(s["ticker"]))
            reason = (
                "no ISIN match; ticker fallback failed or price check disagreed"
                if listed
                else "no ISIN match and the ticker is not in the BMLL reference pull"
            )
            if c9 and c9[0].isalpha():
                # CINS CUSIP: a foreign-domiciled issuer whose ISIN is not "US" + CUSIP
                reason += " (foreign issuer: CINS CUSIP, re-run the BMLL puller for its ticker fallback)"
            queue.append(
                dict(
                    permno=permno,
                    ticker=s["ticker"],
                    isin=isin,
                    valid_from=s0,
                    valid_to=s1,
                    reason=reason,
                )
            )
    smap = (
        pl.DataFrame(
            rows,
            schema={
                "permno": pl.Int64,
                "listing_id": pl.Int64,
                "instrument_id": pl.Int64,
                "sip_listing_id": pl.Int64,
                "isin": pl.Utf8,
                "figi": pl.Utf8,
                "ticker": pl.Utf8,
                "opol": pl.Utf8,
                "valid_from": pl.Date,
                "valid_to": pl.Date,
                "method": pl.Utf8,
                "confidence": pl.Float64,
            },
        )
        .unique(["permno", "valid_from"], keep="first")
        .sort("permno", "valid_from")
    )
    q = pl.DataFrame(
        queue,
        schema={
            "permno": pl.Int64,
            "ticker": pl.Utf8,
            "isin": pl.Utf8,
            "valid_from": pl.Date,
            "valid_to": pl.Date,
            "reason": pl.Utf8,
        },
    )
    return MappingResult(smap, q)


def validate_mapping(smap: pl.DataFrame, needs: pl.DataFrame) -> pl.DataFrame:
    """``needs`` has (permno, date). Returns rows that do not have exactly one mapping.

    Mirrors the handoff notebook's ``validate_bmll_mapping`` rule."""
    joined = needs.join(
        smap.select("permno", "listing_id", "valid_from", "valid_to"), on="permno", how="left"
    )
    hit = joined.with_columns(
        ((pl.col("date") >= pl.col("valid_from")) & (pl.col("date") <= pl.col("valid_to")))
        .fill_null(False)
        .alias("hit")
    )
    counts = hit.group_by("permno", "date").agg(pl.col("hit").sum().alias("n"))
    return counts.filter(pl.col("n") != 1).sort("permno", "date")


def require_unique(smap: pl.DataFrame, needs: pl.DataFrame) -> None:
    bad = validate_mapping(smap, needs)
    if bad.height:
        raise MappingError(
            f"{bad.height} (permno, date) pairs without exactly one BMLL mapping, e.g. {bad.head(3).to_dicts()}"
        )
