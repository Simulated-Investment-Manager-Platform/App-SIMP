"""BMLL puller — runs in its own environment (``bmll`` SDK >= 1.25 pins NumPy < 2; the SIMP core
needs NumPy 2), outside the Data Lab, authenticated with the SSH key at ``BMLL_KEY_PATH``.

Steps:
  1. reference: ISIN list (from ``simp data export-isins``) -> Listing rows (primary + @SIP + siblings)
  2. daily analytics for the primary listings (VWAP, TWAP, auction prices, spreads, vol, sweeps, ...)
  3. classified trades for the primary listings, notional in USD (auction volume, venue mix, ADV)

It writes ``reference_listing.parquet``, ``daily_analytics.parquet`` and ``classified_trades.parquet``
renamed to the column names of SIMP's lake contracts. ``simp data register-bmll DIR`` then
conforms types, checks the contracts and writes manifests. If the SDK returns a column under a
name this script does not know, registration fails naming the missing column; add it to RENAME.

The Auction Imbalance detail dataset (``auction_imbalance.parquet``: ImbalanceSide,
ImbalanceQuantity, PairedQuantity, ReferencePrice, IndicativeUncrossingPrice) and Trades Plus
@SIP come from Data Lab jobs (``bmll2``); ``lab_job_spec`` builds those specs. Without the
imbalance file SIMP still runs and reports imbalance as zero.

Usage::

    uv run --project connectors/bmll_puller python connectors/bmll_puller/puller.py \\
        --isins isins.csv --start 2022-03-01 --end 2026-07-31 --out lake_staging/bmll
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import pandas as pd

DAILY_METRICS = [
    "VWAP",
    "TWAP",
    "ClosingAuctionPrice|Executed",
    "OpeningAuctionPrice|Executed",
    "Spread|RelTWA",
    "Spread|RelVWA",
    "Spread|AbsTWA",
    "Volatility|Intraday",
    "SweepToFill|25kUSDTWASpread",
    "SweepToFill|100kUSDTWASpread",
    "SweepToFill|250kUSDTWASpread",
    "AuctionOrderImbalance|Abs",
    "AuctionOrderImbalance|Notional",
    "AuctionDislocation|ClosingLast",
    "Volume",
]

# SDK output name -> SIMP lake contract name (first match wins).
RENAME = {
    "ObjectId": "ListingId",
    "Object Id": "ListingId",
    "listing_id": "ListingId",
    "TradeDate": "Date",
    "date": "Date",
    "Classification": "ClassificationName",
    "Venue": "ExecutionVenue",
    "Volume": "TradeVolume",
    "NotionalUSD": "TradeNotionalUSD",
    "Notional": "TradeNotionalUSD",
    "Opol": "OPOL",
    "StartDate": "ValidFrom",
    "EndDate": "ValidTo",
}


def _rename(df: pd.DataFrame, keep_volume: bool = False) -> pd.DataFrame:
    m = {k: v for k, v in RENAME.items() if k in df.columns and v not in df.columns}
    if keep_volume:
        m.pop("Volume", None)
    return df.rename(columns=m)


def reference_by_isin(isins: list[str], start: date, end: date) -> pd.DataFrame:
    import bmll

    frames = []
    for i in range(0, len(isins), 200):
        frames.append(
            bmll.reference.query(
                start_date=start, end_date=end, object_type="Listing", ISIN=isins[i : i + 200]
            )
        )
    ref = _rename(pd.concat(frames, ignore_index=True))
    if "ValidFrom" not in ref.columns:
        ref["ValidFrom"] = pd.Timestamp(start).date()
    if "ValidTo" not in ref.columns:
        ref["ValidTo"] = date(9999, 12, 31)
    return ref


def daily_analytics(listing_ids: list[int], start: date, end: date) -> pd.DataFrame:
    import bmll

    frames = []
    for i in range(0, len(listing_ids), 100):
        frames.append(
            bmll.time_series.query(
                start_date=start,
                end_date=end,
                metric=DAILY_METRICS,
                frequency="D",
                object_ids=listing_ids[i : i + 100],
                object_type="Listing",
            )
        )
    return _rename(pd.concat(frames, ignore_index=True), keep_volume=True)


def classified_trades(listing_ids: list[int], start: date, end: date) -> pd.DataFrame:
    import bmll

    frames = []
    for i in range(0, len(listing_ids), 100):
        frames.append(
            bmll.time_series.classified_trades(
                start_date=start,
                end_date=end,
                object_ids=listing_ids[i : i + 100],
                object_type="Listing",
                notional_currency="USD",
            )
        )
    return _rename(pd.concat(frames, ignore_index=True))


def estimate_trades_plus(sip_listing_ids: list[int], day: date) -> object:
    """Size a Trades Plus @SIP pull before running it (bmll.data.query estimate=True)."""
    import bmll

    return bmll.data.query(
        "trades-plus",
        start_date=day,
        end_date=day,
        ListingId=[str(x) for x in sip_listing_ids],
        estimate=True,
    )


def lab_job_spec(kind: str, dates: list[date], listing_ids: list[int], storage_uri: str) -> dict[str, object]:
    """Spec for a scheduled Data Lab job (submit inside the Lab with bmll.compute.create_job)."""
    return {
        "name": f"simp-{kind}-{dates[0]}-{dates[-1]}",
        "kind": kind,  # trades_plus_sip | auction_imbalance | l3 | t3_simulation
        "dates": [d.isoformat() for d in dates],
        "listing_ids": listing_ids,
        "output": f"{storage_uri}/bmll/{kind}/date={{date}}/part-0.parquet",
        "manifest": True,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--isins", required=True, help="CSV from `simp data export-isins` (column isin)")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    s, e = date.fromisoformat(a.start), date.fromisoformat(a.end)
    isins = pd.read_csv(a.isins)["isin"].dropna().astype(str).unique().tolist()

    ref = reference_by_isin(isins, s, e)
    ref.to_parquet(out / "reference_listing.parquet")
    prim = (
        ref[(ref["IsPrimary"]) & (ref["MIC"] == ref["OPOL"])]
        if {"IsPrimary", "OPOL"} <= set(ref.columns)
        else ref
    )
    lids = sorted(prim["ListingId"].astype(int).unique().tolist())
    print(f"reference: {len(ref)} listing rows, {len(lids)} primary listings")

    da = daily_analytics(lids, s, e)
    da.to_parquet(out / "daily_analytics.parquet")
    print(f"daily analytics: {len(da)} rows; columns {sorted(da.columns)}")
    ct = classified_trades(lids, s, e)
    ct.to_parquet(out / "classified_trades.parquet")
    print(f"classified trades: {len(ct)} rows; columns {sorted(ct.columns)}")
    (out / "pull_spec.json").write_text(
        json.dumps({"start": a.start, "end": a.end, "isins": len(isins), "listings": len(lids)})
    )


if __name__ == "__main__":
    main()
