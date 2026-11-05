"""Run-config schema. A run is fully described by this config, the data manifest hashes and
the git SHA. Validation is explicit (Pydantic validators), never bare ``assert``."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from simp.domain.enums import ExecutionModel, TimingRule
from simp.errors import DataWindowError


class Window(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bootstrap: date = date(2022, 6, 21)
    start: date = date(2022, 7, 1)
    end: date = date(2026, 6, 30)

    @model_validator(mode="after")
    def check(self) -> Window:
        if self.start > self.end:
            raise ValueError(f"start {self.start} is after end {self.end}")
        if self.bootstrap > self.start:
            raise ValueError(f"bootstrap {self.bootstrap} is after start {self.start}")
        return self


def check_release(window: Window, first: date, last: date) -> None:
    """The handoff's ``assert start >= first and end <= last or mode == 'cache'`` parsed as
    ``(a and b) or c``. Here every bound is always checked."""
    if window.bootstrap < first or window.end > last:
        raise DataWindowError(
            f"data release covers {first}..{last}, run asks {window.bootstrap}..{window.end}"
        )


class FlowConfig(BaseModel):
    """F_t = AUM_{t-1} (mu + sigma eps_t + J_t), eps AR(1) with Student-t innovations."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    preset: Literal["default", "notebook_seeded", "none", "custom", "file"] = "default"
    mu_bps: float = 0.0
    sigma_bps: float = 10.0
    phi: float = Field(0.3, ge=-0.99, le=0.99)
    nu: float = Field(4.0, gt=2.0)
    jump_prob: float = Field(0.005, ge=0.0, le=1.0)
    jump_min_bps: float = -200.0
    jump_max_bps: float = -50.0
    flow_file: str | None = None  # CSV "date,amount" replaces the generator
    min_ticket_usd: float = 5_000.0
    min_ticket_nav_bps: float = 0.1
    cash_buffer_bps: float = 10.0
    cash_sweep: bool = True  # reinvest dividends / residual cash above the buffer daily
    netting: bool = True

    @model_validator(mode="after")
    def check(self) -> FlowConfig:
        if self.jump_min_bps > self.jump_max_bps:
            raise ValueError("jump_min_bps must be <= jump_max_bps")
        if self.preset == "file" and not self.flow_file:
            raise ValueError("preset 'file' needs flow_file")
        return self

    @classmethod
    def from_preset(cls, name: str) -> FlowConfig:
        if name == "none":
            return cls(enabled=False, preset="none", cash_sweep=False, cash_buffer_bps=0.0)
        if name == "notebook_seeded":
            return cls(preset="notebook_seeded", sigma_bps=0.5, jump_prob=0.0, phi=0.0)
        return cls()


class CostModelParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    Y: float = 0.7
    beta: float = 0.5
    kappa: float = 0.5
    lam: float = 0.5
    auction_cap: float = 0.25  # excess over 25% of expected auction volume spills to continuous
    t2_interval_cap: float = 0.10  # T2: max share of each interval's volume
    moc_auction_share: float | None = Field(None, gt=0, le=0.5)  # overrides the desk policy's 5% MOC share


class TimingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule: TimingRule = TimingRule.EFFECTIVE_DATE
    decision_offset_sessions: int = Field(5, ge=1, le=20)
    initial_execution_offset: int = Field(1, ge=0, le=5)


class GeneratorConfig(BaseModel):
    """Phase 1 random multi-MIC order generator."""

    model_config = ConfigDict(extra="forbid")
    lam: float = 40.0
    pareto_alpha: float = 1.2
    min_participation: float = 0.0001
    max_participation: float = 0.25
    cost_min_bps: float = -5.0
    cost_max_bps: float = 25.0
    instruction_mix: dict[str, float] = {"VWAP_DAY": 0.5, "ARRIVAL": 0.3, "TARGET_CLOSE": 0.2}


class RunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "S&P 500 index fund"
    fund_id: str = "SP500_INDEX"
    strategy: Literal["index_replication", "synthetic_generator"] = "index_replication"
    seed: int = 42
    window: Window = Window()
    initial_aum: float = 10_000_000_000.0
    initial_unit_nav: float = 100.0
    fee_bps: float = 3.0  # annual management fee
    timing: TimingConfig = TimingConfig()
    flows: FlowConfig = FlowConfig()
    execution_model: ExecutionModel = ExecutionModel.T1
    cost: CostModelParams = CostModelParams()
    desk_policy: str = "default"
    benchmark: Literal["CRSP_VWRETD", "SP500_TR"] = "CRSP_VWRETD"
    exclusion_policy: Literal["exclude_securities", "keep_securities"] = "exclude_securities"
    unmapped_policy: Literal["exclude", "stop"] = "exclude"
    provisional_dividends: bool = True
    generator: GeneratorConfig = GeneratorConfig()
    data_snapshot: str | None = None  # manifest-set hash; None = resolve at submit and pin
    study_id: str | None = None
    study_step: int | None = None
    label: str | None = None

    @model_validator(mode="after")
    def check(self) -> RunConfig:
        if self.initial_aum <= 0:
            raise ValueError("initial_aum must be positive")
        if self.strategy == "synthetic_generator" and self.execution_model == ExecutionModel.T0:
            raise ValueError("the Phase 1 generator prices around VWAP; use T1 or T2, not T0")
        return self

    def canonical_json(self) -> str:
        data: dict[str, Any] = self.model_dump(mode="json", exclude={"study_id", "study_step", "label"})
        return json.dumps(data, sort_keys=True, separators=(",", ":"))

    def config_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


PRESETS: dict[str, dict[str, Any]] = {
    "handoff_replay": {
        "name": "Handoff replay (T0, handoff timing, no flows)",
        "timing": {"rule": "HANDOFF"},
        "execution_model": "T0",
        "flows": FlowConfig.from_preset("none").model_dump(),
        "fee_bps": 0.0,
    },
    "index_t1_flows": {"name": "S&P 500 index fund, T1 costs, flows"},
    "index_t0_effective": {
        "name": "S&P 500 index fund, T0, effective-date timing",
        "execution_model": "T0",
        "flows": FlowConfig.from_preset("none").model_dump(),
        "fee_bps": 0.0,
    },
    "phase1_generator": {
        "name": "Phase 1 random order generator",
        "fund_id": "SYNTH_GENERATOR",
        "strategy": "synthetic_generator",
        "window": {"bootstrap": "2026-01-02", "start": "2026-01-05", "end": "2026-03-31"},
        "flows": FlowConfig.from_preset("none").model_dump(),
        "fee_bps": 0.0,
    },
}


def preset(name: str, **overrides: Any) -> RunConfig:
    base = dict(PRESETS[name])
    base.update(overrides)
    return RunConfig.model_validate(base)
