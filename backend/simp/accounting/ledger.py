"""Fund ledger. Positions change only through fills, flows and corporate actions.

Balance-sheet accounts: CASH, SECURITY (quantity per permno), RECEIVABLE (+), PAYABLE (-).
NAV = CASH + sum(qty x close) + RECEIVABLE + PAYABLE. FEE / CAPITAL / INCOME are memo accounts.

Each fill posts SECURITY (qty, cost) and PAYABLE / RECEIVABLE (notional +- explicit costs);
cash moves on the settlement date in a separate entry (T+2 until 2024-05-27, T+1 after).
The ledger keeps its own running balances, independent of the engine's state, so the daily
NAV identity check compares two bookkeeping paths.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np

from simp.domain.enums import Account
from simp.domain.ids import IdFactory
from simp.domain.records import LedgerEntry
from simp.errors import NavIdentityError


@dataclass
class Pending:
    settle: date
    account: str  # RECEIVABLE or PAYABLE
    amount: float  # signed (receivable > 0, payable < 0)
    ref_type: str
    ref_id: str | None
    permno: int | None = None


@dataclass
class Ledger:
    run_id: str
    fund_id: str
    ids: IdFactory
    n_sec: int
    entries: list[LedgerEntry] = field(default_factory=list)
    cash: float = 0.0
    recv: float = 0.0
    pay: float = 0.0
    qty: np.ndarray = field(default_factory=lambda: np.zeros(0))
    pending: list[Pending] = field(default_factory=list)
    memo: dict[str, float] = field(default_factory=lambda: defaultdict(float))

    def __post_init__(self) -> None:
        self.qty = np.zeros(self.n_sec)

    def post(self, ts: datetime, account: str, amount: float, ref_type: str, ref_id: str | None,
             permno: int | None = None, quantity: float = 0.0, j: int | None = None) -> None:
        self.entries.append(LedgerEntry(self.ids.new(ts), self.run_id, self.fund_id, ts, account, permno,
                                        round(amount, 6), quantity, ref_type, ref_id))
        if account == Account.CASH:
            self.cash += amount
        elif account == Account.RECEIVABLE:
            self.recv += amount
        elif account == Account.PAYABLE:
            self.pay += amount
        elif account == Account.SECURITY and j is not None:
            self.qty[j] += quantity
        else:
            self.memo[account] += amount

    # ---------------------------------------------------------------- events
    def trade(self, ts: datetime, settle: date, j: int, permno: int, qty_signed: float, price: float, costs: float, fill_id: str) -> None:
        gross = qty_signed * price
        self.post(ts, Account.SECURITY, gross, "FILL", fill_id, permno, qty_signed, j)
        amt = -(gross + costs)  # buy: payable -(gross+costs); sell: receivable (|gross| - costs)
        acct = Account.PAYABLE if amt < 0 else Account.RECEIVABLE
        self.post(ts, acct, amt, "FILL", fill_id, permno)
        self.post(ts, Account.FEE, costs, "FILL_COSTS", fill_id, permno)
        self.pending.append(Pending(settle, acct, amt, "SETTLE", fill_id, permno))

    def flow(self, ts: datetime, settle: date, amount: float, flow_id: str) -> None:
        acct = Account.RECEIVABLE if amount >= 0 else Account.PAYABLE
        self.post(ts, acct, amount, "FLOW", flow_id)
        self.post(ts, Account.CAPITAL, amount, "FLOW", flow_id)
        self.pending.append(Pending(settle, acct, amount, "FLOW_SETTLE", flow_id))

    def dividend_accrual(self, ts: datetime, pay: date, j: int, permno: int, amount: float, ref: str) -> None:
        self.post(ts, Account.RECEIVABLE, amount, "DIVIDEND", ref, permno)
        self.post(ts, Account.INCOME, amount, "DIVIDEND", ref, permno)
        self.pending.append(Pending(pay, Account.RECEIVABLE, amount, "DIVIDEND_PAY", ref, permno))

    def split(self, ts: datetime, j: int, permno: int, delta_qty: float, ref: str) -> None:
        self.post(ts, Account.SECURITY, 0.0, "SPLIT", ref, permno, delta_qty, j)

    def fee(self, ts: datetime, amount: float, ref: str) -> None:
        self.post(ts, Account.CASH, -amount, "MGMT_FEE", ref)
        self.post(ts, Account.FEE, amount, "MGMT_FEE", ref)

    def settle(self, ts: datetime, today: date) -> None:
        keep = []
        for p in self.pending:
            if p.settle <= today:
                self.post(ts, p.account, -p.amount, p.ref_type, p.ref_id, p.permno)
                self.post(ts, Account.CASH, p.amount, p.ref_type, p.ref_id, p.permno)
            else:
                keep.append(p)
        self.pending = keep

    # ---------------------------------------------------------------- checks
    def nav(self, close: np.ndarray) -> float:
        held = self.qty != 0
        return float(self.cash + np.dot(self.qty[held], np.nan_to_num(close[held])) + self.recv + self.pay)

    def check_identity(self, day: date, close: np.ndarray, engine_nav: float, units: float, unit_nav: float,
                       engine_qty: np.ndarray, tol: float = 0.01) -> None:
        ln = self.nav(close)
        if abs(ln - engine_nav) > tol:
            raise NavIdentityError(f"ledger NAV {ln:,.4f} != engine NAV {engine_nav:,.4f} (diff {ln - engine_nav:,.4f})", session=day)
        if not np.array_equal(self.qty, engine_qty):
            bad = int(np.argmax(self.qty != engine_qty))
            raise NavIdentityError(f"position break at security index {bad}: ledger {self.qty[bad]} engine {engine_qty[bad]}", session=day)
        if units > 0 and abs(units * unit_nav - engine_nav) > max(tol, abs(engine_nav) * 1e-12):
            raise NavIdentityError(f"units x unit NAV {units * unit_nav:,.4f} != NAV {engine_nav:,.4f}", session=day)
