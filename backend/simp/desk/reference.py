"""Simulated counterparties: brokers, desks and venues (seeded into the DB; editable in Settings)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Broker:
    broker_id: str
    name: str
    commission_bps: float
    specialty: str
    portfolio_fee_bps: float | None = None


@dataclass(frozen=True)
class Venue:
    mic: str
    name: str
    kind: str  # LIT | DARK | INTERNAL
    supports_auction: bool
    fee_remove: float  # USD / share (positive = cost)
    fee_add: float  # negative = rebate
    fee_auction: float
    fee_midpoint: float


BROKERS: dict[str, Broker] = {
    b.broker_id: b
    for b in [
        Broker("BRK_A", "Broker A (algorithmic)", 1.5, "ALGO"),
        Broker("BRK_B", "Broker B (close specialist)", 1.0, "AUCTION"),
        Broker("BRK_C", "Broker C (portfolio trading)", 0.0, "PORTFOLIO", portfolio_fee_bps=2.0),
        Broker("BRK_D", "Broker D (high touch)", 3.0, "HIGH_TOUCH"),
        Broker("BRK_E", "Broker E (algorithmic, low cost)", 1.0, "ALGO"),
        Broker("INTERNAL", "Internal cross", 0.0, "CROSS"),
    ]
}

VENUES: dict[str, Venue] = {
    v.mic: v
    for v in [
        Venue("XNYS", "NYSE", "LIT", True, 0.0030, -0.0020, 0.0010, 0.0010),
        Venue("XNAS", "Nasdaq", "LIT", True, 0.0030, -0.0021, 0.0010, 0.0010),
        Venue("ARCX", "NYSE Arca", "LIT", True, 0.0030, -0.0020, 0.0010, 0.0010),
        Venue("BATS", "Cboe BZX", "LIT", True, 0.0030, -0.0020, 0.0010, 0.0010),
        Venue("EDGX", "Cboe EDGX", "LIT", False, 0.0029, -0.0020, 0.0, 0.0010),
        Venue("IEXG", "IEX", "LIT", False, 0.0009, 0.0, 0.0, 0.0009),
        Venue("MEMX", "MEMX", "LIT", False, 0.0029, -0.0022, 0.0, 0.0010),
        Venue("DARK", "Broker dark pools (ATS)", "DARK", False, 0.0010, 0.0010, 0.0, 0.0010),
        Venue("INTERNAL", "Internal cross", "INTERNAL", False, 0.0, 0.0, 0.0, 0.0),
    ]
}

DESKS = {"DESK_US_EQ": "US equity index desk"}
