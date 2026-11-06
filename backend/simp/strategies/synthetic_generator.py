"""Phase 1 random order generator (spec: Phase 1 order generator).

Orders/day ~ Poisson(lambda); names drawn from the mapped S&P universe weighted by ADV20;
participation Q/ADV20 ~ truncated Pareto(alpha) on [x_min, x_max]; strategy from the desk
policy; venues from the name's execution-venue shares; fill price = VWAP (1 + s c + xi), with
the injected cost c and the noise xi stored as the order's answer key.
"""

from __future__ import annotations

import math
from datetime import time
from typing import TYPE_CHECKING

import numpy as np

from simp.accounting.calendar import close_ts, ny_ts
from simp.domain.enums import DecisionPriceSource, Reason, Urgency
from simp.domain.records import Decision, PmOrder
from simp.rng import component_rng

if TYPE_CHECKING:
    from simp.runs.engine import Engine

DURATION = {"VWAP_DAY": 380 / 390, "ARRIVAL": 120 / 390, "TARGET_CLOSE": 1 / 390}


def truncated_pareto(rng: np.random.Generator, alpha: float, lo: float, hi: float, n: int) -> np.ndarray:
    u = rng.random(n)
    r = (lo / hi) ** alpha
    out: np.ndarray = lo * (1 - u * (1 - r)) ** (-1 / alpha)
    return out


class SyntheticGenerator:
    name = "synthetic_generator"
    version = "1.0"

    def bind(self, e: Engine) -> None:
        self.e = e

    def initial(self, i: int) -> list[PmOrder]:
        return []

    def evening(self, i: int) -> list[PmOrder]:
        return []

    def morning(self, i: int) -> list[PmOrder]:
        e = self.e
        md, g = e.md, e.cfg.generator
        md.guard(i - 1)
        rng = component_rng(e.cfg.seed, "generator", md.sessions[i])
        n = int(rng.poisson(g.lam))
        adv = md.adv20[i]  # trailing, excludes today: known pre-open
        prior = e.prior_close(i)
        ok = md.member[i - 1] & md.mapped & np.isfinite(adv) & (adv > 0) & np.isfinite(prior)
        ts = ny_ts(md.sessions[i], time(9, 15))
        out: list[PmOrder] = []
        # names with a declared unsupported corporate action: never picked, and flattened before the ex-date
        day = md.sessions[i]
        pending = {
            a.j
            for a in md.actions
            if a.kind in ("SO", "RI", "MG") and a.declared is not None and a.declared <= day < a.ex
        }
        for j in pending:
            ok[j] = False
            held = e.projected_holdings(i)[j]
            if held != 0 and np.isfinite(prior[j]):
                d0 = Decision(
                    e.ids.new(ts), e.run_id, e.cfg.fund_id, "CORP_ACTION_EXIT", md.sessions[i - 1], day, ts
                )
                e.decisions.append(d0)
                out.append(
                    PmOrder(
                        pm_order_id=e.ids.new(ts),
                        run_id=e.run_id,
                        fund_id=e.cfg.fund_id,
                        decision_id=d0.decision_id,
                        permno=int(md.permnos[j]),
                        listing_id=int(md.listing_id[j]),
                        side="SELL" if held > 0 else "BUY",
                        quantity=float(abs(held)),
                        reason=str(Reason.CORP_ACTION),
                        decision_ts=ts,
                        decision_price=float(prior[j]),
                        decision_price_source=str(DecisionPriceSource.PRIOR_CLOSE),
                        instruction="TARGET_CLOSE",
                        deadline_ts=close_ts(day),
                        urgency=str(Urgency.HIGH),
                        limit_price=None,
                        status="NEW",
                        created_by=f"{self.name}@{self.version}",
                        exec_date=day,
                    )
                )
        if n == 0 or not ok.any():
            return out
        p = np.where(ok, np.nan_to_num(adv) * np.nan_to_num(md.prc[i - 1]), 0.0)
        js = rng.choice(md.N, n, p=p / p.sum())
        x = truncated_pareto(rng, g.pareto_alpha, g.min_participation, g.max_participation, n)
        sides = rng.choice(["BUY", "SELL"], n)
        insts = list(g.instruction_mix)
        probs = np.array([g.instruction_mix[k] for k in insts])
        inst = rng.choice(insts, n, p=probs / probs.sum())
        c = rng.uniform(g.cost_min_bps, g.cost_max_bps, n)
        d = Decision(
            e.ids.new(ts), e.run_id, e.cfg.fund_id, "SYNTHETIC_DAILY", md.sessions[i - 1], md.sessions[i], ts
        )
        e.decisions.append(d)
        for k in range(n):
            j = int(js[k])
            q = max(1, math.floor(x[k] * adv[j]))
            sig_bps = float(np.nan_to_num(md.vol_intraday[i - 1, j], nan=0.015)) * 1e4
            xi = float(rng.normal(0, sig_bps * math.sqrt(DURATION[str(inst[k])])))
            out.append(
                PmOrder(
                    pm_order_id=e.ids.new(ts),
                    run_id=e.run_id,
                    fund_id=e.cfg.fund_id,
                    decision_id=d.decision_id,
                    permno=int(md.permnos[j]),
                    listing_id=int(md.listing_id[j]),
                    side=str(sides[k]),
                    quantity=float(q),
                    reason=str(Reason.SYNTHETIC_TEST),
                    decision_ts=ts,
                    decision_price=float(prior[j]),
                    decision_price_source=str(DecisionPriceSource.PRIOR_CLOSE),
                    instruction=str(inst[k]),
                    deadline_ts=close_ts(md.sessions[i]),
                    urgency=str(Urgency.MEDIUM),
                    limit_price=None,
                    status="NEW",
                    created_by=f"{self.name}@{self.version}",
                    exec_date=md.sessions[i],
                    answer_key={
                        "injected_cost_bps": float(c[k]),
                        "noise_bps": xi,
                        "participation": float(x[k]),
                    },
                )
            )
        return out
