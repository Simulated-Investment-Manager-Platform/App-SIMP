"""Unit tests: calendar, ISIN, config validation, impact models, weighted stats, desk policy, lake contracts."""

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl
import pytest

from simp.accounting.calendar import SessionCalendar, nyse_sessions, quarterly_effective_dates
from simp.analytics.weighted import (
    SIZE_EDGES,
    SIZE_LABELS,
    block_bootstrap_ci,
    bucket,
    concentration_curve,
    wmean,
    wquantile,
)
from simp.config.run_config import CostModelParams, RunConfig, Window, check_release
from simp.data.mapping.isin import is_valid_isin, isin_from_cusip9
from simp.desk.policy import DeskPolicy, OrderContext, apply_spill_cap, load_policy, plan_order
from simp.errors import DataWindowError
from simp.execution.impact import arrival_is_bps, auction_impact_bps, participation_impact_bps


# ------------------------------------------------------------------ calendar
def test_sessions_and_holidays() -> None:
    s = nyse_sessions(date(2025, 1, 1), date(2025, 12, 31))
    assert date(2025, 1, 9) not in s  # national day of mourning
    assert date(2025, 4, 18) not in s  # Good Friday
    assert date(2025, 6, 19) not in s  # Juneteenth
    assert len(s) == 250


def test_effective_dates_juneteenth_2026() -> None:
    cal = SessionCalendar(nyse_sessions(date(2026, 1, 1), date(2026, 12, 31)))
    eff = quarterly_effective_dates(cal, date(2026, 1, 1), date(2026, 12, 31))
    assert eff[0] == date(2026, 3, 20)
    assert eff[1] == date(2026, 6, 18)  # third Friday 06-19 is Juneteenth -> prior session


def test_settlement_cycle_cutover() -> None:
    cal = SessionCalendar(nyse_sessions(date(2024, 5, 1), date(2024, 6, 30)))
    assert cal.settlement_date(date(2024, 5, 24)) == date(2024, 5, 29)  # T+2 across Memorial Day
    assert cal.settlement_date(date(2024, 5, 28)) == date(2024, 5, 29)  # T+1 from 2024-05-28


# ------------------------------------------------------------------ identifiers
def test_isin_check_digit() -> None:
    assert isin_from_cusip9("037833100") == "US0378331005"  # Apple
    assert isin_from_cusip9("594918104") == "US5949181045"  # Microsoft
    assert is_valid_isin("US0378331005")
    assert not is_valid_isin("US0378331006")


# ------------------------------------------------------------------ config (Andrew's assert fix)
def test_window_validator() -> None:
    with pytest.raises(ValueError, match="after end"):
        Window(bootstrap=date(2022, 1, 1), start=date(2023, 1, 2), end=date(2023, 1, 1))


def test_check_release_always_checks_both_bounds() -> None:
    w = Window(bootstrap=date(2022, 6, 21), start=date(2022, 7, 1), end=date(2026, 6, 30))
    check_release(w, date(2022, 1, 3), date(2026, 7, 31))
    with pytest.raises(DataWindowError):
        check_release(
            w, date(2022, 1, 3), date(2026, 6, 1)
        )  # a 'cache' mode can no longer short-circuit this


def test_config_hash_ignores_labels() -> None:
    a, b = RunConfig(label="x"), RunConfig(label="y")
    assert a.config_hash() == b.config_hash()
    assert RunConfig(seed=1).config_hash() != RunConfig(seed=2).config_hash()


def test_config_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError):
        RunConfig.model_validate({"not_a_field": 1})


# ------------------------------------------------------------------ impact models
def test_impact_formulas() -> None:
    p = CostModelParams()
    assert participation_impact_bps(1e4, 1e6, 0.02, p) == pytest.approx(0.7 * 0.02 * 0.1 * 1e4)
    assert arrival_is_bps(1e4, 1e6, 0.02, 4.0, p) == pytest.approx(2.0 + 14.0)
    base = auction_impact_bps(1e4, 1e6, 0.02, False, p)
    assert base == pytest.approx(0.5 * 0.02 * 0.1 * 1e4)
    assert auction_impact_bps(1e4, 1e6, 0.02, True, p) == pytest.approx(base * 1.5)
    assert auction_impact_bps(0, 1e6, 0.02, True, p) == 0


# ------------------------------------------------------------------ weighted statistics
def test_weighted_mean_and_quantile() -> None:
    c = np.array([1.0, 100.0])
    n = np.array([99.0, 1.0])
    assert wmean(c, n) == pytest.approx(1.99)
    q = wquantile(np.arange(101.0), np.ones(101), 0.5)
    assert q[0] == pytest.approx(50.0, abs=0.6)


def test_block_bootstrap_contains_mean() -> None:
    rng = np.random.default_rng(1)
    x = rng.normal(5, 2, 2000)
    w = rng.pareto(1.2, 2000) + 1
    blocks = rng.integers(0, 16, 2000)
    lo, hi = block_bootstrap_ci(x, w, blocks, n_boot=500)
    assert lo < wmean(x, w) < hi


def test_buckets_are_right_closed() -> None:
    b = bucket(np.array([0.0005, 0.001, 0.002, 0.2, np.nan]), SIZE_EDGES, SIZE_LABELS)
    assert list(b) == ["<=0.1%", "<=0.1%", "0.1-0.5%", ">10%", "n/a"]


def test_concentration_curve() -> None:
    n = np.r_[np.full(10, 8.5), np.full(90, 15 / 90)]
    _, _, top10 = concentration_curve(n)
    assert top10 == pytest.approx(0.85, abs=1e-9)


# ------------------------------------------------------------------ desk policy
def _ctx(**kw: object) -> OrderContext:
    base = dict(
        reason="INDEX_REBALANCE",
        instruction="TARGET_CLOSE",
        quantity=1000.0,
        notional=1e6,
        adv20=1e6,
        expected_auction=1e5,
        basket_key=None,
    )
    base.update(kw)
    return OrderContext(**base)  # type: ignore[arg-type]


def test_policy_moc_when_small() -> None:
    rule, legs = plan_order(load_policy("default"), _ctx(quantity=4000), np.random.default_rng(0))
    assert rule == "target-close-fits-auction" and [leg.strategy for leg in legs] == ["MOC"]


def test_policy_split_when_large() -> None:
    rule, legs = plan_order(load_policy("default"), _ctx(quantity=20000), np.random.default_rng(0))
    assert rule == "target-close-large"
    assert [(leg.strategy, leg.quantity) for leg in legs] == [("MOC", 5000), ("ALGO_VWAP", 15000)]


def test_policy_flows_always_moc_and_spill() -> None:
    pol = load_policy("default")
    rule, legs = plan_order(pol, _ctx(reason="SUBSCRIPTION", quantity=50000), np.random.default_rng(0))
    assert rule == "flows-moc"
    spilled = apply_spill_cap(pol, legs, 1e5)
    assert [(leg.strategy, leg.quantity) for leg in spilled] == [("MOC", 25000), ("ALGO_VWAP", 25000)]


def test_policy_portfolio_trade_basket() -> None:
    rule, legs = plan_order(
        load_policy("default"), _ctx(notional=20000, basket_key="D1"), np.random.default_rng(0)
    )
    assert rule == "small-basket-portfolio-trade" and legs[0].program_key == "D1"


def test_policy_validation() -> None:
    data = load_policy("default").model_dump()
    data["rules"] = data["rules"][:-1]
    with pytest.raises(ValueError, match="catch-all"):
        DeskPolicy.model_validate(data)


# ------------------------------------------------------------------ lake contracts
def test_lake_contract_and_tamper_detection(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from simp.data.lake.contracts import ContractViolation
    from simp.data.lake.store import Lake
    from simp.errors import DataMissingError

    lake = Lake(tmp_path)
    df = pl.DataFrame(
        {
            "caldt": [date(2025, 1, 2)],
            "vwretd": [0.01],
            "vwretx": [0.009],
            "sprtrn": [0.009],
            "spindx": [5000.0],
        }
    )
    lake.write("crsp", "dsp500", df, date_col="caldt", query_args={}, sdk_version="t", data_label="SYNTHETIC")
    with pytest.raises(ContractViolation):
        lake.write(
            "crsp",
            "dsp500",
            df.drop("spindx"),
            date_col="caldt",
            query_args={},
            sdk_version="t",
            data_label="SYNTHETIC",
        )
    h = lake.create_snapshot([("crsp", "dsp500")])
    lake.verify_snapshot(h)
    f = next((tmp_path / "crsp" / "dsp500").rglob("*.parquet"))
    f.write_bytes(f.read_bytes() + b"x")
    with pytest.raises(DataMissingError, match="changed"):
        lake.verify_snapshot(h)
