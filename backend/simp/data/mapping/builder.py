"""PERMNO -> ISIN/ticker -> BMLL ListingId / InstrumentId, point in time.

1. Build ISINs from CRSP CUSIP9 per ``stksecurityinfohist`` interval.
2. Look up by ISIN; keep the primary listing on the OPOL plus the @SIP listing.
3. Fall back on Ticker + OPOL; accept only if CRSP and BMLL closes agree within 1 bp on 5 sample days.
4. Validate: exactly one mapping per (permno, date) for each holding/order date. Unmapped names
   go to a review queue, never dropped silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

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
        cands = [(r, "ISIN", 1.0) for r in by_isin.get(isin, [])] if isin else []
        if not cands:
            for r in by_ticker.get(s["ticker"], []):
                if r["OPOL"] != exch_to_mic(s["primaryexch"]):
                    continue
                # price cross-check on up to 5 sample days inside the overlap
                ov = _overlap(s0, s1, r["ValidFrom"], r["ValidTo"])  # type: ignore[arg-type]
                if ov is None:
                    continue
                days = [d for d in crsp_dates.get(permno, []) if ov[0] <= d <= ov[1]]
                sample = days[:: max(1, len(days) // 5)][:5]
                ok = [
                    abs(bc[(int(r["ListingId"]), d)] / cc[(permno, d)] - 1) <= 1e-4  # type: ignore[call-overload]
                    for d in sample
                    if (int(r["ListingId"]), d) in bc and (permno, d) in cc  # type: ignore[call-overload]
                ]
                if len(ok) >= min(5, len(sample)) and ok and all(ok):
                    cands.append((r, "TICKER_PRICE_CHECK", 0.9))
        placed = False
        for r, method, conf in cands:
            lf, lt = life.get(int(r["ListingId"]), (r["ValidFrom"], r["ValidTo"]))  # type: ignore[call-overload,arg-type]
            ov = _overlap(s0, s1, lf, lt)
            if ov is None:
                continue
            sips = [
                x
                for x in sip_by_inst.get(int(r["InstrumentId"]), [])  # type: ignore[call-overload]
                if _overlap(ov[0], ov[1], x["SipFrom"], x["SipTo"])  # type: ignore[arg-type]
            ]
            rows.append(
                dict(
                    permno=permno,
                    listing_id=int(r["ListingId"]),  # type: ignore[call-overload]
                    instrument_id=int(r["InstrumentId"]),  # type: ignore[call-overload]
                    sip_listing_id=int(sips[0]["SipListingId"]) if sips else None,  # type: ignore[call-overload]
                    isin=str(r["ISIN"] or isin or ""),
                    figi=r["FIGI"],
                    ticker=s["ticker"],
                    opol=r["OPOL"],
                    valid_from=ov[0],
                    valid_to=ov[1],
                    method=method,
                    confidence=conf,
                )
            )
            placed = True
        if not placed:
            queue.append(
                dict(
                    permno=permno,
                    ticker=s["ticker"],
                    isin=isin,
                    valid_from=s0,
                    valid_to=s1,
                    reason="no ISIN match; ticker fallback failed or price check disagreed",
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
