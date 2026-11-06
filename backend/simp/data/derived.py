"""Derived lake datasets built from raw CRSP + BMLL pulls (same code for live and synthetic).

* ``derived/security_map``  — PERMNO -> BMLL listing map (+ review queue written alongside)
* ``derived/market_daily``  — per permno x date: VWAP, TWAP, auction price/volume, imbalance,
  ADV20/60 (trailing, excluding today), 20-session median auction volume, spread, vol, sweep cost
* ``derived/venue_share``   — trailing execution-venue mix per name from classified trades
"""

from __future__ import annotations

import math

import polars as pl

from simp.data.mapping.builder import MappingResult, build_security_map

# BMLL Data Lab daily-analytics units (checked against the live pull: Spread|AbsTWA / price
# matches Spread|RelTWA / 1e4, and Volatility|Intraday / realised daily sigma ~ sqrt(252)):
#   Spread|RelTWA, AuctionDislocation|ClosingLast   basis points
#   Spread|AbsTWA, SweepToFill|*                    USD per share
#   Volatility|Intraday                             annualised
SQRT_252 = math.sqrt(252)


def build_mapping(raw: dict[tuple[str, str], pl.DataFrame]) -> MappingResult:
    da = raw[("bmll", "daily_analytics")]
    return build_security_map(
        raw[("crsp", "stksecurityinfohist")],
        raw[("bmll", "reference_listing")],
        raw[("crsp", "dsf_v2")].select("permno", "dlycaldt", "dlyprc"),
        da.select("ListingId", "Date", pl.col("ClosingAuctionPrice|Executed").alias("close")),
    )


def _primary_by_permno_date(smap: pl.DataFrame, dsf: pl.DataFrame) -> pl.DataFrame:
    keys = dsf.select("permno", pl.col("dlycaldt").alias("date"))
    j = keys.join(smap.select("permno", "listing_id", "valid_from", "valid_to"), on="permno", how="inner")
    return j.filter((pl.col("date") >= pl.col("valid_from")) & (pl.col("date") <= pl.col("valid_to"))).select(
        "permno", "date", "listing_id"
    )


# BMLL ClassificationName -> SIMP bucket (labels seen in a live US @SIP pull). Anything else is
# bucketed by keywords; pre-open / post-trade prints are never counted as regular-session volume.
BMLL_CLASSES = {
    "LIT_CLOSING_AUCTION": "AUC_CLOSE",
    "LIT_OPENING_AUCTION": "AUC_OPEN",
    "LIT_CONTINUOUS": "CONTINUOUS",
    "DARK": "DARK",
    "DARK_ABOVE_LIS": "DARK",
    "DARK_BELOW_LIS": "DARK",
    "LIT_PRE_OPEN": "OTHER",
    "PRE_OPEN_AUCTION": "OTHER",
    "LIT_POST_TRADE": "OTHER",
    "LIT_UNSCHEDULED_AUCTION": "OTHER",
    "AUCTION_ON_DEMAND": "OTHER",
    "CLOSING_PRICE": "OTHER",
    "BENCHMARK_PRICE": "OTHER",
    "SPECIAL_PRICE": "OTHER",
    "OTC": "OTHER",
}


def classified(ct: pl.DataFrame, smap: pl.DataFrame) -> pl.DataFrame:
    """Normalise classified trades: rows on the @SIP listing (where US trade data lives) are moved
    to the primary listing, and BMLL classification labels are bucketed whatever their spelling:
    AUC_CLOSE, AUC_OPEN, CONTINUOUS (lit), DARK or OTHER (off-book etc., not addressable)."""
    sip = {
        int(s): int(p)
        for s, p in smap.select("sip_listing_id", "listing_id").drop_nulls().unique().iter_rows()
    }
    label = pl.col("ClassificationName").cast(pl.Utf8).str.to_uppercase()
    n = label.str.replace_all(r"[^A-Z]", "")
    auction = n.str.contains("AUCTION")
    session_edge = n.str.contains("PRE") | n.str.contains("POST")  # pre-open / post-trade prints
    fallback = (
        pl.when(session_edge)
        .then(pl.lit("OTHER"))
        .when(n.str.contains("CLOS") & auction)
        .then(pl.lit("AUC_CLOSE"))
        .when(n.str.contains("OPEN") & auction)
        .then(pl.lit("AUC_OPEN"))
        .when(n.str.contains("DARK"))
        .then(pl.lit("DARK"))
        .when(n.str.contains("LIT") & ~auction)
        .then(pl.lit("CONTINUOUS"))
        .otherwise(pl.lit("OTHER"))
    )
    cls = label.replace_strict(BMLL_CLASSES, default=fallback, return_dtype=pl.Utf8)
    return ct.with_columns(
        pl.col("ListingId")
        .replace_strict(sip, default=pl.col("ListingId"), return_dtype=pl.Int64)
        .alias("ListingId"),
        cls.alias("cls"),
    )


def build_market_daily(raw: dict[tuple[str, str], pl.DataFrame], smap: pl.DataFrame) -> pl.DataFrame:
    dsf = raw[("crsp", "dsf_v2")]
    da = raw[("bmll", "daily_analytics")]
    ct = classified(raw[("bmll", "classified_trades")], smap)
    imb = raw[("bmll", "auction_imbalance")]
    keys = _primary_by_permno_date(smap, dsf)

    auc = (
        ct.filter(pl.col("cls") == "AUC_CLOSE")
        .group_by("ListingId", "Date")
        .agg(
            pl.col("TradeVolume").sum().alias("auction_volume_raw"),
            pl.col("TradeNotionalUSD").sum().alias("auction_notional_raw"),
        )
    )
    addressable = (
        ct.filter(pl.col("cls").is_in(["CONTINUOUS", "AUC_CLOSE", "AUC_OPEN", "DARK"]))
        .group_by("ListingId", "Date")
        .agg(pl.col("TradeVolume").sum().alias("addr_volume"))
    )

    md = (
        keys.join(
            da.rename({"ListingId": "listing_id", "Date": "date"}), on=["listing_id", "date"], how="left"
        )
        .join(auc.rename({"ListingId": "listing_id", "Date": "date"}), on=["listing_id", "date"], how="left")
        .join(
            addressable.rename({"ListingId": "listing_id", "Date": "date"}),
            on=["listing_id", "date"],
            how="left",
        )
        .join(imb.rename({"ListingId": "listing_id", "Date": "date"}), on=["listing_id", "date"], how="left")
        .join(
            dsf.select("permno", pl.col("dlycaldt").alias("date"), "dlyprc", "dlyvol"),
            on=["permno", "date"],
            how="left",
        )
        .sort("permno", "date")
    )
    md = md.with_columns(
        pl.col("addr_volume").fill_null(pl.col("dlyvol")).alias("volume"),
    ).with_columns(
        # trailing windows exclude today: known before the session starts
        pl.col("volume").shift(1).rolling_mean(20, min_samples=10).over("permno").alias("adv20"),
        pl.col("volume").shift(1).rolling_mean(60, min_samples=20).over("permno").alias("adv60"),
        pl.col("auction_volume_raw")
        .shift(1)
        .rolling_median(20, min_samples=10)
        .over("permno")
        .alias("auc_median20"),
    )
    out = md.select(
        "permno",
        "listing_id",
        "date",
        pl.col("VWAP").fill_null(pl.col("dlyprc")).alias("vwap"),
        pl.col("TWAP").fill_null(pl.col("dlyprc")).alias("twap"),
        pl.col("ClosingAuctionPrice|Executed")
        .fill_nan(None)
        .fill_null(pl.col("dlyprc"))
        .alias("close_auction_price"),
        pl.col("OpeningAuctionPrice|Executed").fill_null(pl.col("dlyprc")).alias("open_auction_price"),
        pl.col("auction_volume_raw").fill_null(pl.col("auc_median20")).fill_null(0.0).alias("auction_volume"),
        (
            pl.col("auction_volume_raw").fill_null(pl.col("auc_median20")).fill_null(0.0) * pl.col("dlyprc")
        ).alias("auction_notional"),
        (
            pl.when(pl.col("ImbalanceSide") == "SELL").then(-1.0).otherwise(1.0)
            * pl.col("ImbalanceQuantity").fill_null(0.0)
        ).alias("imbalance_qty"),
        pl.col("PairedQuantity").fill_null(0.0).alias("paired_qty"),
        "volume",
        "adv20",
        "adv60",
        "auc_median20",
        pl.col("Spread|RelTWA").fill_nan(None).fill_null(5.0).alias("spread_bps"),
        (pl.col("Volatility|Intraday") / SQRT_252).fill_nan(None).fill_null(0.015).alias("vol_intraday"),
        (pl.col("SweepToFill|100kUSDTWASpread") / pl.col("dlyprc").abs() * 1e4)
        .fill_nan(None)
        .fill_null(10.0)
        .alias("sweep_100k_bps"),
        pl.col("AuctionDislocation|ClosingLast").fill_nan(None).fill_null(0.0).alias("dislocation_bps"),
        pl.col("ClosingAuctionPrice|Executed").fill_nan(None).is_null().alias("auction_price_fallback"),
        pl.col("auction_volume_raw").is_null().alias("auction_volume_fallback"),
    )
    return out.with_columns(pl.col("volume").cast(pl.Float64))


def build_venue_share(raw: dict[tuple[str, str], pl.DataFrame], smap: pl.DataFrame) -> pl.DataFrame:
    ct = classified(raw[("bmll", "classified_trades")], smap).filter(
        pl.col("cls").is_in(["CONTINUOUS", "DARK"])
    )
    ct = ct.with_columns(
        pl.when(pl.col("cls") == "DARK")
        .then(pl.lit("DARK"))
        .otherwise(pl.col("ExecutionVenue"))
        .alias("ExecutionVenue")
    )
    lid = smap.select("permno", pl.col("listing_id").alias("ListingId")).unique()
    v = (
        ct.join(lid, on="ListingId")
        .group_by("permno", "ExecutionVenue")
        .agg(pl.col("TradeVolume").sum().alias("v"))
    )
    v = v.with_columns((pl.col("v") / pl.col("v").sum().over("permno")).alias("share"))
    return v.select("permno", pl.col("ExecutionVenue").alias("venue"), "share").sort("permno", "venue")
