"""Regressions from the first live (CRSP CIZ + BMLL Data Lab) end-to-end runs."""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pytest

from simp.accounting.calendar import SessionCalendar, nyse_sessions
from simp.accounting.ledger import Ledger
from simp.data.market import classify_distribution
from simp.domain.ids import IdFactory


@pytest.mark.parametrize(
    ("distype", "amount", "facshr", "kind"),
    [
        ("CD", 0.24, 0.0, "CD"),
        ("SD", 1.50, 0.0, "CD"),  # special cash dividend
        ("ROC", 0.10, 0.0, "CD"),  # return of capital
        ("FRS", None, 19.0, "FS"),  # 20-for-1 (GOOGL 2022-07-18)
        ("FRS", None, 0.5, "FS"),  # 3-for-2
        ("FRS", None, -0.8, "FS"),  # 1-for-5 reverse split
        ("CP", 104.0, -1.0, "MG"),  # cash take-private (Citrix 2022-09-30)
        ("SP", 49.57, -1.0, "MG"),  # stock-for-stock merger
        ("SP", 36.84, 0.0, "SO"),  # spin-off: parent keeps trading
    ],
)
def test_ciz_distribution_codes(distype: str, amount: float | None, facshr: float, kind: str) -> None:
    r = {"distype": distype, "disdivamt": amount, "disfacshr": facshr}
    assert classify_distribution(r) == kind


def test_settlement_after_last_data_session() -> None:
    # a live CRSP pull ending on the window end: T+1 on the last session must still resolve
    cal = SessionCalendar(nyse_sessions(date(2026, 6, 1), date(2026, 6, 30)))
    assert cal.settlement_date(date(2026, 6, 30)) == date(2026, 7, 1)
    assert cal.settlement_date(date(2026, 6, 29)) == date(2026, 6, 30)


def test_derived_bmll_units(md) -> None:  # type: ignore[no-untyped-def]
    # BMLL reports relative spread/dislocation in bps and intraday vol annualised; the engine
    # expects bps and a daily sigma. A unit slip shows up as 10,000x spreads or ~16x vol.
    spr = md.spread_bps[np.isfinite(md.spread_bps)]
    vol = md.vol_intraday[np.isfinite(md.vol_intraday)]
    dis = md.dislocation_bps[np.isfinite(md.dislocation_bps)]
    assert 0.5 < float(np.median(spr)) < 50
    assert 0.003 < float(np.median(vol)) < 0.05
    assert float(np.percentile(np.abs(dis), 99)) < 200


def test_cash_in_lieu_and_delisting_keep_ledger_nav() -> None:
    led = Ledger("01TEST", "F", IdFactory("01TEST"), 1)
    ts = datetime(2023, 3, 14, 16, 0)
    led.post(ts, "CASH", 1_000.0, "SEED_CAPITAL", None)
    led.post(ts, "SECURITY", 0.0, "TEST", None, 1, 101.0, 0)
    # 3-for-2 split of 101 shares: 151 whole shares, 0.5 paid in cash at 20.0
    led.split(ts, 0, 1, 50.0, "FS")
    led.cash_in_lieu(ts, 1, 0.5 * 20.0, "FS")
    assert led.qty[0] == 151.0
    assert led.nav(np.array([20.0])) == pytest.approx(1_000.0 + 151.5 * 20.0)
    # the security stops trading: shares leave at the last price, proceeds arrive as cash
    led.delisting(ts, 0, 1, 151.0, 0.40, "DL")
    assert led.qty[0] == 0.0
    assert led.nav(np.array([np.nan])) == pytest.approx(1_000.0 + 10.0 + 151.0 * 0.40)
