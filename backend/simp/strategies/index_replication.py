"""S&P 500 index replication with CRSP cap-weight proxies (spec: Index rebalance timing, flows).

Timing rules
* EFFECTIVE_DATE: weights snapshot + decision at the announcement close (effective - offset
  sessions, default 5); execution in the effective-date closing auction. Off-cycle adds and
  deletes get their own decision (3 sessions ahead) and trade on the session before
  ``mbrstartdt`` / at ``mbrenddt``.
* HANDOFF: decide at the Monday close (first member day), fill at Tuesday close; off-cycle
  changes are folded into the next quarter.

Membership for an announced change is readable before it takes effect (the announcement is
public); every other read goes through the engine's look-ahead guard.
"""

from __future__ import annotations

import math
from datetime import datetime, time
from typing import TYPE_CHECKING

import numpy as np

from simp.accounting.calendar import close_ts, ny_ts, quarterly_effective_dates
from simp.domain.enums import DecisionPriceSource, Instruction, Reason, ReviewStatus, TimingRule, Urgency
from simp.domain.records import Decision, PmOrder, TargetWeight
from simp.errors import MappingError

if TYPE_CHECKING:
    from simp.runs.engine import Engine

UNSUPPORTED = ("SO", "RI", "MG")


class IndexReplication:
    name = "index_replication"
    version = "1.0"

    def bind(self, e: Engine) -> None:
        self.e = e
        md, cfg = e.md, e.cfg
        cal = md.cal
        self.rule = cfg.timing.rule
        off = cfg.timing.decision_offset_sessions
        effs = [d for d in quarterly_effective_dates(cal, md.sessions[0], md.sessions[-1])]
        self.event_days = {cal.index(d) for d in effs}
        self.quarterly: dict[int, tuple[int, int]] = {}  # decision_i -> (exec_i, membership_i)
        for d in effs:
            ei = cal.index(d)
            if self.rule == TimingRule.EFFECTIVE_DATE:
                di, xi, mi = ei - off, ei, ei + 1
            else:
                di, xi, mi = ei + 1, ei + 2, ei + 1
            if e.i0 <= di and xi <= e.i1 and mi < md.D:
                self.quarterly[di] = (xi, mi)
        self.exec_quarterly = {xi: di for di, (xi, _) in self.quarterly.items()}
        # off-cycle membership changes (not on quarterly boundaries)
        self.offcycle: dict[int, list[tuple[int, str, int]]] = {}
        if self.rule == TimingRule.EFFECTIVE_DATE:
            qstart = {cal.index(d) + 1 for d in effs if cal.index(d) + 1 < md.D}
            qend = {cal.index(d) for d in effs}
            for permno, ivs in md.mbr_start.items():
                j = md.j(permno)
                for d0, d1 in ivs:
                    if cal.is_session(d0) and cal.index(d0) not in qstart and cal.index(d0) > 0:
                        xi = cal.index(d0) - 1
                        self._add_off(xi, j, "ADD")
                    if cal.is_session(d1) and cal.index(d1) not in qend and cal.index(d1) < md.D - 1:
                        self._add_off(cal.index(d1), j, "DELETE")
        self.reviews = e.ca_reviews
        self.events_by_declared: dict[int, list[int]] = {}
        for k, a in enumerate(md.actions):
            if a.kind in UNSUPPORTED and a.declared is not None:
                try:
                    di = cal.index(cal.on_or_after(a.declared))
                except KeyError:
                    continue
                self.events_by_declared.setdefault(di, []).append(k)
        self.current_target: np.ndarray | None = None
        self.target_by_exec: dict[int, np.ndarray] = {}

    def _add_off(self, xi: int, j: int, kind: str) -> None:
        di = max(xi - 3, 0)
        if self.e.i0 <= di and xi <= self.e.i1:
            self.offcycle.setdefault(di, []).append((j, kind, xi))

    # ------------------------------------------------------------------ helpers
    def _review(self, k: int) -> str:
        a = self.e.md.actions[k]
        return self.reviews.get((a.permno, a.ex.isoformat(), a.kind), str(ReviewStatus.PENDING))

    def excluded(self, i: int) -> dict[int, str]:
        """Securities the fund must not hold as of session i, with reasons."""
        md, cfg = self.e.md, self.e.cfg
        out: dict[int, str] = {}
        day = md.sessions[i]
        for k, a in enumerate(md.actions):
            if a.kind not in UNSUPPORTED or a.declared is None:
                continue
            if a.declared <= day < a.ex:
                keepable = a.kind != "MG" and self._review(k) == ReviewStatus.APPROVED
                if cfg.exclusion_policy == "exclude_securities" or not keepable:
                    out[a.j] = f"pending {a.kind} ex {a.ex} ({self._review(k)})"
        for j in np.where(~md.mapped)[0]:
            out.setdefault(int(j), "UNMAPPED to BMLL listing")
        return out

    def _order(
        self,
        decision: Decision,
        j: int,
        qty: float,
        reason: str,
        price: float,
        exec_i: int,
        instruction: str = Instruction.TARGET_CLOSE,
        ts: datetime | None = None,
        source: str = DecisionPriceSource.PRIOR_CLOSE,
    ) -> PmOrder:
        e = self.e
        md = e.md
        ts = ts or decision.decision_ts
        return PmOrder(
            pm_order_id=e.ids.new(ts),
            run_id=e.run_id,
            fund_id=e.cfg.fund_id,
            decision_id=decision.decision_id,
            permno=int(md.permnos[j]),
            listing_id=int(md.listing_id[j]) or None,
            side="BUY" if qty > 0 else "SELL",
            quantity=float(abs(qty)),
            reason=str(reason),
            decision_ts=ts,
            decision_price=float(price),
            decision_price_source=str(source),
            instruction=str(instruction),
            deadline_ts=close_ts(md.sessions[exec_i]),
            urgency=str(
                Urgency.HIGH
                if reason in (Reason.INDEX_ADD, Reason.INDEX_DELETE, Reason.CORP_ACTION)
                else Urgency.MEDIUM
            ),
            limit_price=None,
            status="NEW",
            created_by=f"{self.name}@{self.version}",
            exec_date=md.sessions[exec_i],
        )

    def _decision(self, i: int, kind: str, exec_i: int, ts: datetime | None = None) -> Decision:
        e = self.e
        ts = ts or close_ts(e.md.sessions[i])
        d = Decision(
            e.ids.new(ts), e.run_id, e.cfg.fund_id, kind, e.md.sessions[i], e.md.sessions[exec_i], ts
        )
        e.decisions.append(d)
        return d

    def _basket(
        self, i: int, exec_i: int, member_i: int, kind: str, nav: float, holdings: np.ndarray
    ) -> list[PmOrder]:
        """Target the cap-weighted basket of members at ``member_i`` with snapshot at session i."""
        e = self.e
        md = e.md
        md.guard(i)
        excl = self.excluded(i)
        if e.cfg.unmapped_policy == "stop":
            bad = [int(md.permnos[j]) for j in np.where(md.member[member_i] & ~md.mapped)[0]]
            if bad:
                raise MappingError(f"members without a BMLL mapping: {bad}", session=md.sessions[i])
        mem = md.member[member_i].copy()
        px = md.prc[i]
        cap = np.where(mem, np.nan_to_num(md.shrout[i]) * np.nan_to_num(px), 0.0)
        incl = mem.copy()
        for j in excl:
            incl[j] = False
        w_all = cap / cap.sum()
        w = np.where(incl, cap, 0.0)
        w = w / w.sum()
        d = self._decision(i, kind, exec_i)
        d.target_weights_ref = d.decision_id
        for j in np.where(mem | (holdings != 0))[0]:
            e.target_weights.append(
                TargetWeight(
                    e.run_id,
                    d.decision_id,
                    int(md.permnos[j]),
                    float(w[j]),
                    "CRSP_CAP_PROXY",
                    bool(mem[j] and not incl[j]),
                    excl.get(int(j)) if mem[j] and not incl[j] else (None if mem[j] else "NOT_A_MEMBER"),
                )
            )
        e.excluded_weight[md.sessions[exec_i]] = float(w_all[~incl & mem].sum())
        investable = nav * (1 - e.cfg.flows.cash_buffer_bps / 1e4)
        tgt = np.zeros(md.N)
        ok = incl & np.isfinite(px) & (px > 0)
        tgt[ok] = np.floor(investable * w[ok] / px[ok])
        self.target_by_exec[exec_i] = w
        out = []
        for j in np.where(tgt != holdings)[0]:
            dq = tgt[j] - holdings[j]
            if dq == 0 or not math.isfinite(px[j]):
                continue
            if kind == "INITIAL":
                reason = Reason.INITIAL
            elif holdings[j] == 0 and dq > 0:
                reason = Reason.INDEX_ADD
            elif tgt[j] == 0 and not mem[j]:
                reason = Reason.INDEX_DELETE
            elif tgt[j] == 0 and j in excl:
                reason = Reason.CORP_ACTION
            else:
                reason = Reason.INDEX_REBALANCE
            out.append(self._order(d, int(j), float(dq), reason, float(px[j]), exec_i))
        return out

    # ------------------------------------------------------------------ hooks
    def initial(self, i: int) -> list[PmOrder]:
        e = self.e
        xi = min(i + e.cfg.timing.initial_execution_offset, e.i1)
        return self._basket(i, xi, i, "INITIAL", e.nav, e.pos.copy())

    def evening(self, i: int) -> list[PmOrder]:
        e = self.e
        md = e.md
        out: list[PmOrder] = []
        # corporate-action exits: an unsupported event declared today on a held name
        excl = None
        day = md.sessions[i]
        pending_events = [
            k
            for k, a in enumerate(md.actions)
            if a.kind in UNSUPPORTED and a.declared is not None and a.declared <= day < a.ex
        ]
        for k in pending_events:  # re-checked daily: a partly filled exit is re-issued
            a = md.actions[k]
            excl = excl if excl is not None else self.excluded(i)
            if a.j not in excl or i + 1 > e.i1 or md.sessions[i + 1] >= a.ex:
                continue
            # cancel any not-yet-executed buys decided before the event was declared
            for orders in e.pending_pm.values():
                for o in list(orders):
                    if o.permno == a.permno and o.side == "BUY":
                        orders.remove(o)
                        o.status = "CANCELLED"
                        o.flags.append("EXCLUDED_AFTER_DECISION")
            held = e.projected_holdings(10**9)[a.j]
            if held > 0:
                d = self._decision(i, "CORP_ACTION_EXIT", i + 1)
                out.append(self._order(d, a.j, -held, Reason.CORP_ACTION, float(md.prc[i, a.j]), i + 1))
        # schedule exits now so the same evening's basket sees them in projected holdings
        e._schedule(out)
        out = []
        if i in self.quarterly:
            xi, mi = self.quarterly[i]
            out += self._basket(i, xi, mi, "QUARTERLY_REBALANCE", e.nav, e.projected_holdings(xi))
        for j, kind, xi in self.offcycle.get(i, []):
            md.guard(i)
            px = float(md.prc[i, j])
            if not math.isfinite(px):
                continue
            d = self._decision(i, f"OFFCYCLE_{kind}", xi)
            if kind == "DELETE":
                q = e.projected_holdings(xi)[j]
                if q > 0:
                    out.append(self._order(d, j, -q, Reason.INDEX_DELETE, px, xi))
            elif j not in self.excluded(i):
                mem = md.member[xi + 1] if xi + 1 < md.D else md.member[xi]
                cap = np.where(mem, np.nan_to_num(md.shrout[i]) * np.nan_to_num(md.prc[i]), 0.0)
                w = cap[j] / cap.sum()
                q = math.floor(e.nav * w / px) - e.projected_holdings(xi)[j]
                if q > 0:
                    out.append(self._order(d, j, q, Reason.INDEX_ADD, px, xi))
        return out

    def morning(self, i: int) -> list[PmOrder]:
        """Cash-flow orders: invest net cash above the buffer pro rata to target weights,
        or sell pro rata to fund redemptions. MOC on the flow date."""
        e = self.e
        md, fc = e.md, e.cfg.flows
        flow = e.today_flow
        if not (fc.enabled or fc.cash_sweep):
            return []
        md.guard(i - 1)
        px = md.prc[i - 1]
        nav_prev = e.nav
        pend = e.pending_pm.get(i, [])
        committed = sum(
            (1 if o.side == "BUY" else -1)
            * o.quantity
            * o.quantity_adjustment_factor
            * float(np.nan_to_num(px[md.j(o.permno)]))
            for o in pend
        )
        liquid = e.ledger.cash + e.ledger.recv + e.ledger.pay
        excess = liquid + flow - committed - fc.cash_buffer_bps / 1e4 * (nav_prev + flow)
        min_ticket = max(fc.min_ticket_usd, fc.min_ticket_nav_bps / 1e4 * nav_prev)
        if abs(excess) < min_ticket:
            return []
        if i in self.target_by_exec:
            w = self.target_by_exec[i].copy()
        else:
            hold = e.projected_holdings(i)
            mv = np.where(hold > 0, hold * np.nan_to_num(px), 0.0)
            for j in self.excluded(i - 1):
                mv[j] = 0.0
            w = mv / mv.sum() if mv.sum() > 0 else mv
        for j in self.excluded(i - 1):
            w[j] = 0.0
        if w.sum() <= 0:
            return []
        w = w / w.sum()
        side = 1 if excess > 0 else -1
        amt = abs(excess)
        elig = (w * amt >= min_ticket) & np.isfinite(px) & (px > 0)
        if not elig.any():
            return []
        we = np.where(elig, w, 0.0)
        we /= we.sum()
        ts = ny_ts(md.sessions[i], time(9, 0))
        reason = (
            Reason.SUBSCRIPTION
            if (flow > 0 and side > 0)
            else Reason.REDEMPTION
            if (flow < 0 and side < 0)
            else Reason.CASH_SWEEP
        )
        d = self._decision(i - 1, f"FLOW_{reason}", i, ts=ts)
        out = []
        hold = e.projected_holdings(i)
        for j in np.where(elig)[0]:
            if side > 0:
                q = math.floor(amt * we[j] / px[j])
            else:
                q = min(math.ceil(amt * we[j] / px[j]), int(hold[j]))
            if q > 0:
                out.append(self._order(d, int(j), side * q, reason, float(px[j]), i, ts=ts))
        return out
