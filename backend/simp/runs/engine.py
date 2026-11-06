"""Deterministic session-by-session engine (spec: The session loop).

Per session, for the run's fund:
1. Pre-open      corporate actions effective today (splits rescale positions and open orders;
                 dividends accrue on the ex-date); settle trades / flows / dividends due today.
2. Decisions     morning strategy hook (flows, generator) reading data <= prior close.
3. Desk          netting, cash-shortfall scaling, policy table -> trader orders / programs.
4-5. Execution   continuous session then closing auction, via the run's execution model.
6. Post-close    mark at the official close, management fee, investor flows at pre-flow unit
                 NAV, NAV identity check (ledger vs P&L path). Any break stops the run.
7. Evening       strategy decisions for later sessions (announcements, off-cycle, CA exits).
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from typing import Any

import numpy as np
import structlog

from simp.accounting.calendar import close_ts, ny_ts
from simp.accounting.flows import FlowProcess
from simp.accounting.ledger import Ledger
from simp.config.run_config import RunConfig, check_release
from simp.data.market import MarketData
from simp.desk.policy import DeskPolicy, OrderContext, apply_spill_cap, load_policy, plan_order
from simp.desk.reference import BROKERS
from simp.domain.enums import FundType, Reason, Strategy, TraderStatus, side_sign
from simp.domain.ids import IdFactory
from simp.domain.records import (
    ChildOrder,
    Decision,
    Fill,
    Flow,
    NavRow,
    PmOrder,
    Program,
    TargetWeight,
    TraderOrder,
)
from simp.errors import CashShortfallError, CorporateActionError, MissingPriceError, OrderInvariantError
from simp.execution.intraday import IntradayService, minutes_from_open
from simp.execution.models import Executor
from simp.rng import component_rng
from simp.strategies.index_replication import IndexReplication
from simp.strategies.synthetic_generator import SyntheticGenerator

log = structlog.get_logger(__name__)
ProgressFn = Callable[[dict[str, Any]], None]


@dataclass
class EngineOutput:
    decisions: list[Decision]
    target_weights: list[TargetWeight]
    pm_orders: list[PmOrder]
    trader_orders: list[TraderOrder]
    programs: list[Program]
    children: list[ChildOrder]
    fills: list[Fill]
    flows: list[Flow]
    nav: list[NavRow]
    positions: list[tuple[str, date, int, float, float, float, float]]
    ledger: list[Any]
    netting: list[dict[str, Any]]
    ca_events: list[dict[str, Any]]
    excluded_weight: dict[date, float]
    flags: dict[str, int] = field(default_factory=dict)


class Engine:
    def __init__(
        self,
        cfg: RunConfig,
        md: MarketData,
        run_id: str,
        *,
        progress: ProgressFn | None = None,
        ca_reviews: dict[tuple[int, str, str], str] | None = None,
        policy: DeskPolicy | None = None,
    ) -> None:
        self.cfg, self.md, self.run_id = cfg, md, run_id
        cal = md.cal
        check_release(cfg.window, md.sessions[0], md.sessions[-1])
        self.i0 = cal.index(cal.on_or_after(cfg.window.bootstrap))
        self.i_start = cal.index(cal.on_or_after(cfg.window.start))
        self.i1 = cal.index(cal.on_or_before(cfg.window.end))
        self.ids = IdFactory(run_id)
        self.ledger = Ledger(run_id, cfg.fund_id, self.ids, md.N)
        self.pos = np.zeros(md.N)
        self.nav = cfg.initial_aum
        self.units = cfg.initial_aum / cfg.initial_unit_nav
        self.unit_nav = cfg.initial_unit_nav
        self.policy = policy or load_policy(cfg.desk_policy)
        self.intra = IntradayService(md)
        self.executor = Executor(md, self.intra, cfg.execution_model, cfg.cost, self.ids, run_id, cfg.seed)
        self.fund_type = FundType.SYNTHETIC if cfg.strategy == "synthetic_generator" else FundType.INDEX
        self.strategy = SyntheticGenerator() if self.fund_type == FundType.SYNTHETIC else IndexReplication()
        self.flowproc = FlowProcess(cfg.flows, cfg.seed)
        self.progress = progress
        self.ca_reviews = ca_reviews or {}
        self.pending_pm: dict[int, list[PmOrder]] = defaultdict(list)
        self.today_flow = 0.0
        self.split_today = np.ones(md.N)  # share factor of splits going ex in the current session
        # outputs
        self.decisions: list[Decision] = []
        self.target_weights: list[TargetWeight] = []
        self.pm_orders: list[PmOrder] = []
        self.trader_orders: list[TraderOrder] = []
        self.programs: list[Program] = []
        self.children: list[ChildOrder] = []
        self.fills: list[Fill] = []
        self.flows: list[Flow] = []
        self.nav_rows: list[NavRow] = []
        self.positions: list[tuple[str, date, int, float, float, float, float]] = []
        self.netting: list[dict[str, Any]] = []
        self.ca_events: list[dict[str, Any]] = []
        self.excluded_weight: dict[date, float] = {}
        self.flags: dict[str, int] = defaultdict(int)
        self.strategy.bind(self)
        self.event_days: set[int] = getattr(self.strategy, "event_days", set())

    # ------------------------------------------------------------------ helpers
    def projected_holdings(self, exec_i: int) -> np.ndarray:
        h = self.pos.copy()
        for xi, orders in self.pending_pm.items():
            if xi <= exec_i:
                for o in orders:
                    h[self.md.j(o.permno)] += side_sign(o.side) * o.quantity * o.quantity_adjustment_factor
        return h

    def prior_close(self, i: int) -> np.ndarray:
        """Session i-1 close in session i share units. Positions and open orders are rescaled
        pre-open on a split's ex-date, so anything sized against them must use this, not the raw
        CRSP close (e.g. GOOGL 20:1 on 2022-07-18 would otherwise be sized at 20x its price)."""
        out: np.ndarray = self.md.prc[i - 1] / self.split_today
        return out

    def _schedule(self, orders: list[PmOrder]) -> None:
        for o in orders:
            if o.decision_ts > o.deadline_ts:
                raise OrderInvariantError(f"PM order {o.pm_order_id} decided after its deadline")
            self.pending_pm[self.md.cal.index(o.exec_date)].append(o)
            self.pm_orders.append(o)

    def expected_auction(self, i: int, j: int) -> float:
        md = self.md
        base = float(md.auc_med20[i, j])
        if not math.isfinite(base) or base <= 0:
            base = float(np.nan_to_num(md.adv20[i, j], nan=0.0)) * 0.075
        if i in self.event_days:
            prior = [k for k in self.event_days if k < i]
            if prior:
                k = max(prior)
                med = float(md.auc_med20[k, j])
                if math.isfinite(med) and med > 0 and math.isfinite(float(md.auc_vol[k, j])):
                    base *= max(1.0, float(md.auc_vol[k, j]) / med)
            else:
                base *= 2.5
        return base

    # ------------------------------------------------------------------ phases
    def _pre_open(self, i: int) -> None:
        md, day = self.md, self.md.sessions[i]
        ts = ny_ts(day, time(8, 0))
        todays = [a for a in md.actions if a.ex_i == i]
        kinds: dict[int, set[str]] = defaultdict(set)
        for a in todays:
            kinds[a.j].add(a.kind)
        for a in todays:
            j = a.j
            held = self.pos[j]
            if held != 0 and "FS" in kinds[j] and kinds[j] & {"CD", "SO", "RI"}:
                raise CorporateActionError(
                    f"same-day split and distribution for permno {a.permno}", session=day
                )
            if a.kind == "FS":
                for orders in self.pending_pm.values():
                    for o in orders:
                        if o.permno == a.permno:
                            o.quantity_adjustment_factor *= a.factor
                if held != 0:
                    new = held * a.factor
                    ref = f"FS:{a.permno}:{a.ex}"
                    # a fractional share (3-for-2, reverse splits) is paid out as cash in lieu at
                    # the split-adjusted prior close; the position keeps the whole shares
                    if abs(new - round(new)) <= 1e-9:
                        whole = float(round(new))
                    else:
                        whole = float(math.floor(new) if new > 0 else math.ceil(new))
                    cil = 0.0
                    if whole != new:
                        px = float(md.prc[i - 1, j]) / a.factor
                        if not math.isfinite(px):
                            raise CorporateActionError(
                                f"fractional split share for permno {a.permno} ({held} x {a.factor}) and no "
                                "prior close to pay cash in lieu",
                                session=day,
                            )
                        cil = (new - whole) * px
                        self.ledger.cash_in_lieu(ts, a.permno, cil, ref)
                        self._income += cil
                        self.flags["CASH_IN_LIEU"] += 1
                    self.ledger.split(ts, j, a.permno, whole - held, ref)
                    self.pos[j] = whole
                    self.ca_events.append(
                        dict(
                            date=day,
                            permno=a.permno,
                            kind="FS",
                            shares=held,
                            amount=cil,
                            factor=a.factor,
                            booked=True,
                        )
                    )
            elif a.kind == "CD" and held != 0:
                pay = a.pay
                booked = True
                if pay is None:
                    if self.cfg.provisional_dividends:
                        pay = a.ex + timedelta(days=21)
                        self.flags["PROVISIONAL_DIVIDEND"] += 1
                    else:
                        booked = False
                amt = held * a.amount
                if booked and pay is not None:
                    self.ledger.dividend_accrual(ts, pay, j, a.permno, amt, f"CD:{a.permno}:{a.ex}")
                    self._income += amt
                self.ca_events.append(
                    dict(
                        date=day,
                        permno=a.permno,
                        kind="CD",
                        shares=held,
                        amount=amt,
                        factor=1.0,
                        booked=booked,
                        provisional=a.provisional,
                    )
                )
            elif a.kind in ("SO", "RI") and held != 0:
                status = self.ca_reviews.get((a.permno, a.ex.isoformat(), a.kind), "PENDING")
                if self.cfg.exclusion_policy == "keep_securities" and status == "APPROVED":
                    amt = held * a.amount
                    self.ledger.dividend_accrual(
                        ts,
                        a.pay or a.ex + timedelta(days=10),
                        j,
                        a.permno,
                        amt,
                        f"{a.kind}:{a.permno}:{a.ex}",
                    )
                    self._income += amt
                    self.ca_events.append(
                        dict(
                            date=day,
                            permno=a.permno,
                            kind=a.kind,
                            shares=held,
                            amount=amt,
                            factor=1.0,
                            booked=True,
                        )
                    )
                else:
                    raise CorporateActionError(
                        f"unsupported corporate action {a.kind} ({status}) on held permno {a.permno}; exclusion policy "
                        f"{self.cfg.exclusion_policy} does not omit it",
                        session=day,
                    )
            elif a.kind == "UNK" and held != 0:
                raise CorporateActionError(
                    f"unclassified distribution on held permno {a.permno} (ex {a.ex}): add its CRSP distype code "
                    "to DISTYPE_MAP in simp/data/market.py",
                    session=day,
                )
            elif a.kind == "MG" and held != 0:
                raise CorporateActionError(
                    f"merger/delisting of held permno {a.permno}: no merger payoff is invented", session=day
                )
        self.ledger.settle(ny_ts(day, time(9, 0)), day)

    def _net(self, i: int, orders: list[PmOrder], remaining: dict[str, float]) -> list[TraderOrder]:
        """Net opposite-side PM orders on the same security (flows vs rebalance) by crossing
        them internally at the close; reports the market notional saved."""
        md = self.md
        day = md.sessions[i]
        out: list[TraderOrder] = []
        by: dict[int, dict[str, list[PmOrder]]] = defaultdict(lambda: defaultdict(list))
        for o in orders:
            by[o.permno][o.side].append(o)
        for permno, sides in by.items():
            if not sides.get("BUY") or not sides.get("SELL"):
                continue
            bq = sum(remaining[o.pm_order_id] for o in sides["BUY"])
            sq = sum(remaining[o.pm_order_id] for o in sides["SELL"])
            cross = min(bq, sq)
            if cross <= 0:
                continue
            for side in ("BUY", "SELL"):
                left = cross
                for o in sides[side]:
                    q = min(left, remaining[o.pm_order_id])
                    if q <= 0:
                        continue
                    out.append(
                        self._trader(
                            o,
                            i,
                            str(Strategy.INTERNAL_CROSS),
                            "INTERNAL",
                            q,
                            (time(15, 50), time(16, 0)),
                            {"netting": True},
                            None,
                        )
                    )
                    remaining[o.pm_order_id] -= q
                    left -= q
            px = float(self.prior_close(i)[md.j(permno)])
            self.netting.append(
                dict(
                    date=day,
                    permno=permno,
                    crossed_shares=cross,
                    saved_notional=2 * cross * px,
                    reasons=sorted({o.reason for s in sides.values() for o in s}),
                )
            )
        return out

    def _trader(
        self,
        o: PmOrder,
        i: int,
        strategy: str,
        broker: str,
        qty: float,
        window: tuple[time, time],
        params: dict[str, Any],
        program_id: str | None,
    ) -> TraderOrder:
        md = self.md
        day = md.sessions[i]
        start, end = ny_ts(day, window[0]), ny_ts(day, window[1])
        start = max(start, o.decision_ts)
        j = md.j(o.permno)
        arrival = self.intra.price_at(i, j, minutes_from_open(window[0]))
        if not math.isfinite(arrival):  # the name did not trade today (halted / delisted)
            arrival = float(self.prior_close(i)[j]) if i > 0 else float("nan")
            if not math.isfinite(arrival):
                arrival = o.decision_price
        b = BROKERS[broker]
        comm = (
            b.portfolio_fee_bps
            if strategy == Strategy.PORTFOLIO_TRADE and b.portfolio_fee_bps is not None
            else b.commission_bps
        )
        if o.answer_key is not None:
            params = {**params, "answer_key": o.answer_key}
        t = TraderOrder(
            self.ids.new(start),
            o.pm_order_id,
            self.run_id,
            self.policy.desk_id,
            strategy,
            program_id,
            broker,
            float(qty),
            start,
            end,
            round(arrival, 4),
            params,
            str(TraderStatus.NEW),
            float(comm),
            permno=o.permno,
            side=o.side,
        )
        self.trader_orders.append(t)
        return t

    def _desk(self, i: int, orders: list[PmOrder]) -> list[TraderOrder]:
        md = self.md
        day = md.sessions[i]
        rng = component_rng(self.cfg.seed, "desk", day)
        remaining = {o.pm_order_id: o.quantity * o.quantity_adjustment_factor for o in orders}
        for o in orders:
            o.status = "RELEASED"
        trader: list[TraderOrder] = []
        if self.cfg.flows.netting and self.fund_type != FundType.SYNTHETIC:
            trader += self._net(i, orders, remaining)
        # cash-shortfall scaling: sells before buys; buys scale down rather than borrow
        if self.fund_type != FundType.SYNTHETIC:
            px = np.nan_to_num(self.prior_close(i))
            sig = np.nan_to_num(md.vol_intraday[i - 1], nan=0.015)
            bn = [
                (remaining[o.pm_order_id] * px[md.j(o.permno)], sig[md.j(o.permno)])
                for o in orders
                if o.side == "BUY"
            ]
            tot = sum(n for n, _ in bn)
            # orders are sized at the prior close but fill at today's close: haircut by 2.5 sigma of the basket
            m = min(0.05, 2.5 * sum(n * s_ for n, s_ in bn) / tot) if tot else 0.0
            buys = tot * (1 + m)
            sells = sum(remaining[o.pm_order_id] * px[md.j(o.permno)] for o in orders if o.side == "SELL") * (
                1 - m
            )
            avail = (
                self.ledger.cash
                + self.ledger.recv
                + self.ledger.pay
                + sells
                + max(self.today_flow, 0.0)
                + min(self.today_flow, 0.0)
            )
            if buys > 0 and avail < buys:
                k = max(avail, 0.0) / buys
                for o in orders:
                    if o.side == "BUY":
                        remaining[o.pm_order_id] = math.floor(remaining[o.pm_order_id] * k)
                        o.flags.append("CASH_SCALED")
                self.flags["CASH_SCALED_DAYS"] += 1
        # basket detection for portfolio trades
        small: dict[str, list[PmOrder]] = defaultdict(list)
        prior = np.nan_to_num(self.prior_close(i))
        for o in orders:
            n = remaining[o.pm_order_id] * float(prior[md.j(o.permno)])
            if (
                o.instruction == "TARGET_CLOSE"
                and n <= 25_000
                and o.reason not in (Reason.SUBSCRIPTION, Reason.REDEMPTION, Reason.CASH_SWEEP)
            ):
                small[o.decision_id].append(o)
        programs: dict[str, Program] = {}
        for o in orders:
            q = remaining[o.pm_order_id]
            if q <= 0:
                continue
            j = md.j(o.permno)
            pxp = float(prior[j])
            eav = self.expected_auction(i, j)
            bkey = (
                o.decision_id
                if len(small.get(o.decision_id, [])) >= 2 and o in small[o.decision_id]
                else None
            )
            ctx = OrderContext(
                o.reason, o.instruction, q, q * pxp, float(np.nan_to_num(md.adv20[i, j])), eav, bkey
            )
            rule, legs = plan_order(self.policy, ctx, rng)
            legs = apply_spill_cap(self.policy, legs, eav)
            for leg in legs:
                pid = None
                if leg.program_key:
                    if leg.program_key not in programs:
                        b = BROKERS[leg.broker_id]
                        programs[leg.program_key] = Program(
                            self.ids.new(close_ts(day) - timedelta(minutes=20)),
                            self.run_id,
                            leg.broker_id,
                            day,
                            0,
                            0.0,
                            float(b.portfolio_fee_bps or 0.0),
                        )
                    pg = programs[leg.program_key]
                    pg.n_orders += 1
                    pg.notional += leg.quantity * pxp
                    pid = pg.program_id
                trader.append(
                    self._trader(
                        o,
                        i,
                        leg.strategy,
                        leg.broker_id,
                        leg.quantity,
                        leg.window,
                        {**leg.params, "rule": rule},
                        pid,
                    )
                )
                o.released_qty += leg.quantity
            o.status = "WORKING"
        self.programs += programs.values()
        # invariant: sum(trader qty) <= PM qty (execution basis)
        per_pm: dict[str, float] = defaultdict(float)
        for t in trader:
            per_pm[t.pm_order_id] += t.quantity
        for o in orders:
            if per_pm[o.pm_order_id] > o.quantity * o.quantity_adjustment_factor + 1e-6:
                raise OrderInvariantError(f"trader quantity overruns PM order {o.pm_order_id}", session=day)
        return trader

    def _post_fills(self, i: int, fills: list[Fill], pm_by_id: dict[str, PmOrder]) -> float:
        md = self.md
        day = md.sessions[i]
        settle = md.cal.settlement_date(day)
        pnl = 0.0
        costs_total = 0.0
        # sells before buys
        for f in sorted(fills, key=lambda f: (f.side != "SELL", f.exec_ts, f.fill_id)):
            j = md.j(f.permno)
            q = side_sign(f.side) * f.quantity
            costs = f.fees_usd + f.commission_usd
            self.ledger.trade(f.exec_ts, settle, j, f.permno, q, f.price, costs, f.fill_id)
            self.pos[j] += q
            pnl += q * (float(md.prc[i, j]) - f.price) - costs
            costs_total += costs
            pm = pm_by_id[f.pm_order_id]
            pm.filled_qty += f.quantity / pm.quantity_adjustment_factor
        self._trade_costs = costs_total
        return pnl

    def _settle_delisted(self, i: int) -> float:
        """Close held positions in securities CRSP has delisted and stopped pricing (e.g. SVB and
        Signature Bank, March 2023): the shares leave at the last CRSP close x (1 + delisting
        return, when CRSP has one), paid as cash. A missing price on a name that is not delisted
        is still a hard stop (MissingPriceError). Returns the proceeds for the P&L path."""
        md = self.md
        day = md.sessions[i]
        proceeds = 0.0
        for j in np.where((self.pos != 0) & ~np.isfinite(md.prc[i]))[0]:
            permno = int(md.permnos[j])
            dl = md.delisted.get(permno)
            if dl is None or dl[0] > day:
                continue
            hist = md.prc[: i + 1, j]
            last = np.where(np.isfinite(hist) & (hist > 0))[0]
            if not len(last):
                continue
            px = float(hist[last[-1]]) * (1.0 + (dl[1] or 0.0))
            qty = float(self.pos[j])
            self.ledger.delisting(close_ts(day), int(j), permno, qty, px, f"DL:{permno}:{dl[0]}")
            self.pos[j] = 0.0
            proceeds += qty * px
            self.flags["DELISTING_SETTLED"] += 1
            self.ca_events.append(
                dict(
                    date=day,
                    permno=permno,
                    kind="DL",
                    shares=qty,
                    amount=qty * px,
                    factor=1.0,
                    booked=True,
                    provisional=dl[1] is None,
                )
            )
        return proceeds

    # ------------------------------------------------------------------ main loop
    def run(self) -> EngineOutput:
        md, cfg = self.md, self.cfg
        prev_close = np.where(np.isfinite(md.prc[self.i0]), md.prc[self.i0], 0.0)
        for i in range(self.i0, self.i1 + 1):
            day = md.sessions[i]
            self._income = 0.0
            self._trade_costs = 0.0
            structlog.contextvars.bind_contextvars(session=str(day))
            if i == self.i0:
                ts = ny_ts(day, time(8, 0))
                self.ledger.post(ts, "CASH", cfg.initial_aum, "SEED_CAPITAL", None)
                self.ledger.post(ts, "CAPITAL", cfg.initial_aum, "SEED_CAPITAL", None)
            nav_prev = self.nav
            # 1. pre-open
            if i > self.i0:
                split = np.ones(md.N)
                for a in md.actions:
                    if a.ex_i == i and a.kind == "FS":
                        split[a.j] = a.factor
                pos_before = self.pos.copy()
                self.split_today = split
                self._pre_open(i)
                pos_open = self.pos.copy()  # after splits (whole shares; fractions paid in cash)
            else:
                split = np.ones(md.N)
                pos_before = self.pos.copy()
                self.split_today = split
                pos_open = self.pos.copy()
            # 2. morning decisions (flows drawn from yesterday's AUM)
            self.today_flow = 0.0
            is_jump = False
            if i > self.i0 and self.fund_type != FundType.SYNTHETIC:
                self.today_flow, is_jump = self.flowproc.draw(day, nav_prev)
            if i > self.i0:
                md.set_horizon(i - 1)
                self._schedule(self.strategy.morning(i))
                md.set_horizon(None)
            # 3. desk
            todays = self.pending_pm.pop(i, [])
            pm_by_id = {o.pm_order_id: o for o in todays}
            traders = self._desk(i, todays) if todays else []
            # 4-5. execution
            res = self.executor.execute(i, traders)
            self.children += res.children
            self.fills += res.fills
            trade_pnl = self._post_fills(i, res.fills, pm_by_id)
            for o in todays:
                o.status = (
                    "DONE"
                    if o.filled_qty >= o.quantity - 1e-6
                    else ("DONE" if o.filled_qty > 0 else "CANCELLED")
                )
            # 6. post-close
            close = md.prc[i]
            delist_proceeds = self._settle_delisted(i)
            held = self.pos != 0
            if np.any(held & ~np.isfinite(close)):
                bad = [int(md.permnos[j]) for j in np.where(held & ~np.isfinite(close))[0]]
                raise MissingPriceError(f"missing CRSP close for held permnos {bad}", session=day)
            c = np.where(np.isfinite(close), close, 0.0)
            mtm = float(np.dot(pos_open, c) - np.dot(pos_before, prev_close)) if i > self.i0 else 0.0
            nav_pre_fee = nav_prev + mtm + self._income + trade_pnl + delist_proceeds
            fee = nav_pre_fee * cfg.fee_bps / 1e4 / 252 if i > self.i0 else 0.0
            if fee:
                self.ledger.fee(close_ts(day), fee, f"FEE:{day}")
            nav_pre_flow = nav_pre_fee - fee
            unit_nav_pre = nav_pre_flow / self.units
            gross_ret = (nav_pre_fee / self.units) / self.unit_nav - 1 if i > self.i0 else 0.0
            flow = self.today_flow
            if flow:
                units = flow / unit_nav_pre
                fl = Flow(
                    self.ids.new(close_ts(day)),
                    self.run_id,
                    cfg.fund_id,
                    day,
                    flow,
                    units,
                    unit_nav_pre,
                    cfg.seed,
                    is_jump,
                )
                self.flows.append(fl)
                self.ledger.flow(close_ts(day), md.cal.settlement_date(day), flow, fl.flow_id)
                self.units += units
            nav = nav_pre_flow + flow
            self.ledger.check_identity(day, c, nav, self.units, unit_nav_pre, self.pos)
            liquid = self.ledger.cash + self.ledger.recv + self.ledger.pay
            if self.fund_type != FundType.SYNTHETIC and liquid < -1e-4 * nav:
                raise CashShortfallError(
                    f"net liquid cash {liquid:,.2f} is negative (the fund would be borrowing)", session=day
                )
            equity = float(np.dot(self.pos, c))
            fund_ret = unit_nav_pre / self.unit_nav - 1 if i > self.i0 else 0.0
            self.nav_rows.append(
                NavRow(
                    self.run_id,
                    cfg.fund_id,
                    day,
                    self.ledger.cash,
                    equity,
                    self.ledger.recv,
                    self.ledger.pay,
                    nav,
                    self.units,
                    unit_nav_pre,
                    fund_ret,
                    gross_ret,
                    flow,
                    float(md.vwretd[i]) if i > self.i0 else 0.0,
                    float(md.vwretx[i]) if i > self.i0 else 0.0,
                    (float(md.sp_tr[i]) if math.isfinite(float(md.sp_tr[i])) else None)
                    if i > self.i0
                    else 0.0,
                    (nav - equity) / nav if nav else 0.0,
                    fee,
                    self._trade_costs,
                )
            )
            for j in np.where(self.pos != 0)[0]:
                self.positions.append(
                    (
                        self.run_id,
                        day,
                        int(md.permnos[j]),
                        float(self.pos[j]),
                        float(c[j]),
                        float(self.pos[j] * c[j]),
                        float(split[j]),
                    )
                )
            self.nav, self.unit_nav = nav, unit_nav_pre
            prev_close = c.copy()
            # 7. evening decisions
            md.set_horizon(i)
            if i == self.i0:
                self._schedule(self.strategy.initial(i))
            else:
                self._schedule(self.strategy.evening(i))
            md.set_horizon(None)
            if self.progress and (i == self.i1 or (i - self.i0) % 5 == 0):
                self.progress(
                    dict(
                        session=str(day),
                        i=i - self.i0,
                        n=self.i1 - self.i0 + 1,
                        nav=nav,
                        unit_nav=unit_nav_pre,
                        orders=len(self.pm_orders),
                        fills=len(self.fills),
                    )
                )
        # leftover PM orders whose execution date is beyond the window are cancelled explicitly
        for orders in self.pending_pm.values():
            for o in orders:
                o.status = "CANCELLED"
                o.flags.append("BEYOND_WINDOW")
        structlog.contextvars.unbind_contextvars("session")
        return EngineOutput(
            self.decisions,
            self.target_weights,
            self.pm_orders,
            self.trader_orders,
            self.programs,
            self.children,
            self.fills,
            self.flows,
            self.nav_rows,
            self.positions,
            self.ledger.entries,
            self.netting,
            self.ca_events,
            self.excluded_weight,
            dict(self.flags),
        )
