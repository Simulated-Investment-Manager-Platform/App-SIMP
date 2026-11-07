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
ImbalanceQuantity, PairedQuantity, ReferencePrice, IndicativeUncrossingPrice) comes from a Data
Lab job (``bmll2``); without it SIMP still runs and reports imbalance as zero.

T2 stage (``--t2-needs t2_needs.csv`` from ``simp data export-t2-needs <run>``): Trades Plus @SIP
ticks for only the (date, ticker) pairs a run traded intraday, queried by Ticker (a ListingId
filter on @SIP is refused by BMLL entitlements) and collapsed to 1-minute regular-session bars
(NBBO mid, continuous VWAP, addressable volume) in ``trades_plus_minute.parquet``.

Usage::

    uv run --project connectors/bmll_puller python connectors/bmll_puller/puller.py \\
        --isins isins.csv --start 2022-03-01 --end 2026-07-31 --out lake_staging/bmll
    uv run --project connectors/bmll_puller python connectors/bmll_puller/puller.py \\
        --t2-needs t2_needs.csv --out lake_staging/bmll

Credentials (environment): BMLL_KEY_PATH (private key; <key>.pub next to it), BMLL_USERNAME
(your BMLL API username), optional BMLL_KEY_PASSPHRASE and BMLL_LAB_ORG_ID. Test with --check-auth.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
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
    "Shares": "TradeVolume",
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


def _chunk_files(name: str, items: list, size: int) -> list[Path]:  # type: ignore[type-arg]
    import hashlib

    n = (len(items) + size - 1) // size
    out = []
    for k in range(n):
        key = hashlib.md5(repr(items[k * size : (k + 1) * size]).encode()).hexdigest()[:10]
        out.append((CHUNKS or Path(".")) / f"{name}_{k:04d}_{key}.parquet")
    return out


def _batched(name: str, items: list, size: int, fetch, keep: bool = True) -> pd.DataFrame:  # type: ignore[no-untyped-def]
    """Run ``fetch`` per batch with progress output; each finished batch is saved and skipped on restart."""
    import hashlib
    import time

    frames = []
    n = (len(items) + size - 1) // size
    t0 = time.time()
    for k in range(n):
        batch = items[k * size : (k + 1) * size]
        # keyed by the batch's exact inputs: a run with other dates/ISINs/listings never reuses it
        key = hashlib.md5(repr(batch).encode()).hexdigest()[:10]
        f = CHUNKS / f"{name}_{k:04d}_{key}.parquet" if CHUNKS else None
        if f is not None and f.exists():
            if keep:
                frames.append(pd.read_parquet(f))
            print(f"  {name}: batch {k + 1}/{n} already done", flush=True)
            continue
        t = time.time()
        df = fetch(batch)
        if f is not None:
            df.to_parquet(f)
        if keep:
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
    """Listing reference data for these ISINs, as validity intervals."""
    return _reference("ISIN", isins, start, end)


def reference_by_ticker(tickers: list[str], start: date, end: date) -> pd.DataFrame:
    """Listing reference data for these tickers (CRSP style, ``BRK.B``), as validity intervals.

    For issuers whose ISIN is not ``US`` + CUSIP: foreign-domiciled S&P 500 members (CINS CUSIPs
    such as Linde ``G54950103``, real ISIN ``IE000S9YS762``) and US-format CUSIPs with a foreign
    ISIN (Schlumberger ``AN8068571086``). A ticker can be reused by another company, so SIMP
    accepts a ticker match only when the BMLL closing price agrees with the CRSP close."""
    return _reference("Ticker", [t.replace(".", " ") for t in tickers], start, end)


def _reference(field: str, values: list[str], start: date, end: date) -> pd.DataFrame:
    """``reference.query`` over a date range returns one row per listing per day (millions of rows
    for four years), so instead query monthly snapshots on the primary venues + @SIP and collapse
    them: each (ListingId, ISIN, Ticker) gets ValidFrom/ValidTo from the snapshots it appears in.
    """
    import hashlib

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
                start_date=d, end_date=d, object_type="Listing", MIC=REF_MICS, **{field: values[i : i + 200]}
            )
            for i in range(0, len(values), 200)
        ]
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        df["SnapDate"] = d
        return df

    # batches are one snapshot day each, so the query values go in the name: a run with another
    # ISIN or ticker list must not reuse these chunks
    tag = hashlib.md5(repr((field, values)).encode()).hexdigest()[:6]
    raw = _rename(_batched(f"reference_{field.lower()}_{tag}", days, 1, snapshot))
    if raw.empty:
        return raw
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
        date(9999, 12, 31) if v >= days[-1] else nxt.get(v, min(d for d in days if d > v)) - timedelta(days=1)
        for v in agg["ValidTo"]
    ]
    last = raw.drop(columns=[c for c in ("Date", "SnapDate") if c in raw.columns]).drop_duplicates(
        keys, keep="last"
    )
    ref = agg.merge(last, on=keys, how="left")
    print(f"  reference: {len(ref)} listing intervals from {len(days)} snapshots", flush=True)
    return ref


# SIMP field -> keyword sets; the first BMLL daily metric whose name contains every keyword
# of a set wins (exact name tried first). Names differ by BMLL release / entitlement, so they
# are resolved against ``time_series.available`` instead of being hard-coded.
METRIC_WANTS: dict[str, list[list[str]]] = {
    "VWAP": [["vwap"]],
    "TWAP": [["twap"]],
    "ClosingAuctionPrice|Executed": [["closing", "auction", "price"], ["close", "auction", "price"]],
    "OpeningAuctionPrice|Executed": [["opening", "auction", "price"], ["open", "auction", "price"]],
    "Spread|RelTWA": [["spread", "rel", "twa"], ["spread", "relative", "time"], ["spread", "bps"]],
    "Spread|RelVWA": [["spread", "rel", "vwa"], ["spread", "relative", "volume"]],
    "Spread|AbsTWA": [["spread", "abs", "twa"], ["spread", "absolute"]],
    "Volatility|Intraday": [["volatility", "intraday"], ["volatility"]],
    "SweepToFill|25kUSDTWASpread": [["sweep", "25k"]],
    "SweepToFill|100kUSDTWASpread": [["sweep", "100k"]],
    "SweepToFill|250kUSDTWASpread": [["sweep", "250k"]],
    "AuctionOrderImbalance|Abs": [["imbalance", "abs"], ["imbalance", "quantity"]],
    "AuctionOrderImbalance|Notional": [["imbalance", "notional"]],
    "AuctionDislocation|ClosingLast": [["dislocation", "clos"]],
    # Daily volume is taken from classified trades (addressable volume), not from daily analytics.
}
METRIC_MAP: dict[str, str] = {}  # resolved: SIMP field -> BMLL metric name


def resolve_metrics(listing_id: int, out: Path) -> dict[str, str]:
    """Match SIMP's fields to the daily metrics BMLL offers for this account; print the table."""
    import json

    import bmll

    av = bmll.time_series.available(object_id=listing_id)
    av.to_csv(out / "available_metrics.csv", index=False)
    if "frequency" in av.columns:
        daily = av[av["frequency"].astype(str).str.upper().isin(["D", "DAILY", "1D"])]
        av = daily if len(daily) else av
    # Metrics are base names with variants in a ``suffix`` column; the service is queried with the
    # base name and returns one column per variant, named "<metric>|<suffix>".
    if {"metric", "suffix"} <= set(av.columns):
        full = [
            f"{m}|{x}" if isinstance(x, str) and x and x.lower() != "nan" else str(m)
            for m, x in zip(av["metric"], av["suffix"])
        ]
    else:
        full = [str(x) for x in av[av.columns[0]]]
    names = sorted(set(full), key=len)
    override = out / "metric_map.json"
    manual = json.loads(override.read_text()) if override.exists() else {}
    resolved: dict[str, str] = {}
    for field, sets in METRIC_WANTS.items():
        if field in manual:
            resolved[field] = manual[field]
            continue
        hit = next((n for n in names if n.lower() == field.lower()), None)
        for kw in sets:
            if hit:
                break
            hit = next(
                (
                    n
                    for n in names
                    if all(k in n.lower() for k in kw) and ("sweep" not in n.lower() or "sweep" in kw)
                ),
                None,
            )
        if hit:
            resolved[field] = hit
    print(f"  {len(names)} daily metrics available (full list: {out / 'available_metrics.csv'})", flush=True)
    for field in METRIC_WANTS:
        print(
            f"    {field:32s} <- {resolved.get(field, 'NOT AVAILABLE (SIMP uses its fallback)')}", flush=True
        )
    print(
        f'  to correct a match, write {override} with {{"SIMP field": "BMLL metric"}} and re-run', flush=True
    )
    return resolved


def daily_analytics(listing_ids: list[int], start: date, end: date) -> pd.DataFrame:
    import bmll

    metrics = sorted({name.split("|")[0] for name in METRIC_MAP.values()})  # query base names
    if not metrics:
        raise SystemExit("none of SIMP's daily metrics are available for this account")
    import hashlib

    tag = hashlib.md5(",".join(metrics).encode()).hexdigest()[:6]  # new metric set -> new batches
    df = _batched(
        f"daily_analytics_{tag}",
        listing_ids,
        25,
        lambda b: bmll.time_series.query(
            start_date=start, end_date=end, metric=metrics, frequency="D", object_ids=b, object_type="Listing"
        ),
    )
    df = _rename(df, keep_volume=True)
    back = {bmll_name: field for field, bmll_name in METRIC_MAP.items()}
    return df.rename(columns={c: back[c] for c in df.columns if c in back and back[c] not in df.columns})


CT_COLS = {"ListingId": "int64", "Date": "datetime64[ns]", "ClassificationName": "string",
           "ExecutionVenue": "string", "TradeVolume": "float64", "TradeNotionalUSD": "float64"}


def _slim_ct(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only what SIMP uses, with compact numeric types (raw SDK frames are text columns)."""
    df = _rename(df)
    out = pd.DataFrame({c: df[c] for c in CT_COLS if c in df.columns})
    for c in ("ListingId", "TradeVolume", "TradeNotionalUSD"):
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    out["Date"] = pd.to_datetime(out["Date"])
    return out.astype({c: t for c, t in CT_COLS.items() if c in out.columns and c != "Date"})


def classified_trades(listing_ids: list[int], start: date, end: date, dest: Path) -> tuple[int, list[str]]:
    """Pull per batch (saved, resumable), then stream batches into one Parquet file: the full pull
    is ~35M rows, too large to hold as one pandas frame on a laptop."""
    import bmll
    import pyarrow as pa
    import pyarrow.parquet as pq

    _batched(
        "classified_trades",
        listing_ids,
        10,
        lambda b: _slim_ct(
            bmll.time_series.classified_trades(
                start_date=start, end_date=end, object_ids=b, object_type="Listing", notional_currency="USD"
            )
        ),
        keep=False,
    )
    files = sorted(CHUNKS.glob("classified_trades_*.parquet")) if CHUNKS else []
    wanted = {f.name for f in _chunk_files("classified_trades", listing_ids, 10)}
    rows, labels, writer = 0, set(), None
    for f in files:
        if f.name not in wanted:
            continue
        part = _slim_ct(pd.read_parquet(f))
        labels |= set(part["ClassificationName"].dropna().astype(str).unique())
        table = pa.Table.from_pandas(part, preserve_index=False)
        if writer is None:
            writer = pq.ParquetWriter(dest, table.schema)
        writer.write_table(table.cast(writer.schema))
        rows += len(part)
    if writer is not None:
        writer.close()
    return rows, sorted(labels)


# ------------------------------------------------------------------ T2: Trades Plus minute bars
TP_COLS = ["ListingId", "Ticker", "TradeTimestamp", "Price", "Size", "Classification", "BestBidPrice", "BestAskPrice"]
TP_BATCH = 10  # tickers per query (~300k prints per large-cap name per day)
OPEN_MIN, N_MIN = 9 * 60 + 30, 390


def minute_bars(ticks: pd.DataFrame) -> pd.DataFrame:
    """Trades Plus prints for one date -> one row per (ListingId, regular-session minute 0..389).

    Mid is the last valid NBBO mid quoted before a print in that minute; VWAP and Volume count
    lit-continuous and dark prints only (auction, pre/post-session and off-book prints are not
    addressable by an intraday algo). Cancellations (negative Size) are dropped."""
    t = ticks[ticks["Size"] > 0].copy()
    ts = pd.to_datetime(t["TradeTimestamp"])
    ts = ts.dt.tz_localize("UTC") if ts.dt.tz is None else ts.dt.tz_convert("UTC")
    loc = ts.dt.tz_convert("America/New_York")
    t["Minute"] = loc.dt.hour * 60 + loc.dt.minute - OPEN_MIN
    t = t[(t["Minute"] >= 0) & (t["Minute"] < N_MIN)]
    t["Date"] = loc[t.index].dt.date
    cls = t["Classification"].astype("string").str.upper().fillna("")
    cont = (cls == "LIT_CONTINUOUS") | cls.str.startswith("DARK")
    bid, ask = t["BestBidPrice"].astype(float), t["BestAskPrice"].astype(float)
    t["mid"] = ((bid + ask) / 2).where((bid > 0) & (ask >= bid))
    t["ts"] = ts[t.index]
    keys = ["ListingId", "Date", "Minute"]
    mid = t.dropna(subset=["mid"]).sort_values("ts").groupby(keys)["mid"].last().rename("Mid")
    c = t[cont]
    c = c.assign(px_sz=c["Price"].astype(float) * c["Size"].astype(float))
    g = c.groupby(keys).agg(Volume=("Size", "sum"), Notional=("px_sz", "sum"), Trades=("Size", "size"))
    g["Vwap"] = g["Notional"] / g["Volume"]
    out = pd.concat([mid, g[["Vwap", "Volume", "Trades"]]], axis=1).reset_index()
    out["Volume"] = out["Volume"].fillna(0.0).astype(float)
    out["Trades"] = out["Trades"].fillna(0).astype("int64")
    out["Minute"] = out["Minute"].astype("int16")
    return out[["ListingId", "Date", "Minute", "Mid", "Vwap", "Volume", "Trades"]]


def _tp_query(day: date, tickers: list[str]) -> pd.DataFrame:
    import bmll

    kw = dict(start_date=day, end_date=day, MIC="@SIP", Ticker=tickers)
    try:
        df = bmll.data.query("trades_plus", columns=TP_COLS, **kw)
    except TypeError:  # older SDK without columns=
        df = bmll.data.query("trades_plus", **kw)[TP_COLS]
    return df.to_pandas() if hasattr(df, "to_pandas") else df


def trades_plus_minute(needs: pd.DataFrame, dest: Path) -> int:
    """Pull and aggregate Trades Plus for every (date, ticker) in ``needs`` (columns date, permno,
    listing_id, sip_listing_id, ticker). One chunk per (date, ticker batch), so a restart resumes."""
    import hashlib
    import time

    assert CHUNKS is not None
    needs = needs.assign(date=pd.to_datetime(needs["date"]).dt.date)
    jobs = []
    for day, part in sorted(needs.groupby("date"), key=lambda x: x[0]):
        tick = sorted(part["ticker"].astype(str).unique())
        for k in range(0, len(tick), TP_BATCH):
            jobs.append((day, tick[k : k + TP_BATCH], part))
    print(f"  {len(jobs)} queries over {needs['date'].nunique()} sessions, {len(needs)} (date, ticker) pairs", flush=True)
    t0, done = time.time(), 0
    for n, (day, batch, part) in enumerate(jobs, 1):
        h = hashlib.md5(f"{day}|{','.join(batch)}".encode()).hexdigest()[:12]
        f = CHUNKS / f"tpm_{day}_{h}.parquet"
        if f.exists():
            continue
        ticks = _tp_query(day, batch)
        want = part[part["ticker"].astype(str).isin(batch)]
        sip_to = {int(r.sip_listing_id): (int(r.permno), int(r.listing_id)) for r in want.itertuples()}
        ticks = ticks[ticks["ListingId"].astype("int64").isin(list(sip_to))]  # drop ticker reuse
        bars = minute_bars(ticks) if len(ticks) else pd.DataFrame(columns=["ListingId", "Date", "Minute", "Mid", "Vwap", "Volume", "Trades"])
        sip_ids = bars["ListingId"].astype("int64")
        bars["permno"] = sip_ids.map({k: v[0] for k, v in sip_to.items()}).astype("int64")
        bars["ListingId"] = sip_ids.map({k: v[1] for k, v in sip_to.items()}).astype("int64")  # -> primary
        bars.to_parquet(f, index=False)
        done += 1
        el = time.time() - t0
        eta = el / done * (len(jobs) - n)
        print(f"  [{n}/{len(jobs)}] {day} {len(batch)} tickers: {len(ticks):,} prints -> {len(bars):,} bars "
              f"({el:,.0f}s, ETA ~{eta / 60:,.0f} min)", flush=True)
    # cumulative: every bar chunk pulled so far (earlier runs' needs too), so the registered
    # dataset never loses dates when needs are exported from another run
    parts = [p for f in sorted(CHUNKS.glob("tpm_*.parquet")) if len(p := pd.read_parquet(f))]
    out = pd.concat(parts, ignore_index=True).drop_duplicates(["permno", "Date", "Minute"]) if parts else pd.DataFrame()
    out.to_parquet(dest, index=False)
    return len(out)


def estimate_trades_plus(tickers: list[str], day: date) -> object:
    """Size a Trades Plus @SIP pull before running it (bmll.data.query estimate=True)."""
    import bmll

    return bmll.data.query("trades_plus", start_date=day, end_date=day, MIC="@SIP", Ticker=tickers, estimate=True)


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
    ap.add_argument("--t2-needs", help="CSV from `simp data export-t2-needs <run>`: pull Trades Plus minute bars only")
    a = ap.parse_args()
    login()
    if a.check_auth:
        return
    global CHUNKS
    if a.t2_needs:
        if not a.out:
            ap.error("--out is required")
        out = Path(a.out)
        CHUNKS = out / "_chunks"
        CHUNKS.mkdir(parents=True, exist_ok=True)
        print("T2: Trades Plus @SIP minute bars", flush=True)
        n = trades_plus_minute(pd.read_csv(a.t2_needs), out / "trades_plus_minute.parquet")
        print(f"trades_plus_minute: {n:,} bars written to {out / 'trades_plus_minute.parquet'}", flush=True)
        return
    if not (a.isins and a.start and a.end and a.out):
        ap.error("--isins, --start, --end and --out are required")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    CHUNKS = out / "_chunks"
    CHUNKS.mkdir(exist_ok=True)
    s, e = date.fromisoformat(a.start), date.fromisoformat(a.end)
    spec = pd.read_csv(a.isins)
    isins = spec["isin"].dropna().astype(str).unique().tolist()

    print(f"1/3 reference lookup for {len(isins)} ISINs", flush=True)
    ref_i = reference_by_isin(isins, s, e)
    tickers = unmatched_tickers(spec, set(ref_i["ISIN"].dropna().astype(str)) if len(ref_i) else set())
    print(f"  ticker fallback for {len(tickers)} tickers whose ISIN BMLL does not know", flush=True)
    ref_t = reference_by_ticker(tickers, s, e) if tickers else pd.DataFrame()
    if len(ref_t):
        ref_t = ref_t[~ref_t["ListingId"].isin(ref_i["ListingId"])]
    ref = pd.concat([ref_i, ref_t], ignore_index=True)
    ref.to_parquet(out / "reference_listing.parquet")

    def primaries(r: pd.DataFrame) -> pd.DataFrame:
        if not len(r):
            return r
        return r[(r["IsPrimary"]) & (r["MIC"] == r["OPOL"])] if {"IsPrimary", "OPOL"} <= set(r.columns) else r

    def ids(r: pd.DataFrame) -> list[int]:
        return sorted(r["ListingId"].astype(int).unique().tolist()) if len(r) else []

    # ISIN-matched listings first, ticker-matched ones after: batches are cut from this list in
    # order, so the batches an earlier ISIN-only pull saved stay valid and only the new ones run
    lids_i = ids(primaries(ref_i))
    lids = lids_i + [x for x in ids(primaries(ref_t)) if x not in set(lids_i)]
    n_prim = len(lids)
    # then listings on a venue CRSP names as primary that BMLL's reference does not flag: the
    # reference keeps each listing's latest flags, so a venue move (Linde NYSE -> Nasdaq,
    # 2023-11-07) would otherwise leave the earlier venue's daily figures unpulled
    lids += [x for x in crsp_venue_listings(spec, ref, s, e) if x not in set(lids)]
    print(
        f"reference: {len(ref)} listing rows, {n_prim} primary listings ({n_prim - len(lids_i)} by ticker), "
        f"{len(lids) - n_prim} more on CRSP's primary venue",
        flush=True,
    )

    print("2/3 daily analytics", flush=True)
    METRIC_MAP.update(resolve_metrics(lids[0], out))
    da = daily_analytics(lids, s, e)
    da.to_parquet(out / "daily_analytics.parquet")
    print(f"daily analytics: {len(da)} rows; columns {sorted(da.columns)}")
    print("3/3 classified trades", flush=True)
    # US trade data (and so classified trades) lives on the @SIP listing; the real venue is in
    # ExecutionVenue. SIMP maps @SIP rows back to the primary listing when it builds market_daily.
    def sips(r: pd.DataFrame) -> list[int]:
        if not len(r):
            return []
        p = primaries(r)
        x = r[(r["MIC"] == "@SIP") & r["InstrumentId"].isin(p["InstrumentId"])] if "InstrumentId" in r.columns else r[r["MIC"] == "@SIP"]
        return ids(x)

    sip_i = sips(ref_i)
    sip_ids = sip_i + [x for x in sips(ref) if x not in set(sip_i)]  # same order rule as lids
    if "InstrumentId" in ref.columns:  # the @SIP listing of any instrument pulled only for its venue
        inst = ref.loc[ref["ListingId"].isin(lids), "InstrumentId"]
        sip_ids += [x for x in ids(ref[(ref["MIC"] == "@SIP") & ref["InstrumentId"].isin(inst)]) if x not in set(sip_ids)]
    print(f"  querying {len(sip_ids)} @SIP listings", flush=True)
    rows, labels = classified_trades(sip_ids, s, e, out / "classified_trades.parquet")
    print(f"classified trades: {rows:,} rows written to {out / 'classified_trades.parquet'}", flush=True)
    print("  classifications:", labels, flush=True)
    if not rows:
        print("  WARNING: no classified trades returned: check the account's classified-trades entitlement", flush=True)
    (out / "pull_spec.json").write_text(
        json.dumps(
            {"start": a.start, "end": a.end, "isins": len(isins), "tickers": len(tickers), "listings": len(lids)}
        )
    )


# CRSP primary exchange codes -> MIC; mirrors simp.data.mapping.isin.EXCH_TO_MIC (this env cannot import simp)
EXCH_TO_MIC = {"N": "XNYS", "Q": "XNAS", "A": "XASE", "P": "ARCX", "R": "ARCX", "Z": "BATS", "B": "BATS", "I": "IEXG"}


def crsp_venue_listings(spec: pd.DataFrame, ref: pd.DataFrame, start: date, end: date) -> list[int]:
    """Listings on the MIC CRSP gives as primary for a spec row whose CRSP interval overlaps the
    pull window, matched by ISIN or ticker."""
    if not len(ref) or not {"primaryexch", "valid_from"} <= set(spec.columns):
        return []
    d0 = pd.to_datetime(spec["valid_from"]).dt.date
    d1 = pd.to_datetime(spec["valid_to"]).dt.date.where(spec["valid_to"].notna(), date(9999, 12, 31))
    spec = spec[(d0 <= end) & (d1 >= start)]
    r = ref.assign(_t=ref["Ticker"].astype(str).str.replace(" ", ".", regex=False))
    out: set[int] = set()
    for isin, ticker, code in spec[["isin", "ticker", "primaryexch"]].itertuples(index=False):
        code = str(code).strip().upper() if isinstance(code, str) else ""
        mic = EXCH_TO_MIC.get(code)
        if mic is None:
            continue
        hit = r[(r["MIC"] == mic) & ((r["ISIN"].astype(str) == str(isin)) | (r["_t"] == str(ticker)))]
        out |= set(hit["ListingId"].astype(int))
    return sorted(out)


def unmatched_tickers(spec: pd.DataFrame, found_isins: set[str]) -> list[str]:
    """Tickers of the securities (permnos) none of whose ISINs BMLL returned."""
    if "ticker" not in spec.columns:
        return []
    key = "permno" if "permno" in spec.columns else "ticker"
    hit = set(spec.loc[spec["isin"].astype(str).isin(found_isins), key])
    miss = spec[~spec[key].isin(hit)]
    return sorted({str(t).strip() for t in miss["ticker"].dropna() if str(t).strip()})


if __name__ == "__main__":
    main()
