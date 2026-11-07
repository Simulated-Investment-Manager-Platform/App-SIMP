"""Offline test of the live-data path: WRDS-shaped frames (Decimal numerics, missing optional
columns) are conformed to the contracts, and a live snapshot assembles from staged CRSP + BMLL
files without the optional Auction Imbalance dataset."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import numpy as np
import polars as pl
import pytest

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


def test_foreign_issuer_maps_by_ticker_with_price_check() -> None:
    """A CINS CUSIP (Linde, G54950103) has no ``US`` ISIN: BMLL lists it under IE000S9YS762, so the
    ISIN lookup misses and the ticker fallback maps it once the closes agree; a reused ticker whose
    closes disagree goes to the review queue."""
    from simp.data.mapping.builder import build_security_map

    days = [date(2024, 1, d) for d in (2, 3, 4, 5, 8, 9, 10)]
    secinfo = pl.DataFrame(
        {
            "permno": [18143, 99],
            "secinfostartdt": [date(2023, 3, 1), date(2023, 3, 1)],
            "secinfoenddt": [date(9999, 12, 31)] * 2,
            "ticker": ["LIN", "XYZ"],
            "cusip": ["G5495010", "G1234510"],
            "cusip9": ["G54950103", None],
            "primaryexch": ["Q", "N"],
        }
    )
    ref = pl.DataFrame(
        {
            "ListingId": [10, 11, 20],
            "InstrumentId": [5, 5, 7],
            "ISIN": ["IE000S9YS762", "IE000S9YS762", "US0000000000"],
            "FIGI": ["F", "F", "G"],
            "Ticker": ["LIN", "LIN", "XYZ"],
            "MIC": ["XNAS", "@SIP", "XNYS"],
            "OPOL": ["XNAS", "XNAS", "XNYS"],
            "IsPrimary": [True, False, True],
            "ValidFrom": [date(2023, 3, 1)] * 3,
            "ValidTo": [date(9999, 12, 31)] * 3,
        }
    )
    crsp = pl.DataFrame(
        {"permno": [18143] * 7 + [99] * 7, "dlycaldt": days * 2, "dlyprc": [470.0 + i for i in range(7)] * 2}
    )
    bmll = pl.DataFrame(
        {
            "ListingId": [10] * 7 + [20] * 7,
            "Date": days * 2,
            "close": [470.0 + i for i in range(7)] + [12.0] * 7,  # XYZ: another company, other prices
        }
    )
    res = build_security_map(secinfo, ref, crsp, bmll)
    sm = res.security_map
    assert sm["permno"].to_list() == [18143]
    assert sm["method"].to_list() == ["TICKER_PRICE_CHECK"]
    assert sm["listing_id"].to_list() == [10] and sm["sip_listing_id"].to_list() == [11]
    assert sm["isin"].to_list() == ["IE000S9YS762"]
    assert res.review_queue["permno"].to_list() == [99]


def _venue_move_fixture(isin: str, cusip9: str):  # type: ignore[no-untyped-def]
    """CRSP: NYSE until 2023-11-06, Nasdaq from 2023-11-07. BMLL's reference flags only the current
    primary (XNAS) for the whole history; NYSE closes exist before the move, Nasdaq ones after."""
    pre = [date(2023, 3, 2)] + [date(2023, 10, d) for d in (2, 3, 4, 5, 6, 9)]  # trades from day one
    post = [date(2023, 11, d) for d in (7, 8, 9, 10, 13, 14, 15)]
    secinfo = pl.DataFrame(
        {
            "permno": [7, 7],
            "secinfostartdt": [date(2023, 3, 2), date(2023, 11, 7)],
            "secinfoenddt": [date(2023, 11, 6), date(9999, 12, 31)],
            "ticker": ["LIN", "LIN"],
            "cusip": [cusip9[:8]] * 2,
            "cusip9": [cusip9] * 2,
            "primaryexch": ["N", "Q"],
        }
    )
    ref = pl.DataFrame(
        {
            "ListingId": [30, 31, 32],
            "InstrumentId": [9, 9, 9],
            "ISIN": [isin] * 3,
            "FIGI": ["F"] * 3,
            "Ticker": ["LIN"] * 3,
            "MIC": ["XNYS", "XNAS", "@SIP"],
            "OPOL": ["XNAS"] * 3,
            "IsPrimary": [False, True, False],
            "ValidFrom": [date(2023, 4, 3)] * 3,
            "ValidTo": [date(9999, 12, 31)] * 3,
        }
    )
    px = [400.0 + i for i in range(7)]
    crsp = pl.DataFrame({"permno": [7] * 14, "dlycaldt": pre + post, "dlyprc": px + px})
    bmll = pl.DataFrame(
        {"ListingId": [30] * 7 + [31] * 7, "Date": pre + post, "close": px + px},
        schema={"ListingId": pl.Int64, "Date": pl.Date, "close": pl.Float64},
    )
    return secinfo, ref, crsp, bmll


def test_venue_move_maps_each_crsp_interval_to_its_venue() -> None:
    """Linde (CINS CUSIP, ticker path) and a US ISIN name that moved venue (Walmart-style): before
    the move the NYSE listing, after it the Nasdaq one, one listing per day."""
    from simp.data.mapping.builder import build_security_map, validate_mapping

    for isin, cusip9, method in (
        ("IE000S9YS762", "G54950103", "TICKER_PRICE_CHECK"),
        ("US9311421039", "931142103", "ISIN_CRSP_VENUE"),
    ):
        secinfo, ref, crsp, bmll = _venue_move_fixture(isin, cusip9)
        res = build_security_map(secinfo, ref, crsp, bmll)
        sm = res.security_map
        got = [(r["listing_id"], r["opol"], r["valid_from"], r["valid_to"]) for r in sm.iter_rows(named=True)]
        assert got == [
            (30, "XNYS", date(2023, 3, 2), date(2023, 11, 6)),
            (31, "XNAS", date(2023, 11, 7), date(9999, 12, 31)),
        ], isin
        assert sm["method"][0] == method and sm["sip_listing_id"].to_list() == [32, 32]
        needs = crsp.select("permno", pl.col("dlycaldt").alias("date"))
        assert validate_mapping(sm, needs).height == 0
        assert res.review_queue.height == 0


def test_overlapping_listings_give_one_mapping_per_day() -> None:
    """Bunge: the Bermuda listing's snapshot interval runs to 2023-10-31 and the Swiss successor
    trades from 2023-10-31; the later listing wins from its start, so each day has one listing."""
    from simp.data.mapping.builder import _one_listing_per_day

    old = {"listing_id": 1, "valid_from": date(2019, 9, 17), "valid_to": date(2023, 10, 31)}
    new = {"listing_id": 2, "valid_from": date(2023, 10, 31), "valid_to": date(2026, 6, 30)}
    out = _one_listing_per_day([new, old])
    assert [(r["listing_id"], r["valid_from"], r["valid_to"]) for r in out] == [
        (1, date(2019, 9, 17), date(2023, 10, 30)),
        (2, date(2023, 10, 31), date(2026, 6, 30)),
    ]
    inner = {"listing_id": 3, "valid_from": date(2021, 1, 1), "valid_to": date(2021, 1, 31)}
    out = _one_listing_per_day([old, inner])
    assert [(r["listing_id"], r["valid_from"], r["valid_to"]) for r in out] == [
        (1, date(2019, 9, 17), date(2020, 12, 31)),
        (3, date(2021, 1, 1), date(2021, 1, 31)),
        (1, date(2021, 2, 1), date(2023, 10, 31)),
    ]


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


def test_live_t2_replays_trades_plus_minute_bars(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Live snapshot + Trades Plus minute bars: T2 caps each minute at 10% of the REAL minute
    volume and prices off the bar mid; a session without bars falls back and is flagged."""
    from datetime import time

    from simp.accounting.calendar import ny_ts
    from simp.config.run_config import CostModelParams
    from simp.data.lake.store import Lake
    from simp.data.market import load_market
    from simp.data.pull import pull_live, write_raw
    from simp.data.synthetic.generator import generate
    from simp.domain.enums import ExecutionModel
    from simp.domain.ids import IdFactory
    from simp.domain.records import TraderOrder
    from simp.execution.intraday import IntradayService
    from simp.execution.models import Executor

    w = generate(3, date(2025, 6, 2), date(2026, 3, 31))
    lake = Lake(tmp_path)
    write_raw(lake, {k: v for k, v in w.tables.items() if k[0] == "crsp"}, "CRSP", {}, "test")
    bmll = {k: v for k, v in w.tables.items() if k[0] == "bmll" and k[1] != "auction_imbalance"}
    write_raw(lake, bmll, "BMLL", {}, "test")
    snap0 = pull_live(lake, date(2025, 6, 2), date(2026, 3, 31), reuse_crsp=True)
    md0 = load_market(snap0, str(tmp_path))
    i_bar, i_none = len(md0.sessions) - 20, len(md0.sessions) - 10
    j = int(np.flatnonzero(md0.member[i_bar] & md0.member[i_none])[0])
    permno, d = int(md0.permnos[j]), md0.sessions[i_bar]
    bars = pl.DataFrame(
        {
            "ListingId": [int(md0.listing_id[j])] * 390,
            "permno": [permno] * 390,
            "Date": [d] * 390,
            "Minute": list(range(390)),
            "Mid": [100.0 + 0.01 * m for m in range(390)],
            "Vwap": [100.0 + 0.01 * m for m in range(390)],
            "Volume": [1000.0] * 390,
            "Trades": [10] * 390,
        }
    )
    lake.write(
        "bmll", "trades_plus_minute", bars, date_col="Date", query_args={}, sdk_version="t", data_label="BMLL"
    )
    snap = pull_live(lake, date(2025, 6, 2), date(2026, 3, 31), reuse_crsp=True)
    md = load_market(snap, str(tmp_path))
    intra = IntradayService(md)
    assert intra.live and not intra.synthetic and intra.has_bars(i_bar, j) and not intra.has_bars(i_none, j)
    assert intra.price_at(i_bar, j, 100.5) == pytest.approx(100.0 + 0.01 * 100, abs=0.01)
    ex = Executor(md, intra, ExecutionModel.T2, CostModelParams(), IdFactory("t2"), "run", 1)

    def order(i: int) -> TraderOrder:
        s = md.sessions[i]
        return TraderOrder(
            f"to{i}", f"pm{i}", "run", "DESK", "ALGO_VWAP", None, "BRK1", 1_000_000.0,
            ny_ts(s, time(9, 30)), ny_ts(s, time(16, 0)), 100.0, {}, "WORKING", 1.0, permno, "BUY",
        )  # fmt: skip

    got = ex.execute(i_bar, [order(i_bar)]).fills
    assert got and all(f.quantity <= 100 for f in got)  # 10% of 1,000 shares a minute
    assert sum(f.quantity for f in got) <= 390 * 100
    assert all(f.ref_snapshot["interval_volume"] == 1000.0 and not f.ref_snapshot["flags"] for f in got)
    assert all(abs(f.price - 100.0) < 5 for f in got)
    fb = ex.execute(i_none, [order(i_none)]).fills
    assert fb and all(f.ref_snapshot["flags"] == ["INTRADAY_FALLBACK_DAILY"] for f in fb)


def test_t2_fetches_missing_bars_on_demand(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A T2 run on a live snapshot without bars fetches each session's missing (date, name)
    pairs through the puller, caches them, replays them, and never fetches a pair twice."""
    from simp.data.lake.store import Lake
    from simp.data.market import load_market
    from simp.data.pull import pull_live, write_raw
    from simp.data.synthetic.generator import generate
    from simp.data.t2_bars import BarCache
    from simp.runs.engine import Engine
    from tests.conftest import SMALL_END, SMALL_START, cfg

    w = generate(3, SMALL_START, SMALL_END)
    lake = Lake(tmp_path)
    write_raw(lake, {k: v for k, v in w.tables.items() if k[0] == "crsp"}, "CRSP", {}, "test")
    bmll = {k: v for k, v in w.tables.items() if k[0] == "bmll" and k[1] != "auction_imbalance"}
    write_raw(lake, bmll, "BMLL", {}, "test")
    snap = pull_live(lake, SMALL_START, SMALL_END, reuse_crsp=True)

    calls: list[int] = []

    def fake_puller(needs: pl.DataFrame, staging, log) -> pl.DataFrame:  # type: ignore[no-untyped-def]
        calls.append(needs.height)
        rows = [
            (int(r["listing_id"]), int(r["permno"]), r["date"], m, 50.0, 50.0, 1e6, 5)
            for r in needs.iter_rows(named=True)
            for m in range(390)
        ]
        cols = ["ListingId", "permno", "Date", "Minute", "Mid", "Vwap", "Volume", "Trades"]
        return pl.DataFrame(rows, schema=cols, orient="row")

    def run() -> tuple[BarCache, list]:  # type: ignore[type-arg]
        md = load_market(snap, str(tmp_path))
        cache = BarCache(lake, lake.read(snap, "derived", "security_map"),
                         lake.read(snap, "bmll", "reference_listing"), puller=fake_puller)  # fmt: skip
        md.bar_loader = cache.loader(md)
        out = Engine(cfg(execution_model="T2"), md, "r1", bar_cache=cache).run()
        return cache, out.fills

    cache, fills = run()
    t2 = [f for f in fills if f.fill_model == "T2_REPLAY"]
    assert calls and sum(calls) == len(cache.fetched()) and cache.used
    assert t2 and all(not f.ref_snapshot["flags"] for f in t2)
    assert all(f.ref_snapshot["interval_volume"] == 1e6 for f in t2)
    n_calls = len(calls)
    cache2, fills2 = run()  # same lake: everything is cached now
    assert len(calls) == n_calls and cache2.audit_hash() == cache.audit_hash()
    assert [(f.price, f.quantity) for f in fills2] == [(f.price, f.quantity) for f in fills]


def test_live_source_never_picks_a_synthetic_snapshot(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """With SIMP_DATA_SOURCE=live, a synthetic snapshot made after the live one is ignored."""
    from types import SimpleNamespace

    from simp.data.lake.store import Lake
    from simp.data.pull import SNAPSHOT_DATASETS, build_derived, pull_live, write_raw
    from simp.data.synthetic.generator import generate
    from simp.runs import orchestrator

    w = generate(3, date(2025, 6, 2), date(2026, 3, 31))
    lake = Lake(tmp_path)
    write_raw(lake, {k: v for k, v in w.tables.items() if k[0] == "crsp"}, "CRSP", {}, "test")
    write_raw(
        lake,
        {k: v for k, v in w.tables.items() if k[0] == "bmll" and k[1] != "auction_imbalance"},
        "BMLL",
        {},
        "test",
    )
    live = pull_live(lake, date(2025, 6, 2), date(2026, 3, 31), reuse_crsp=True)
    write_raw(lake, w.tables, "SYNTHETIC", {"seed": 3}, "test")
    build_derived(lake, w.tables, "SYNTHETIC", {"seed": 3})
    synth = lake.create_snapshot(SNAPSHOT_DATASETS)
    assert lake.snapshots()[-1]["snapshot_hash"] == synth != live
    monkeypatch.setattr(orchestrator, "Lake", lambda: Lake(tmp_path))
    monkeypatch.setattr(orchestrator, "get_settings", lambda: SimpleNamespace(data_source="live"))
    assert orchestrator.latest_snapshot() == live
    monkeypatch.setattr(orchestrator, "get_settings", lambda: SimpleNamespace(data_source="synthetic"))
    assert orchestrator.latest_snapshot() == synth
