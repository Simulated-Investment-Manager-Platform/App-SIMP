"""Enumerations of the domain model (spec: Domain model)."""

from __future__ import annotations

from enum import StrEnum


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


def side_sign(side: Side | str) -> int:
    return 1 if str(side) == Side.BUY else -1


class Reason(StrEnum):
    INITIAL = "INITIAL"
    INDEX_REBALANCE = "INDEX_REBALANCE"
    INDEX_ADD = "INDEX_ADD"
    INDEX_DELETE = "INDEX_DELETE"
    SUBSCRIPTION = "SUBSCRIPTION"
    REDEMPTION = "REDEMPTION"
    CASH_SWEEP = "CASH_SWEEP"
    CORP_ACTION = "CORP_ACTION"
    SYNTHETIC_TEST = "SYNTHETIC_TEST"


class DecisionPriceSource(StrEnum):
    PRIOR_CLOSE = "PRIOR_CLOSE"
    MID = "MID"
    LAST_TRADE = "LAST_TRADE"


class Instruction(StrEnum):
    TARGET_CLOSE = "TARGET_CLOSE"
    VWAP_DAY = "VWAP_DAY"
    ARRIVAL = "ARRIVAL"
    NONE = "NONE"


class Urgency(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class PmStatus(StrEnum):
    NEW = "NEW"
    RELEASED = "RELEASED"
    WORKING = "WORKING"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


class Strategy(StrEnum):
    HIGH_TOUCH = "HIGH_TOUCH"
    ALGO_VWAP = "ALGO_VWAP"
    ALGO_TWAP = "ALGO_TWAP"
    ALGO_POV = "ALGO_POV"
    ALGO_IS = "ALGO_IS"
    MOC = "MOC"
    LOC = "LOC"
    PORTFOLIO_TRADE = "PORTFOLIO_TRADE"
    # Extension: internal crossing of opposite flow and rebalance quantity (netting).
    INTERNAL_CROSS = "INTERNAL_CROSS"


AUCTION_STRATEGIES = {Strategy.MOC, Strategy.LOC, Strategy.PORTFOLIO_TRADE, Strategy.INTERNAL_CROSS}


class TraderStatus(StrEnum):
    NEW = "NEW"
    WORKING = "WORKING"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class OrderType(StrEnum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    MOC = "MOC"
    LOC = "LOC"
    MIDPOINT_PEG = "MIDPOINT_PEG"
    CROSS = "CROSS"


class Tif(StrEnum):
    DAY = "DAY"
    IOC = "IOC"
    AT_CLOSE = "AT_CLOSE"


class ChildStatus(StrEnum):
    NEW = "NEW"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class Liquidity(StrEnum):
    ADD = "ADD"
    REMOVE = "REMOVE"
    AUCTION = "AUCTION"
    MIDPOINT = "MIDPOINT"
    CROSS = "CROSS"


class MarketState(StrEnum):
    """Subset of BMLL MARKET_STATES used by fills."""

    PRE_OPEN = "PRE_OPEN"
    OPENING_AUCTION = "OPENING_AUCTION"
    CONTINUOUS_TRADING = "CONTINUOUS_TRADING"
    CLOSING_AUCTION = "CLOSING_AUCTION"
    POST_CLOSE = "POST_CLOSE"


class Account(StrEnum):
    CASH = "CASH"
    SECURITY = "SECURITY"
    RECEIVABLE = "RECEIVABLE"
    PAYABLE = "PAYABLE"
    FEE = "FEE"
    CAPITAL = "CAPITAL"
    INCOME = "INCOME"


class FundType(StrEnum):
    INDEX = "INDEX"
    ACTIVE = "ACTIVE"
    SYNTHETIC = "SYNTHETIC"


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    ANALYSING = "ANALYSING"
    DONE = "DONE"
    FAILED = "FAILED"


class ExecutionModel(StrEnum):
    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"


class TimingRule(StrEnum):
    HANDOFF = "HANDOFF"  # decide Monday (first member day) close, fill Tuesday close
    EFFECTIVE_DATE = "EFFECTIVE_DATE"  # decide at announcement close, fill effective-date closing auction


class ReviewStatus(StrEnum):
    APPROVED = "APPROVED"
    PENDING = "PENDING"
    UNSUPPORTED = "UNSUPPORTED"
    REJECTED = "REJECTED"


class Role(StrEnum):
    VIEWER = "viewer"
    RESEARCHER = "researcher"
    ADMIN = "admin"


VENUES_LIT = ["XNYS", "XNAS", "ARCX", "BATS", "EDGX", "IEXG", "MEMX"]
VENUE_DARK = "DARK"
VENUE_INTERNAL = "INTERNAL"
PRIMARY_VENUES = ["XNYS", "XNAS", "ARCX", "BATS"]
