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

Credentials (environment): BMLL_KEY_PATH (private key; <key>.pub next to it), BMLL_USERNAME
(your BMLL API username), optional BMLL_KEY_PASSPHRASE and BMLL_LAB_ORG_ID. Test with --check-auth.
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


def login() -> None:
    """Log in explicitly, before any query, and fail with one clear message.

    The SDK identifies you by an API *username*: BMLL_USERNAME, else the comment field of the
    public key (``<BMLL_KEY_PATH>.pub``). A key made with plain ``ssh-keygen`` has the comment
    ``user@host``, which BMLL rejects with 401, and the SDK's retry-on-401 then recurses until
    RecursionError. Organisation (Lab) accounts also need BMLL_LAB_ORG_ID.
    """
    import os
    import sys

    import bmll

    key = os.environ.get("BMLL_KEY_PATH")
    if not key:
        sys.exit("BMLL_KEY_PATH is not set (path to the private key registered with BMLL)")
    priv = Path(key).expanduser()
    pub = priv.with_suffix(".pub")
    if not priv.exists():
        sys.exit(f"private key not found: {priv}")
    if not pub.exists():
        sys.exit(f"public key not found: {pub} (the SDK needs both files in the same folder)")
    parts = pub.read_text().split()
    comment = parts[2] if len(parts) == 3 else None
    user = os.environ.get("BMLL_USERNAME") or comment
    org = os.environ.get("BMLL_LAB_ORG_ID")
    print(
        f"BMLL login: username={user!r} ({'BMLL_USERNAME' if os.environ.get('BMLL_USERNAME') else 'public-key comment'}), "
        f"key={priv}, lab_org_id={org!r}"
    )
    if not user:
        sys.exit(
            "no BMLL username: set BMLL_USERNAME to your API username (data.bmlltech.com, Data Delivery page)"
        )
    sys.setrecursionlimit(200)  # turn the SDK's 401 retry loop into a fast failure
    try:
        bmll.login(
            username=user,
            key_path=str(priv),
            passphrase=os.environ.get("BMLL_KEY_PASSPHRASE"),
            lab_org_id=org,
        )
    except RecursionError:
        sys.exit(
            "BMLL rejected the credentials (HTTP 401 on /auth/identity). Check:\n"
            f"  1. BMLL_USERNAME is your BMLL API username (currently {user!r}), not your Mac user@host\n"
            f"  2. the public key {pub} is the one registered on data.bmlltech.com\n"
            "  3. for an organisation / Data Lab account, BMLL_LAB_ORG_ID is set\n"
            "  4. BMLL_KEY_PASSPHRASE is set if the key has a passphrase"
        )
    finally:
        sys.setrecursionlimit(3000)
    print("BMLL login OK")


CHUNKS: Path | None = None  # set in main(): <out>/_chunks, one Parquet per finished batch (resume on restart)


def _batched(name: str, items: list, size: int, fetch) -> pd.DataFrame:  # type: ignore[no-untyped-def]
    """Run ``fetch`` per batch with progress output; each finished batch is saved and skipped on restart."""
    import time

    frames = []
    n = (len(items) + size - 1) // size
    t0 = time.time()
    for k in range(n):
        f = CHUNKS / f"{name}_{k:04d}.parquet" if CHUNKS else None
        if f is not None and f.exists():
            frames.append(pd.read_parquet(f))
            print(f"  {name}: batch {k + 1}/{n} already done", flush=True)
            continue
        t = time.time()
        df = fetch(items[k * size : (k + 1) * size])
        if f is not None:
            df.to_parquet(f)
        frames.append(df)
        done = time.time() - t0
        eta = done / (k + 1) * (n - k - 1)
        print(
            f"  {name}: batch {k + 1}/{n}, {len(df):,} rows in {time.time() - t:.0f}s, ETA ~{eta / 60:.0f} min",
            flush=True,
        )
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


REF_MICS = ["XNYS", "XNAS", "ARCX", "BATS", "XASE", "@SIP"]  # primary venues (OPOL) + consolidated tape


def reference_by_isin(isins: list[str], start: date, end: date) -> pd.DataFrame:
    """Listing reference data as validity intervals.

    ``reference.query`` over a date range returns one row per listing per day (millions of rows
    for four years), so instead query monthly snapshots on the primary venues + @SIP and collapse
    them: each (ListingId, ISIN, Ticker) gets ValidFrom/ValidTo from the snapshots it appears in.
    """
    import bmll

    days = pd.bdate_range(start, end, freq="BMS").date.tolist()
    if not days or days[0] != start:
        days = [start, *days]
    if days[-1] != end:
        days.append(end)

    def snapshot(day_batch: list[date]) -> pd.DataFrame:
        d = day_batch[0]
        frames = [
            bmll.reference.query(
                start_date=d, end_date=d, object_type="Listing", ISIN=isins[i : i + 200], MIC=REF_MICS
            )
            for i in range(0, len(isins), 200)
        ]
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        df["SnapDate"] = d
        return df

    raw = _rename(_batched("reference", days, 1, snapshot))
    keys = [c for c in ("ListingId", "ISIN", "Ticker") if c in raw.columns]
    agg = (
        raw.groupby(keys, dropna=False)
        .agg(ValidFrom=("SnapDate", "min"), ValidTo=("SnapDate", "max"))
        .reset_index()
    )
    # a listing seen in the first / last snapshot is valid from the window start / open-ended
    nxt = {d: days[k + 1] for k, d in enumerate(days[:-1])}
    agg["ValidFrom"] = agg["ValidFrom"].where(agg["ValidFrom"] != days[0], date(2015, 1, 1))
    agg["ValidTo"] = [
        date(9999, 12, 31) if v == days[-1] else nxt[v] - pd.Timedelta(days=1).to_pytimedelta()
        for v in agg["ValidTo"]
    ]
    last = raw.drop(columns=[c for c in ("Date", "SnapDate") if c in raw.columns]).drop_duplicates(
        keys, keep="last"
    )
    ref = agg.merge(last, on=keys, how="left")
    print(f"  reference: {len(ref)} listing intervals from {len(days)} snapshots", flush=True)
    return ref


def daily_analytics(listing_ids: list[int], start: date, end: date) -> pd.DataFrame:
    import bmll

    return _rename(
        _batched(
            "daily_analytics",
            listing_ids,
            25,
            lambda b: bmll.time_series.query(
                start_date=start,
                end_date=end,
                metric=DAILY_METRICS,
                frequency="D",
                object_ids=b,
                object_type="Listing",
            ),
        ),
        keep_volume=True,
    )


def classified_trades(listing_ids: list[int], start: date, end: date) -> pd.DataFrame:
    import bmll

    return _rename(
        _batched(
            "classified_trades",
            listing_ids,
            25,
            lambda b: bmll.time_series.classified_trades(
                start_date=start, end_date=end, object_ids=b, object_type="Listing", notional_currency="USD"
            ),
        )
    )


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
    ap.add_argument("--check-auth", action="store_true", help="only test the BMLL login, then exit")
    ap.add_argument("--isins", help="CSV from `simp data export-isins` (column isin)")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--out")
    a = ap.parse_args()
    login()
    if a.check_auth:
        return
    if not (a.isins and a.start and a.end and a.out):
        ap.error("--isins, --start, --end and --out are required")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    global CHUNKS
    CHUNKS = out / "_chunks"
    CHUNKS.mkdir(exist_ok=True)
    s, e = date.fromisoformat(a.start), date.fromisoformat(a.end)
    isins = pd.read_csv(a.isins)["isin"].dropna().astype(str).unique().tolist()

    print(f"1/3 reference lookup for {len(isins)} ISINs", flush=True)
    ref = reference_by_isin(isins, s, e)
    ref.to_parquet(out / "reference_listing.parquet")
    prim = (
        ref[(ref["IsPrimary"]) & (ref["MIC"] == ref["OPOL"])]
        if {"IsPrimary", "OPOL"} <= set(ref.columns)
        else ref
    )
    lids = sorted(prim["ListingId"].astype(int).unique().tolist())
    print(f"reference: {len(ref)} listing rows, {len(lids)} primary listings", flush=True)

    print("2/3 daily analytics", flush=True)
    da = daily_analytics(lids, s, e)
    da.to_parquet(out / "daily_analytics.parquet")
    print(f"daily analytics: {len(da)} rows; columns {sorted(da.columns)}")
    print("3/3 classified trades", flush=True)
    ct = classified_trades(lids, s, e)
    ct.to_parquet(out / "classified_trades.parquet")
    print(f"classified trades: {len(ct)} rows; columns {sorted(ct.columns)}")
    (out / "pull_spec.json").write_text(
        json.dumps({"start": a.start, "end": a.end, "isins": len(isins), "listings": len(lids)})
    )


if __name__ == "__main__":
    main()
