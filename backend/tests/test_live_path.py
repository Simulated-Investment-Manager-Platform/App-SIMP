"""Offline test of the live-data path: WRDS-shaped frames (Decimal numerics, missing optional
columns) are conformed to the contracts, and a live snapshot assembles from staged CRSP + BMLL
files without the optional Auction Imbalance dataset."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import polars as pl

from simp.data.lake.contracts import CONTRACTS, check_contract, conform


def test_conform_casts_wrds_decimals_and_fills_optional() -> None:
    raw = pl.DataFrame(
        {
            "permno": [10001],
            "secinfostartdt": [date(2020, 1, 1)],
            "secinfoenddt": [date(9999, 12, 31)],
            "ticker": ["ABC"],
            "cusip": ["03783310"],
            "cusip9": ["037833100"],
            "primaryexch": ["Q"],
            "extra_wrds_column": [1],
        }
    )
    c = CONTRACTS[("crsp", "stksecurityinfohist")]
    check_contract(conform(raw, c), c)
    dsf = pl.DataFrame(
        {
            "caldt": [date(2025, 1, 2)],
            "vwretd": [Decimal("0.0123")],
            "vwretx": [Decimal("0.011")],
            "sprtrn": [Decimal("0.011")],
            "spindx": [Decimal("5000.12")],
        }
    )
    c2 = CONTRACTS[("crsp", "dsp500")]
    out = conform(dsf, c2)
    check_contract(out, c2)
    assert out["vwretd"].dtype == pl.Float64


def test_live_snapshot_from_staged_files(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from simp.data.lake.store import Lake
    from simp.data.pull import pull_live, write_raw
    from simp.data.synthetic.generator import generate

    w = generate(3, date(2025, 6, 2), date(2026, 3, 31))
    lake = Lake(tmp_path)
    crsp = {k: v for k, v in w.tables.items() if k[0] == "crsp"}
    write_raw(lake, crsp, "CRSP", {}, "test")
    bmll = {k: v for k, v in w.tables.items() if k[0] == "bmll" and k[1] != "auction_imbalance"}
    write_raw(lake, bmll, "BMLL", {}, "test")
    snap = pull_live(lake, date(2025, 6, 2), date(2026, 3, 31), reuse_crsp=True)
    md = lake.read(snap, "derived", "market_daily")
    assert md.height > 0 and (md["imbalance_qty"] == 0).all()
    assert lake.manifest_for(snap, "derived", "market_daily")["data_label"] == "CRSP+BMLL"


def test_wrds_distributions_with_shared_ex_date_and_type() -> None:
    """Real CIZ data: a regular and a special dividend share (permno, ex-date, type)."""
    c = CONTRACTS[("crsp", "stkdistributions")]
    base = {"permno": 14593, "disexdt": date(2024, 2, 9), "distype": "CD", "disfacpr": 0.0, "disfacshr": 0.0}
    no_seq = pl.DataFrame(
        [
            {**base, "disdivamt": Decimal("0.24")},
            {**base, "disdivamt": Decimal("1.00")},
            {**base, "disdivamt": Decimal("1.00")},
        ]
    )  # last row is an exact duplicate
    out = conform(no_seq, c)
    check_contract(out, c)
    assert out.height == 2 and sorted(out["disseqnbr"].to_list()) == [0, 1]
    same_seq = pl.DataFrame(
        [{**base, "disseqnbr": 1, "disdivamt": 0.24}, {**base, "disseqnbr": 1, "disdivamt": 1.0}]
    )
    out2 = conform(same_seq, c)
    check_contract(out2, c)
    assert out2.height == 2 and out2["disseqnbr"].n_unique() == 2


def test_classified_trades_on_sip_listing_map_to_primary() -> None:
    """US classified trades come on the @SIP listing with BMLL's own labels and a Shares column."""
    from simp.data.derived import classified

    smap = pl.DataFrame({"permno": [1], "listing_id": [100], "sip_listing_id": [900]})
    raw = pl.DataFrame(
        {
            "Date": [date(2025, 3, 21)] * 4,
            "ListingId": ["900"] * 4,
            "ExecutionVenue": ["XNAS", "XNAS", "BATS", "FINR"],
            "ClassificationName": ["Lit Closing Auction", "Lit Continuous", "LIT_CONTINUOUS", "Off Book"],
            "Shares": ["1000", "500", "300", "200"],
            "TradeNotionalUSD": ["100000", "50000", "30000", "20000"],
        }
    ).rename({"Shares": "TradeVolume"})
    c = CONTRACTS[("bmll", "classified_trades")]
    out = classified(conform(raw, c), smap)
    assert out["ListingId"].unique().to_list() == [100]
    assert sorted(out["cls"].to_list()) == ["AUC_CLOSE", "CONTINUOUS", "CONTINUOUS", "OTHER"]


def test_bmll_classification_labels() -> None:
    from simp.data.derived import classified

    labels = [
        "AUCTION_ON_DEMAND",
        "BENCHMARK_PRICE",
        "CLOSING_PRICE",
        "DARK_ABOVE_LIS",
        "DARK_BELOW_LIS",
        "LIT_CLOSING_AUCTION",
        "LIT_CONTINUOUS",
        "LIT_OPENING_AUCTION",
        "LIT_POST_TRADE",
        "LIT_PRE_OPEN",
        "LIT_UNSCHEDULED_AUCTION",
        "OTC",
        "PRE_OPEN_AUCTION",
        "SPECIAL_PRICE",
        "Some_New_Dark_Pool",
    ]
    ct = pl.DataFrame({"ListingId": [1] * len(labels), "ClassificationName": labels})
    got = dict(
        zip(
            labels,
            classified(ct, pl.DataFrame({"listing_id": [1], "sip_listing_id": [9]}))["cls"],
            strict=True,
        )
    )
    assert got["LIT_CLOSING_AUCTION"] == "AUC_CLOSE" and got["LIT_OPENING_AUCTION"] == "AUC_OPEN"
    assert (
        got["LIT_CONTINUOUS"] == "CONTINUOUS"
        and got["DARK_BELOW_LIS"] == "DARK"
        and got["Some_New_Dark_Pool"] == "DARK"
    )
    assert {
        got[x] for x in ("LIT_PRE_OPEN", "LIT_POST_TRADE", "PRE_OPEN_AUCTION", "CLOSING_PRICE", "OTC")
    } == {"OTHER"}


def test_mapping_with_missing_cusip9() -> None:
    """Real CRSP intervals can lack cusip9 (use cusip + check digit) or both CUSIPs (review queue)."""
    from simp.data.mapping.builder import build_security_map

    secinfo = pl.DataFrame(
        {
            "permno": [1, 2],
            "secinfostartdt": [date(2020, 1, 1)] * 2,
            "secinfoenddt": [date(9999, 12, 31)] * 2,
            "ticker": ["AAPL", "ZZZ"],
            "cusip": ["03783310", None],
            "cusip9": [None, None],
            "primaryexch": ["Q", "N"],
        }
    )
    ref = pl.DataFrame(
        {
            "ListingId": [10, 11],
            "InstrumentId": [5, 5],
            "ISIN": ["US0378331005", "US0378331005"],
            "FIGI": ["F", "F"],
            "Ticker": ["AAPL", "AAPL"],
            "MIC": ["XNAS", "@SIP"],
            "OPOL": ["XNAS", "XNAS"],
            "IsPrimary": [True, False],
            "ValidFrom": [date(2015, 1, 1)] * 2,
            "ValidTo": [date(9999, 12, 31)] * 2,
        }
    )
    empty_px = pl.DataFrame(schema={"permno": pl.Int64, "dlycaldt": pl.Date, "dlyprc": pl.Float64})
    res = build_security_map(
        secinfo,
        ref,
        empty_px,
        pl.DataFrame(schema={"ListingId": pl.Int64, "Date": pl.Date, "close": pl.Float64}),
    )
    assert res.security_map["permno"].to_list() == [1] and res.security_map["sip_listing_id"].to_list() == [
        11
    ]
    assert res.review_queue["permno"].to_list() == [2]


class _FakeWrds:
    """Stands in for WRDS: ``tables`` maps schema.table to the columns it has."""

    def __init__(self, tables: dict[str, list[str]]) -> None:
        self.tables = tables
        self.queries: list[str] = []

    def fetch(self, sql: str, params=()):  # type: ignore[no-untyped-def]
        self.queries.append(sql)
        if "information_schema.columns" in sql:
            cols = self.tables.get(f"{params[0]}.{params[1]}", [])
            return pl.DataFrame({"column_name": cols}, schema={"column_name": pl.Utf8})
        return pl.DataFrame(
            {"caldt": [date(2026, 6, 30)], "vwretd": [0.0], "vwretx": [0.0], "sprtrn": [0.0], "spindx": [1.0]}
        )


def _benchmark_table(tables: dict[str, list[str]]) -> str:
    from simp.data.crsp import wrds_loader as wl

    saved = dict(wl.TABLES)
    try:
        client = wl.WrdsClient.__new__(wl.WrdsClient)
        fake = _FakeWrds(tables)
        client.fetch = fake.fetch  # type: ignore[method-assign]
        client.benchmark(date(2022, 3, 1), date(2026, 6, 30))
        return fake.queries[-1].split(" from ")[1].split(" ")[0]
    finally:
        wl.TABLES.clear()
        wl.TABLES.update(saved)


def test_benchmark_reads_ciz_dsp500_v2_then_legacy() -> None:
    cols = ["caldt", "vwretd", "vwretx", "sprtrn", "spindx"]
    # the CIZ file continues past 2024; the legacy dsp500 stopped at 2024-12-31
    both = {"crsp_q_indexes.dsp500_v2": cols, "crsp_q_indexes.dsp500": cols}
    assert _benchmark_table(both) == "crsp_q_indexes.dsp500_v2"
    assert _benchmark_table({"crsp_q_indexes.dsp500": cols}) == "crsp_q_indexes.dsp500"
    assert _benchmark_table({"crsp.dsp500": cols}) == "crsp.dsp500"
