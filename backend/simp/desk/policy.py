"""Desk policy: a YAML rules table (data, not code) mapping PM orders to trader-order legs."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from simp.desk.reference import BROKERS
from simp.domain.enums import Strategy

POLICY_DIR = Path(__file__).parent / "policies"


class When(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: list[str] | None = None
    instruction: str | None = None
    max_notional: float | None = None
    basket: bool = False
    max_pct_expected_auction: float | None = None
    max_pct_adv: float | None = None


class Leg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy: Strategy
    broker: str | None = None
    brokers: dict[str, float] | None = None
    window: tuple[str, str] | None = None
    pct_expected_auction: float | None = None
    remainder: bool = False
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check(self) -> Leg:
        if (self.broker is None) == (self.brokers is None):
            raise ValueError("a leg needs exactly one of broker / brokers")
        for b in [self.broker] if self.broker else list(self.brokers or {}):
            if b not in BROKERS:
                raise ValueError(f"unknown broker {b}")
        if self.brokers and abs(sum(self.brokers.values()) - 1) > 1e-9:
            raise ValueError("broker weights must sum to 1")
        if self.window:
            t0, t1 = (time.fromisoformat(x) for x in self.window)
            if not (time(9, 30) <= t0 < t1 <= time(16, 0)):
                raise ValueError(f"window {self.window} must lie inside 09:30-16:00 and start before it ends")
        return self


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    when: When
    then: list[Leg]

    @field_validator("then")
    @classmethod
    def non_empty(cls, v: list[Leg]) -> list[Leg]:
        if not v:
            raise ValueError("rule needs at least one leg")
        if sum(1 for leg in v if leg.remainder) > 1:
            raise ValueError("at most one remainder leg")
        if len(v) > 1 and not any(leg.remainder for leg in v):
            raise ValueError("multi-leg rules need a remainder leg")
        return v


class DeskPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    version: int
    desk_id: str
    expected_auction: dict[str, Any]
    auction_spill_cap: float = Field(0.25, gt=0, le=1)
    rules: list[Rule]

    @model_validator(mode="after")
    def check(self) -> DeskPolicy:
        if not self.rules or self.rules[-1].when != When():
            raise ValueError("the last rule must be a catch-all (`when: {}`)")
        return self


def load_policy(name_or_yaml: str) -> DeskPolicy:
    if "\n" in name_or_yaml:
        data = yaml.safe_load(name_or_yaml)
    else:
        data = yaml.safe_load((POLICY_DIR / f"{name_or_yaml}.yaml").read_text())
    return DeskPolicy.model_validate(data)


def policy_names() -> list[str]:
    return sorted(p.stem for p in POLICY_DIR.glob("*.yaml"))


@dataclass
class LegPlan:
    strategy: str
    broker_id: str
    quantity: float
    window: tuple[time, time]
    params: dict[str, Any] = field(default_factory=dict)
    program_key: str | None = None


@dataclass
class OrderContext:
    """What the desk may know about one PM order on its execution day (data <= prior close)."""

    reason: str
    instruction: str
    quantity: float
    notional: float
    adv20: float
    expected_auction: float
    basket_key: str | None


def _matches(w: When, o: OrderContext) -> bool:
    if w.reason is not None and o.reason not in w.reason:
        return False
    if w.instruction is not None and o.instruction != w.instruction:
        return False
    if w.max_notional is not None and o.notional > w.max_notional:
        return False
    if w.basket and o.basket_key is None:
        return False
    if w.max_pct_expected_auction is not None and (
        o.expected_auction <= 0 or o.quantity > w.max_pct_expected_auction * o.expected_auction
    ):
        return False
    if w.max_pct_adv is not None and (not o.adv20 or o.quantity > w.max_pct_adv * o.adv20):
        return False
    return True


def _window(leg: Leg, strategy: Strategy) -> tuple[time, time]:
    if leg.window:
        return time.fromisoformat(leg.window[0]), time.fromisoformat(leg.window[1])
    if strategy in (Strategy.MOC, Strategy.LOC, Strategy.PORTFOLIO_TRADE):
        return time(15, 45), time(16, 0)
    return time(9, 30), time(15, 50)


def plan_order(policy: DeskPolicy, o: OrderContext, rng: np.random.Generator) -> tuple[str, list[LegPlan]]:
    for rule in policy.rules:
        if not _matches(rule.when, o):
            continue
        legs: list[LegPlan] = []
        left = o.quantity
        for leg in rule.then:
            if leg.brokers:
                names, w = zip(*sorted(leg.brokers.items()), strict=True)
                broker = str(rng.choice(list(names), p=np.array(w)))
            else:
                broker = str(leg.broker)
            if leg.remainder:
                continue
            q = left
            if leg.pct_expected_auction is not None:
                q = min(left, math.floor(leg.pct_expected_auction * max(o.expected_auction, 0.0)))
            if q <= 0:
                continue
            legs.append(
                LegPlan(
                    str(leg.strategy),
                    broker,
                    q,
                    _window(leg, leg.strategy),
                    dict(leg.params),
                    o.basket_key if leg.strategy == Strategy.PORTFOLIO_TRADE else None,
                )
            )
            left -= q
        rem = next((leg for leg in rule.then if leg.remainder), None)
        if rem is not None and left > 0:
            broker = str(rem.broker) if rem.broker else sorted(rem.brokers or {})[0]
            legs.append(
                LegPlan(str(rem.strategy), broker, left, _window(rem, rem.strategy), dict(rem.params))
            )
        return rule.name, legs
    raise AssertionError("unreachable: policy validation guarantees a catch-all rule")


def apply_spill_cap(policy: DeskPolicy, legs: list[LegPlan], expected_auction: float) -> list[LegPlan]:
    """MOC quantity above ``auction_spill_cap`` x expected auction volume fills in the
    continuous session instead of in the cross."""
    cap = math.floor(policy.auction_spill_cap * max(expected_auction, 0.0))
    out: list[LegPlan] = []
    for leg in legs:
        if leg.strategy == Strategy.MOC and leg.quantity > cap:
            if cap > 0:
                out.append(LegPlan(leg.strategy, leg.broker_id, cap, leg.window, dict(leg.params)))
            out.append(
                LegPlan(
                    str(Strategy.ALGO_VWAP),
                    "BRK_A",
                    leg.quantity - max(cap, 0),
                    (time(13, 0), time(15, 50)),
                    {"spill_from": "MOC", "reason": "above auction cap"},
                )
            )
        else:
            out.append(leg)
    return out
