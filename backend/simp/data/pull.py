"""Data pulls: raw CRSP + BMLL into the lake, then derived datasets and a pinned snapshot."""

from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl
import structlog

from simp.data.derived import build_mapping, build_market_daily, build_venue_share
from simp.data.lake.store import Lake
from simp.errors import DataMissingError

log = structlog.get_logger(__name__)

DAILY = {
    ("crsp", "dsf_v2"): "dlycaldt",
    ("crsp", "dsp500"): "caldt",
    ("vendor", "sp500_tr"): "caldt",
    ("bmll", "daily_analytics"): "Date",
    ("bmll", "classified_trades"): "Date",
    ("bmll", "auction_imbalance"): "Date",
    ("derived", "market_daily"): "date",
}

SNAPSHOT_DATASETS = [
    ("crsp", "dsf_v2"),
    ("crsp", "stksecurityinfohist"),
    ("crsp", "stkdistributions"),
    ("crsp", "stkdelists"),
    ("crsp", "dsp500list_v2"),
    ("crsp", "dsp500"),
    ("vendor", "sp500_tr"),
    ("bmll", "reference_listing"),
    ("derived", "security_map"),
    ("derived", "market_daily"),
    ("derived", "venue_share"),
]


def write_raw(
    lake: Lake, raw: dict[tuple[str, str], pl.DataFrame], label: str, args: dict[str, Any], sdk: str
) -> None:
    for (source, ds), df in raw.items():
        lake.write(
            source,
            ds,
            df,
            date_col=DAILY.get((source, ds)),
            query_args=args,
            sdk_version=sdk,
            data_label=label,
        )
        log.info("lake.write", source=source, dataset=ds, rows=df.height)


def build_derived(
    lake: Lake, raw: dict[tuple[str, str], pl.DataFrame], label: str, args: dict[str, Any]
) -> pl.DataFrame:
    res = build_mapping(raw)
    lake.write(
        "derived",
        "security_map",
        res.security_map,
        date_col=None,
        query_args=args,
        sdk_version="simp",
        data_label=label,
    )
    md = build_market_daily(raw, res.security_map)
    lake.write(
        "derived", "market_daily", md, date_col="date", query_args=args, sdk_version="simp", data_label=label
    )
    vs = build_venue_share(raw, res.security_map)
    lake.write(
        "derived", "venue_share", vs, date_col=None, query_args=args, sdk_version="simp", data_label=label
    )
    q = lake.root / "derived" / "security_map" / "review_queue.parquet"
    res.review_queue.write_parquet(q)
    return res.review_queue


def pull_synthetic(lake: Lake, seed: int) -> str:
    from simp.data.synthetic.generator import generate

    w = generate(seed)
    args = {"generator": "simp.data.synthetic", "seed": seed}
    write_raw(lake, w.tables, "SYNTHETIC", args, "simp-synthetic-1")
    build_derived(lake, w.tables, "SYNTHETIC", args)
    return lake.create_snapshot(SNAPSHOT_DATASETS)


def _latest(lake: Lake, source: str, dataset: str) -> pl.DataFrame | None:
    m = lake.latest_manifest(source, dataset)
    if m is None:
        return None
    return pl.read_parquet([str(lake.root / f) for f in m["files"]])


CRSP_DATASETS = ["dsf_v2", "stksecurityinfohist", "stkdistributions", "stkdelists", "dsp500list_v2", "dsp500"]


def pull_crsp_only(lake: Lake, start: date, end: date) -> dict[str, int]:
    """Step 1 of a live pull: CRSP from WRDS into the lake (needed to build the ISIN list for BMLL)."""
    from simp.data.crsp.wrds_loader import pull_crsp

    crsp = pull_crsp(start, end)
    write_raw(lake, crsp, "CRSP", {"start": str(start), "end": str(end)}, "wrds-ciz")
    return {f"{s}/{d}": df.height for (s, d), df in crsp.items()}


def pull_live(lake: Lake, start: date, end: date, *, reuse_crsp: bool = False) -> str:
    """CRSP from WRDS (or the last CRSP pull with ``reuse_crsp``); BMLL from files staged by the
    isolated BMLL puller and registered with ``simp data register-bmll``."""
    from simp.data.lake.contracts import CONTRACTS

    if reuse_crsp:
        raw: dict[tuple[str, str], pl.DataFrame] = {}
        for ds in CRSP_DATASETS:
            df = _latest(lake, "crsp", ds)
            if df is None:
                raise DataMissingError(
                    f"no CRSP {ds} in the lake: run `simp data pull --source live --crsp-only` first"
                )
            raw[("crsp", ds)] = df
    else:
        pull_crsp_only(lake, start, end)
        raw = {("crsp", ds): df for ds in CRSP_DATASETS if (df := _latest(lake, "crsp", ds)) is not None}
    for ds in ("reference_listing", "daily_analytics", "classified_trades"):
        df = _latest(lake, "bmll", ds)
        if df is None:
            raise DataMissingError(
                f"BMLL {ds} not pulled: run the BMLL puller, then `simp data register-bmll`"
            )
        raw[("bmll", ds)] = df
    imb = _latest(lake, "bmll", "auction_imbalance")
    if imb is None:
        # Auction Imbalance detail is a Data Lab dataset; without it, imbalance falls back to zero
        # (flagged by the empty dataset) and daily AuctionOrderImbalance metrics still feed the TCA.
        c = CONTRACTS[("bmll", "auction_imbalance")]
        imb = pl.DataFrame(schema=dict(c.columns))
    raw[("bmll", "auction_imbalance")] = imb
    build_derived(lake, raw, "CRSP+BMLL", {"start": str(start), "end": str(end)})
    optional = [("vendor", "sp500_tr"), ("bmll", "trades_plus_minute")]  # T2 needs the latter
    return lake.create_snapshot(
        [d for d in SNAPSHOT_DATASETS if d not in optional]
        + [d for d in optional if lake.latest_manifest(*d)]
    )
