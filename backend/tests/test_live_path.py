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
