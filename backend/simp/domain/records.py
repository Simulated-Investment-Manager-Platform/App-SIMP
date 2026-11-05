"""In-memory records of the order chain and fund ledger. Field names match the DB tables."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass
class Decision:
    decision_id: str
    run_id: str
    fund_id: str
    decision_type: str
    snapshot_date: date
    effective_date: date
    decision_ts: datetime
    target_weights_ref: str | None = None


@dataclass
class TargetWeight:
    run_id: str
    decision_id: str
    permno: int
    weight: float
    weight_source: str
    excluded_flag: bool
    exclusion_reason: str | None


@dataclass
class PmOrder:
    pm_order_id: str
    run_id: str
    fund_id: str
    decision_id: str
    permno: int
    listing_id: int | None
    side: str
    quantity: float
    reason: str
    decision_ts: datetime
    decision_price: float
    decision_price_source: str
    instruction: str
    deadline_ts: datetime
    urgency: str
    limit_price: float | None
    status: str
    created_by: str
    exec_date: date
    quantity_adjustment_factor: float = 1.0
    answer_key: dict[str, Any] | None = None
    filled_qty: float = 0.0
    released_qty: float = 0.0
    flags: list[str] = field(default_factory=list)


@dataclass
class Program:
    program_id: str
    run_id: str
    broker_id: str
    trade_date: date
    n_orders: int
    notional: float
    fee_bps: float


@dataclass
class TraderOrder:
    trader_order_id: str
    pm_order_id: str
    run_id: str
    desk_id: str
    strategy: str
    program_id: str | None
    broker_id: str
    quantity: float
    start_ts: datetime
    end_ts: datetime
    arrival_price: float
    params: dict[str, Any]
    status: str
    commission_bps: float
    permno: int = 0
    side: str = "BUY"
    filled_qty: float = 0.0


@dataclass
class ChildOrder:
    child_order_id: str
    trader_order_id: str
    run_id: str
    broker_id: str
    venue_mic: str
    order_type: str
    quantity: float
    limit_price: float | None
    sent_ts: datetime
    tif: str
    status: str


@dataclass
class Fill:
    fill_id: str
    child_order_id: str
    trader_order_id: str
    pm_order_id: str
    run_id: str
    broker_id: str
    venue_mic: str
    exec_ts: datetime
    price: float
    quantity: float
    liquidity: str
    market_state: str
    fees_usd: float
    commission_usd: float
    fill_model: str
    ref_snapshot: dict[str, Any]
    permno: int
    side: str


@dataclass
class LedgerEntry:
    entry_id: str
    run_id: str
    fund_id: str
    ts: datetime
    account: str
    permno: int | None
    amount: float
    quantity: float
    ref_type: str
    ref_id: str | None


@dataclass
class Flow:
    flow_id: str
    run_id: str
    fund_id: str
    date: date
    amount_usd: float
    units: float
    unit_nav_used: float
    generator_seed: int
    is_jump: bool


@dataclass
class NavRow:
    run_id: str
    fund_id: str
    date: date
    cash: float
    equity_value: float
    receivables: float
    payables: float
    nav: float
    units: float
    unit_nav: float
    fund_return: float
    gross_return: float
    flow_usd: float
    bench_return_tr: float
    bench_return_pr: float
    bench_return_official: float | None
    cash_weight: float
    fees_usd: float
    trading_cost_usd: float
