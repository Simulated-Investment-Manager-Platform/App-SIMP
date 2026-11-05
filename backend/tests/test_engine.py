"""Engine-level tests on a small synthetic world: order-chain invariants, known-answer TCA,
timing regression, mapping, determinism, hard stops."""

from __future__ import annotations

from collections import defaultdict

import numpy as np
import pytest

from simp.analytics.tca import build_tca
from simp.runs.engine import Engine
from simp.runs.orchestrator import output_hash


@pytest.fixture(scope="module")
def t1_run(md, make_cfg):  # type: ignore[no-untyped-def]
    cfg = make_cfg(execution_model="T1")
    return cfg, Engine(cfg, md, "01TESTT1").run()


def test_order_chain_invariants(t1_run, md) -> None:  # type: ignore[no-untyped-def]
    _, out = t1_run
    pm = {o.pm_order_id: o for o in out.pm_orders}
    to = {t.trader_order_id: t for t in out.trader_orders}
    ch = {c.child_order_id: c for c in out.children}
    assert out.fills, "the run traded"
    by_to: dict[str, float] = defaultdict(float)
    by_pm: dict[str, float] = defaultdict(float)
    for f in out.fills:
        t = to[f.trader_order_id]
        c = ch[f.child_order_id]
        p = pm[t.pm_order_id]
        assert f.pm_order_id == p.pm_order_id  # 100% lineage
        assert p.decision_ts <= t.start_ts <= c.sent_ts <= f.exec_ts <= t.end_ts  # timestamps run forward
        by_to[t.trader_order_id] += f.quantity
    for t in out.trader_orders:
        assert by_to[t.trader_order_id] <= t.quantity + 1e-9  # fills never overrun
        by_pm[t.pm_order_id] += t.quantity
        if by_to[t.trader_order_id] < t.quantity - 1e-9:
            assert t.status in ("EXPIRED", "CANCELLED")  # leftovers are explicit
    for k, q in by_pm.items():
        assert q <= pm[k].quantity * pm[k].quantity_adjustment_factor + 1e-9


def test_nav_identity_and_units(t1_run) -> None:  # type: ignore[no-untyped-def]
    _, out = t1_run
    for r in out.nav:
        assert abs(r.cash + r.equity_value + r.receivables + r.payables - r.nav) < 0.01
        assert abs(r.units * r.unit_nav - r.nav) < 0.01
        # settled cash can dip while sales settle; net liquid cash (cash + receivables - payables) never does
        assert r.cash + r.receivables + r.payables >= -1e-4 * r.nav, f"fund borrowing on {r.date}"


def test_flows_deal_at_pre_flow_nav(t1_run) -> None:  # type: ignore[no-untyped-def]
    _, out = t1_run
    navs = {r.date: r for r in out.nav}
    assert out.flows
    for f in out.flows[:50]:
        assert f.unit_nav_used == pytest.approx(navs[f.date].unit_nav)
        assert f.units == pytest.approx(f.amount_usd / f.unit_nav_used)


def test_rebalance_on_effective_close(t1_run, md) -> None:  # type: ignore[no-untyped-def]
    _, out = t1_run
    rebal_dates = {o.exec_date for o in out.pm_orders if o.reason == "INDEX_REBALANCE"}
    from simp.accounting.calendar import quarterly_effective_dates

    eff = set(quarterly_effective_dates(md.cal, md.sessions[0], md.sessions[-1]))
    assert rebal_dates and rebal_dates <= eff


def test_determinism(md, make_cfg) -> None:  # type: ignore[no-untyped-def]
    a = output_hash(Engine(make_cfg(execution_model="T1"), md, "01DETA").run())
    b = output_hash(Engine(make_cfg(execution_model="T1"), md, "01DETB").run())
    c = output_hash(Engine(make_cfg(execution_model="T1", seed=43), md, "01DETC").run())
    assert a == b, "same config + data -> identical outputs"
    assert a != c, "a different seed changes the flows"


def test_known_answer_tca(md, make_cfg) -> None:  # type: ignore[no-untyped-def]
    """Generator-injected costs are recovered within 0.5 bps notional-weighted."""
    cfg = make_cfg(
        strategy="synthetic_generator",
        fund_id="SYNTH_GENERATOR",
        flows={"enabled": False, "preset": "none", "cash_sweep": False, "cash_buffer_bps": 0.0},
        fee_bps=0.0,
    )
    out = Engine(cfg, md, "01KAT").run()
    rows, _ = build_tca(out, md, cfg.cost, str(cfg.timing.rule))
    rows = [r for r in rows if r["answer_key"]]  # corporate-action exits carry no injected cost
    assert len(rows) > 500
    w = np.array([r["exec_notional"] for r in rows])
    measured = np.array([r["slip_vwap_bps"] for r in rows])
    injected = np.array([r["answer_key"]["injected_cost_bps"] for r in rows])
    noise = np.array([r["answer_key"]["noise_bps"] for r in rows])
    err = np.sum(w * (measured - noise - injected)) / np.sum(w)
    assert abs(err) < 0.5, f"TCA missed the injected cost by {err:.3f} bps"
    # heavy tail: a small share of orders carries most of the notional, like the handoff
    s = np.sort(w)[::-1]
    assert s[: len(s) // 10].sum() / s.sum() > 0.5


def test_timing_regression(md, make_cfg) -> None:  # type: ignore[no-untyped-def]
    """Effective-date timing removes the one-day-lag tracking error of the handoff timing under T0."""

    def te(rule: str) -> float:
        cfg = make_cfg(
            execution_model="T0",
            timing={"rule": rule},
            flows={"enabled": False, "preset": "none", "cash_sweep": False, "cash_buffer_bps": 0.0},
            fee_bps=0.0,
        )
        out = Engine(cfg, md, f"01TIME{rule}").run()
        a = np.array([r.fund_return - r.bench_return_tr for r in out.nav if r.date >= cfg.window.start][1:])
        return float(np.std(a) * np.sqrt(252) * 1e4)

    assert te("EFFECTIVE_DATE") < te("HANDOFF")


def test_mapping_price_check_and_queue(md, snapshot) -> None:  # type: ignore[no-untyped-def]
    import polars as pl

    from simp.data.lake.store import Lake
    from simp.data.mapping.builder import validate_mapping

    lake = Lake()
    sm = lake.read(snapshot, "derived", "security_map")
    q = pl.read_parquet(lake.root / "derived" / "security_map" / "review_queue.parquet")
    assert q.height == 1, "the unmappable name goes to the review queue, never dropped silently"
    assert "TICKER_PRICE_CHECK" in sm["method"].to_list()
    dsf = lake.read(snapshot, "crsp", "dsf_v2")
    m = lake.latest_manifest("bmll", "daily_analytics")
    assert m is not None
    da = pl.read_parquet([str(lake.root / f) for f in m["files"]])
    j = (
        dsf.join(sm.select("permno", "listing_id"), on="permno")
        .join(
            da.select(
                pl.col("ListingId").alias("listing_id"),
                pl.col("Date").alias("dlycaldt"),
                pl.col("ClosingAuctionPrice|Executed").alias("bc"),
            ),
            on=["listing_id", "dlycaldt"],
        )
        .drop_nulls("bc")
        .filter(pl.col("bc").is_not_nan())
        .sample(500, seed=1)
    )
    assert ((j["bc"] / j["dlyprc"] - 1).abs() <= 1e-4).all(), "CRSP vs BMLL closes agree within 1 bp"
    needs = dsf.filter(pl.col("permno").is_in(sm["permno"].unique().to_list())).select(
        "permno", pl.col("dlycaldt").alias("date")
    )
    assert validate_mapping(sm, needs).height == 0, "exactly one mapping per (permno, date)"


def test_lookahead_guard(md) -> None:  # type: ignore[no-untyped-def]
    from simp.errors import LookAheadError

    md.set_horizon(10)
    try:
        with pytest.raises(LookAheadError):
            md.guard(11)
        md.guard(10)
    finally:
        md.set_horizon(None)


def test_t3_is_lab_only(md, make_cfg) -> None:  # type: ignore[no-untyped-def]
    from simp.errors import ExecutionModelUnavailable

    with pytest.raises(ExecutionModelUnavailable, match="Data Lab"):
        Engine(make_cfg(execution_model="T3"), md, "01T3")


def test_t2_runs_and_caps_interval_volume(md, make_cfg) -> None:  # type: ignore[no-untyped-def]
    out = Engine(make_cfg(execution_model="T2"), md, "01T2").run()
    t2 = [f for f in out.fills if f.fill_model == "T2_REPLAY"]
    assert t2, "algo legs replayed against interval prints"
    for f in t2[:500]:
        assert f.quantity <= f.ref_snapshot["cap"] + 1e-9
