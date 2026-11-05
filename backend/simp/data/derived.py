"""Derived lake datasets built from raw CRSP + BMLL pulls (same code for live and synthetic).

* ``derived/security_map``  — PERMNO -> BMLL listing map (+ review queue written alongside)
* ``derived/market_daily``  — per permno x date: VWAP, TWAP, auction price/volume, imbalance,
  ADV20/60 (trailing, excluding today), 20-session median auction volume, spread, vol, sweep cost
* ``derived/venue_share``   — trailing execution-venue mix per name from classified trades
"""

from __future__ import annotations

import polars as pl

from simp.data.mapping.builder import MappingResult, build_security_map


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


def build_market_daily(raw: dict[tuple[str, str], pl.DataFrame], smap: pl.DataFrame) -> pl.DataFrame:
    dsf = raw[("crsp", "dsf_v2")]
    da = raw[("bmll", "daily_analytics")]
    ct = raw[("bmll", "classified_trades")]
    imb = raw[("bmll", "auction_imbalance")]
    keys = _primary_by_permno_date(smap, dsf)

    auc = (
        ct.filter(pl.col("ClassificationName") == "LIT_CLOSING_AUCTION")
        .group_by("ListingId", "Date")
        .agg(
            pl.col("TradeVolume").sum().alias("auction_volume_raw"),
            pl.col("TradeNotionalUSD").sum().alias("auction_notional_raw"),
        )
    )
    addressable = (
        ct.filter(
            pl.col("ClassificationName").is_in(
                ["LIT_CONTINUOUS", "LIT_CLOSING_AUCTION", "LIT_OPENING_AUCTION", "DARK"]
            )
        )
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
        (pl.col("Spread|RelTWA") * 1e4).fill_null(5.0).alias("spread_bps"),
        pl.col("Volatility|Intraday").fill_null(0.015).alias("vol_intraday"),
        (pl.col("SweepToFill|100kUSDTWASpread") * 1e4).fill_null(10.0).alias("sweep_100k_bps"),
        (pl.col("AuctionDislocation|ClosingLast") * 1e4).fill_null(0.0).alias("dislocation_bps"),
        pl.col("ClosingAuctionPrice|Executed").fill_nan(None).is_null().alias("auction_price_fallback"),
        pl.col("auction_volume_raw").is_null().alias("auction_volume_fallback"),
    )
    return out.with_columns(pl.col("volume").cast(pl.Float64))


def build_venue_share(raw: dict[tuple[str, str], pl.DataFrame], smap: pl.DataFrame) -> pl.DataFrame:
    ct = raw[("bmll", "classified_trades")].filter(
        pl.col("ClassificationName").is_in(["LIT_CONTINUOUS", "DARK"])
    )
    lid = smap.select("permno", pl.col("listing_id").alias("ListingId")).unique()
    v = (
        ct.join(lid, on="ListingId")
        .group_by("permno", "ExecutionVenue")
        .agg(pl.col("TradeVolume").sum().alias("v"))
    )
    v = v.with_columns((pl.col("v") / pl.col("v").sum().over("permno")).alias("share"))
    return v.select("permno", pl.col("ExecutionVenue").alias("venue"), "share").sort("permno", "venue")
