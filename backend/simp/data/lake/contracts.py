"""Data contracts: column names, types and non-null keys checked on every lake write.

Column names for CRSP follow the CIZ (v2) conventions used by the handoff
(``crsp_q_stock`` / ``crsp_q_indexes``). BMLL names follow the Data Lab daily-analytics
and reference schemas. A schema change needs a version bump here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl

F64, I64, STR, DATE, BOOL = pl.Float64, pl.Int64, pl.Utf8, pl.Date, pl.Boolean


@dataclass(frozen=True)
class Contract:
    version: int
    columns: dict[str, type[pl.DataType] | pl.DataType]
    keys: tuple[str, ...]
    optional: frozenset[str] = field(default_factory=frozenset)


CONTRACTS: dict[tuple[str, str], Contract] = {
    # ---------------- CRSP (WRDS, CIZ v2)
    ("crsp", "dsf_v2"): Contract(
        1,
        {
            "permno": I64,
            "dlycaldt": DATE,
            "dlyprc": F64,
            "dlyret": F64,
            "dlyretx": F64,
            "dlyvol": F64,
            "dlycap": F64,
            "shrout": F64,
            "dlyfacprc": F64,
            "dlyopen": F64,
            "dlyclose": F64,
            "dlybid": F64,
            "dlyask": F64,
        },
        ("permno", "dlycaldt"),
    ),
    ("crsp", "stksecurityinfohist"): Contract(
        1,
        {
            "permno": I64,
            "secinfostartdt": DATE,
            "secinfoenddt": DATE,
            "ticker": STR,
            "cusip": STR,
            "cusip9": STR,
            "primaryexch": STR,
            "securitytype": STR,
            "sharetype": STR,
            "issuernm": STR,
        },
        ("permno", "secinfostartdt"),
        optional=frozenset({"issuernm", "securitytype", "sharetype"}),
    ),
    # CIZ can carry several distributions with the same ex-date and type (e.g. regular + special
    # dividend), distinguished by the distribution sequence number.
    ("crsp", "stkdistributions"): Contract(
        2,
        {
            "permno": I64,
            "disexdt": DATE,
            "disseqnbr": I64,
            "dispaydt": DATE,
            "disrecorddt": DATE,
            "disdivamt": F64,
            "disfacpr": F64,
            "disfacshr": F64,
            "distype": STR,
            "disdeclaredt": DATE,
        },
        ("permno", "disexdt", "distype", "disseqnbr"),
        optional=frozenset({"dispaydt", "disrecorddt", "disdeclaredt", "disseqnbr"}),
    ),
    ("crsp", "stkdelists"): Contract(
        2,
        {"permno": I64, "delistingdt": DATE, "delret": F64, "delactiontype": STR},
        ("permno", "delistingdt"),
        optional=frozenset({"delret"}),
    ),
    ("crsp", "dsp500list_v2"): Contract(
        1,
        {"permno": I64, "mbrstartdt": DATE, "mbrenddt": DATE},
        ("permno", "mbrstartdt"),
    ),
    ("crsp", "dsp500"): Contract(
        1,
        {"caldt": DATE, "vwretd": F64, "vwretx": F64, "sprtrn": F64, "spindx": F64},
        ("caldt",),
    ),
    # Official S&P 500 Total Return from a licensed vendor (Compustat or other).
    ("vendor", "sp500_tr"): Contract(1, {"caldt": DATE, "tr_return": F64, "source": STR}, ("caldt",)),
    # ---------------- BMLL
    ("bmll", "reference_listing"): Contract(
        1,
        {
            "ListingId": I64,
            "InstrumentId": I64,
            "ISIN": STR,
            "FIGI": STR,
            "FIGIComposite": STR,
            "Ticker": STR,
            "MIC": STR,
            "OPOL": STR,
            "IsPrimary": BOOL,
            "IsAlive": BOOL,
            "MarketDataLevel": STR,
            "ValidFrom": DATE,
            "ValidTo": DATE,
        },
        ("ListingId", "ValidFrom"),
    ),
    ("bmll", "daily_analytics"): Contract(
        1,
        {
            "ListingId": I64,
            "Date": DATE,
            "MIC": STR,
            "VWAP": F64,
            "TWAP": F64,
            "ClosingAuctionPrice|Executed": F64,
            "OpeningAuctionPrice|Executed": F64,
            "Spread|RelTWA": F64,
            "Spread|RelVWA": F64,
            "Spread|AbsTWA": F64,
            "Volatility|Intraday": F64,
            "SweepToFill|25kUSDTWASpread": F64,
            "SweepToFill|100kUSDTWASpread": F64,
            "SweepToFill|250kUSDTWASpread": F64,
            "AuctionOrderImbalance|Abs": F64,
            "AuctionOrderImbalance|Notional": F64,
            "AuctionDislocation|ClosingLast": F64,
            "Volume": F64,
        },
        ("ListingId", "Date"),
    ),
    ("bmll", "classified_trades"): Contract(
        1,
        {
            "ListingId": I64,
            "Date": DATE,
            "ClassificationName": STR,
            "ExecutionVenue": STR,
            "TradeVolume": F64,
            "TradeNotionalUSD": F64,
        },
        ("ListingId", "Date", "ClassificationName", "ExecutionVenue"),
    ),
    ("bmll", "auction_imbalance"): Contract(
        1,
        {
            "ListingId": I64,
            "Date": DATE,
            "ImbalanceSide": STR,
            "ImbalanceQuantity": F64,
            "PairedQuantity": F64,
            "ReferencePrice": F64,
            "IndicativeUncrossingPrice": F64,
        },
        ("ListingId", "Date"),
    ),
    # ---------------- Derived
    ("derived", "market_daily"): Contract(
        1,
        {
            "permno": I64,
            "listing_id": I64,
            "date": DATE,
            "vwap": F64,
            "twap": F64,
            "close_auction_price": F64,
            "open_auction_price": F64,
            "auction_volume": F64,
            "auction_notional": F64,
            "imbalance_qty": F64,
            "paired_qty": F64,
            "volume": F64,
            "adv20": F64,
            "adv60": F64,
            "auc_median20": F64,
            "spread_bps": F64,
            "vol_intraday": F64,
            "sweep_100k_bps": F64,
            "dislocation_bps": F64,
            "auction_price_fallback": BOOL,
            "auction_volume_fallback": BOOL,
        },
        ("permno", "date"),
        optional=frozenset({"adv20", "adv60", "auc_median20"}),
    ),
    ("derived", "venue_share"): Contract(
        1,
        {"permno": I64, "venue": STR, "share": F64},
        ("permno", "venue"),
    ),
    ("derived", "security_map"): Contract(
        1,
        {
            "permno": I64,
            "listing_id": I64,
            "instrument_id": I64,
            "sip_listing_id": I64,
            "isin": STR,
            "figi": STR,
            "ticker": STR,
            "opol": STR,
            "valid_from": DATE,
            "valid_to": DATE,
            "method": STR,
            "confidence": F64,
        },
        ("permno", "valid_from"),
        optional=frozenset({"listing_id", "instrument_id", "sip_listing_id", "figi", "opol"}),
    ),
}


class ContractViolation(ValueError):
    pass


def check_contract(df: pl.DataFrame, c: Contract) -> None:
    missing = [k for k in c.columns if k not in df.columns]
    if missing:
        raise ContractViolation(f"missing columns {missing}")
    extra = [k for k in df.columns if k not in c.columns]
    if extra:
        raise ContractViolation(f"unexpected columns {extra}")
    for col, typ in c.columns.items():
        if df.schema[col] != typ:
            raise ContractViolation(f"column {col} is {df.schema[col]}, contract says {typ}")
    for k in c.keys:
        if k not in c.optional and df[k].null_count() > 0:
            raise ContractViolation(f"key column {k} has nulls")
    if df.height and df.select(c.keys).is_duplicated().any():
        raise ContractViolation(f"duplicate keys {c.keys}")


def conform(df: pl.DataFrame, c: Contract) -> pl.DataFrame:
    """Cast a source frame to the contract: contract dtypes (Decimal -> Float64 etc.), optional
    columns that the source lacks added as nulls, extra columns dropped. Required columns that
    are missing still fail in ``check_contract``."""
    out = []
    for col, typ in c.columns.items():
        if col in df.columns:
            out.append(pl.col(col).cast(typ, strict=False))
        elif col in c.optional:
            out.append(pl.lit(None, dtype=typ).alias(col))
    df = df.select(out).unique(maintain_order=True)  # exact duplicate rows carry no information
    if "disseqnbr" in df.columns:
        # number events that share (permno, ex-date, type); keep any source sequence, make keys unique
        grp = ["permno", "disexdt", "distype"]
        df = df.with_columns(pl.col("disseqnbr").fill_null(pl.int_range(pl.len()).over(grp)))
        dup = pl.int_range(pl.len()).over([*grp, "disseqnbr"])
        df = df.with_columns(
            pl.when(dup > 0)
            .then(pl.col("disseqnbr") * 1000 + dup)
            .otherwise(pl.col("disseqnbr"))
            .alias("disseqnbr")
        )
    if "dlyprc" in df.columns:  # legacy SIZ used negative prices for bid/ask averages; CIZ should not
        df = df.with_columns(pl.col("dlyprc").abs())
    return df
