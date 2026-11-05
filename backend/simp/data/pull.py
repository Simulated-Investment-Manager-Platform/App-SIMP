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


def pull_live(lake: Lake, start: date, end: date) -> str:
    """CRSP from WRDS; BMLL from files staged by the isolated BMLL puller."""
    from simp.data.crsp.wrds_loader import pull_crsp

    crsp = pull_crsp(start, end)
    write_raw(lake, crsp, "CRSP", {"start": str(start), "end": str(end)}, "wrds-ciz")
    raw = dict(crsp)
    for ds in ("reference_listing", "daily_analytics", "classified_trades", "auction_imbalance"):
        m = lake.latest_manifest("bmll", ds)
        if m is None:
            raise DataMissingError(
                f"BMLL {ds} not pulled — run the BMLL puller then `simp data register-bmll`"
            )
        raw[("bmll", ds)] = pl.read_parquet([str(lake.root / f) for f in m["files"]])
    build_derived(lake, raw, "CRSP+BMLL", {"start": str(start), "end": str(end)})
    return lake.create_snapshot(
        [d for d in SNAPSHOT_DATASETS if d != ("vendor", "sp500_tr")]
        + ([("vendor", "sp500_tr")] if lake.latest_manifest("vendor", "sp500_tr") else [])
    )
