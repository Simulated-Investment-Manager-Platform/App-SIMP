"""Typed API responses (feed the generated TypeScript client)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


class Me(BaseModel):
    username: str
    role: str
    auth_mode: str


class FundOut(BaseModel):
    fund_id: str
    name: str
    strategy_type: str
    benchmark_id: str
    inception_date: date
    initial_aum: float
    cash_buffer_bps: float
    fee_bps: float
    description: str | None = None


class FundIn(BaseModel):
    fund_id: str
    name: str
    strategy_type: str = "INDEX"
    benchmark_id: str = "CRSP_VWRETD"
    inception_date: date
    initial_aum: float
    cash_buffer_bps: float = 10.0
    fee_bps: float = 3.0
    description: str | None = None


class RunOut(BaseModel):
    run_id: str
    fund_id: str
    name: str | None
    status: str
    stage: str | None
    progress: float | None
    config_hash: str
    code_version: str | None
    data_label: str | None
    data_snapshot_hashes: dict[str, Any] | None
    submitted_by: str | None
    submitted_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    output_hash: str | None
    study_id: str | None
    study_step: int | None
    execution_model: str | None = None
    error: dict[str, Any] | None = None


class RunDetail(RunOut):
    config: dict[str, Any]
    summary: dict[str, Any] | None


class SubmitOut(BaseModel):
    run_id: str
    config_hash: str


class NavPoint(BaseModel):
    date: date
    nav: float
    unit_nav: float
    units: float
    cash: float
    equity_value: float
    receivables: float
    payables: float
    fund_return: float
    gross_return: float
    flow_usd: float
    bench_return_tr: float | None
    bench_return_official: float | None
    cash_weight: float
    bench_level: float
    fund_level: float
    active_return: float


class Page(BaseModel):
    total: int
    limit: int
    offset: int
    rows: list[dict[str, Any]]


class TcaGroup(BaseModel):
    key: dict[str, str | None]
    orders: int
    notional: float
    notional_share: float | None = None
    cost_bps: float | None
    cost_usd: float
    ci90: list[float | None]
    eq_weighted_bps: float | None
    p50: float | None
    p90: float | None


class TcaOut(BaseModel):
    method: str
    metric: str
    weighting: str
    groups: list[TcaGroup]
    total: TcaGroup
    fallbacks: dict[str, Any]
    td: dict[str, Any] | None
    data_label: str | None


class ValidateOut(BaseModel):
    ok: bool
    errors: list[str]
    rules: int | None = None
