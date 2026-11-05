"""BMLL puller — runs in its own environment (``bmll`` SDK >= 1.25 pins NumPy < 2, the core
needs NumPy 2), outside the Data Lab with SSH-key auth (BMLL_KEY_PATH).

It writes raw Parquet in the exact layout and column names the core lake expects, plus a
pull spec JSON; the core then registers the files with ``simp data register-bmll`` which
checks contracts and writes manifests. Heavy pulls (Trades Plus @SIP, L3, auction imbalance
detail, T3 simulator runs) are submitted as Data Lab jobs (``bmll.compute``) writing to
external storage; ``lab_job_spec`` below produces those specs.

Usage::

    uv run --project connectors/bmll_puller python puller.py \
        --listings listings.csv --start 2022-03-01 --end 2026-07-31 --out ../../lake_staging/bmll
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import pandas as pd

DAILY_METRICS = [
    "VWAP", "TWAP", "ClosingAuctionPrice|Executed", "OpeningAuctionPrice|Executed",
    "Spread|RelTWA", "Spread|RelVWA", "Spread|AbsTWA", "Volatility|Intraday",
    "SweepToFill|25kUSDTWASpread", "SweepToFill|100kUSDTWASpread", "SweepToFill|250kUSDTWASpread",
    "AuctionOrderImbalance|Abs", "AuctionOrderImbalance|Notional", "AuctionDislocation|ClosingLast", "Volume",
]


def reference_by_isin(isins: list[str], start: date, end: date) -> pd.DataFrame:
    import bmll

    frames = []
    for i in range(0, len(isins), 200):
        frames.append(bmll.reference.query(start_date=start, end_date=end, object_type="Listing", ISIN=isins[i : i + 200]))
    return pd.concat(frames, ignore_index=True)


def daily_analytics(listing_ids: list[int], start: date, end: date) -> pd.DataFrame:
    import bmll

    return bmll.time_series.query(
        start_date=start, end_date=end, metric=DAILY_METRICS, frequency="D", object_ids=listing_ids, object_type="Listing"
    )


def classified_trades(listing_ids: list[int], start: date, end: date) -> pd.DataFrame:
    import bmll

    return bmll.time_series.classified_trades(
        start_date=start, end_date=end, object_ids=listing_ids, object_type="Listing", notional_currency="USD"
    )


def estimate_trades_plus(sip_listing_ids: list[int], day: date) -> object:
    import bmll

    return bmll.data.query("trades-plus", start_date=day, end_date=day, ListingId=[str(x) for x in sip_listing_ids], estimate=True)


def lab_job_spec(kind: str, dates: list[date], listing_ids: list[int], storage_uri: str) -> dict[str, object]:
    """Spec for a scheduled Data Lab job (submitted with bmll.compute.create_job)."""
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
    ap.add_argument("--listings", required=True, help="CSV with ListingId column (primary listings)")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    lids = pd.read_csv(a.listings)["ListingId"].astype(int).tolist()
    s, e = date.fromisoformat(a.start), date.fromisoformat(a.end)
    daily_analytics(lids, s, e).to_parquet(out / "daily_analytics.parquet")
    classified_trades(lids, s, e).to_parquet(out / "classified_trades.parquet")
    (out / "pull_spec.json").write_text(json.dumps({"start": a.start, "end": a.end, "n_listings": len(lids)}))


if __name__ == "__main__":
    main()
