"""Ledger property tests (Hypothesis): for random fills, splits, dividends, flows and fees the
balance sheet reconciles with an independent P&L path, and positions never drift."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from simp.accounting.ledger import Ledger
from simp.domain.ids import IdFactory

N = 4
event = st.one_of(
    st.tuples(
        st.just("fill"), st.integers(0, N - 1), st.integers(-500, 500), st.floats(5, 500), st.floats(0, 20)
    ),
    st.tuples(st.just("div"), st.integers(0, N - 1), st.floats(0.01, 3.0), st.integers(0, 10)),
    st.tuples(st.just("split"), st.integers(0, N - 1), st.sampled_from([2, 3, 4])),
    st.tuples(st.just("flow"), st.floats(-1e5, 1e5)),
    st.tuples(st.just("fee"), st.floats(0, 100)),
    st.tuples(st.just("move"), st.lists(st.floats(-0.1, 0.1), min_size=N, max_size=N)),
    st.tuples(
        st.just("settle"),
    ),
)


@settings(max_examples=150, deadline=None)
@given(st.lists(event, min_size=1, max_size=60))
def test_nav_identity_holds(events: list[tuple]) -> None:  # type: ignore[type-arg]
    led = Ledger("RUN", "F", IdFactory("RUN"), N)
    t0 = datetime(2025, 1, 2, 16, tzinfo=UTC)
    led.post(t0, "CASH", 1e7, "SEED", None)
    price = np.full(N, 100.0)
    pos = np.zeros(N)
    nav = 1e7  # independent P&L path
    day = date(2025, 1, 2)
    for k, e in enumerate(events):
        ts = t0 + timedelta(minutes=k)
        kind = e[0]
        if kind == "fill":
            _, j, q, p, cost = e
            if q == 0:
                continue
            led.trade(ts, day + timedelta(days=2), j, 10000 + j, float(q), float(p), float(cost), f"f{k}")
            pos[j] += q
            nav += q * (price[j] - p) - cost
        elif kind == "div":
            _, j, amt, lag = e
            if pos[j] != 0:
                led.dividend_accrual(ts, day + timedelta(days=lag), j, 10000 + j, pos[j] * amt, f"d{k}")
                drop = min(amt, price[j] * 0.5)  # ex-date price drop
                nav += pos[j] * amt - pos[j] * drop
                price[j] -= drop
        elif kind == "split":
            _, j, f = e
            if pos[j] != 0:
                led.split(ts, j, 10000 + j, pos[j] * (f - 1), f"s{k}")
                pos[j] *= f
            price[j] /= f
        elif kind == "flow":
            led.flow(ts, day + timedelta(days=1), float(e[1]), f"fl{k}")
            nav += e[1]
        elif kind == "fee":
            led.fee(ts, float(e[1]), f"fee{k}")
            nav -= e[1]
        elif kind == "move":
            new = price * (1 + np.array(e[1]))
            nav += float(np.dot(pos, new - price))
            price = new
        elif kind == "settle":
            day += timedelta(days=1)
            led.settle(ts, day)
        led.check_identity(day, price, nav, units=0.0, unit_nav=0.0, engine_qty=pos)
    assert np.array_equal(led.qty, pos)
    # cash only moves through settlement / fees / seed: receivables + payables explain the rest
    assert abs(led.cash + led.recv + led.pay + float(np.dot(led.qty, price)) - led.nav(price)) < 1e-6
