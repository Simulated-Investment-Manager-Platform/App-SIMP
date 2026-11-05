"""Compatibility shim for the handoff notebook ``BMLL_Stage3_Daily_Fund_Research.ipynb``.

``research_pipeline.py`` from the handoff is split into ``simp.accounting``, ``simp.data.crsp``
and ``simp.analytics``. These names keep the notebook's imports working. Each notebook hook
that was waiting on an input now receives it from SIMP:

* ``seeded_flows``            <- simp.accounting.flows (preset "notebook_seeded", sigma 0.5 bps)
* ``benchmark_report``        <- benchmark_returns.csv written from the lake (crsp.dsp500 / vendor TR)
* ``validate_bmll_mapping``   <- bmll_identifier_mapping.csv (``simp map build``)
* ``execution_comparison``    <- per-order arrival / VWAP / TWAP / auction prices from the TCA tables
"""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import polars as pl

from simp.accounting.flows import simulate_rates
from simp.analytics.fund import fund_report
from simp.config.run_config import FlowConfig
from simp.data.mapping.builder import validate_mapping


def seeded_flows(dates: list[date], seed: int = 42, sigma_bps: float = 0.5, aum: float = 1.0) -> pd.DataFrame:
    """Daily flows as a fraction of AUM (the notebook's Monte Carlo flows)."""
    cfg = FlowConfig.from_preset("notebook_seeded").model_copy(update={"sigma_bps": sigma_bps})
    rates = simulate_rates(cfg, seed, list(dates))
    return pd.DataFrame({"date": dates, "amount": rates * aum})


def benchmark_report(
    portfolio_nav: pd.DataFrame, benchmark_returns: pd.DataFrame, start: date
) -> dict[str, Any]:
    """portfolio_nav: date, nav, unit_nav, fund_return; benchmark_returns: date, return."""
    nav = portfolio_nav.merge(benchmark_returns.rename(columns={"return": "bench_return_tr"}), on="date")
    rows = [
        {
            "date": pd.Timestamp(r.date).date(),
            "fund_return": float(r.fund_return),
            "gross_return": float(r.fund_return),
            "bench_return_tr": float(r.bench_return_tr),
            "bench_return_official": None,
            "bench_return_pr": float(r.bench_return_tr),
            "unit_nav": float(r.unit_nav),
            "nav": float(r.nav),
            "cash_weight": 0.0,
            "trading_cost_usd": 0.0,
            "fees_usd": 0.0,
            "flow_usd": 0.0,
        }
        for r in nav.itertuples()
    ]
    return fund_report(rows, start)


def validate_bmll_mapping(mapping: pd.DataFrame, needs: pd.DataFrame) -> pd.DataFrame:
    """mapping: permno, bmll_id, valid_from, valid_to; needs: permno, date. Returns violations."""
    smap = pl.from_pandas(mapping.rename(columns={"bmll_id": "listing_id"}))
    smap = smap.with_columns(pl.col("valid_from").cast(pl.Date), pl.col("valid_to").cast(pl.Date))
    nd = pl.from_pandas(needs).with_columns(pl.col("date").cast(pl.Date))
    return validate_mapping(smap, nd).to_pandas()


def execution_comparison(tca_orders: pd.DataFrame) -> pd.DataFrame:
    """Per-order arrival, VWAP, auction and close prices with the cost of each vs decision (bps)."""
    s = np.where(tca_orders["side"] == "BUY", 1.0, -1.0)
    out = tca_orders[["pm_order_id", "date", "permno", "side", "quantity", "decision_price"]].copy()
    for col in ("arrival_price", "vwap_day", "vwap_interval", "close_price", "avg_price"):
        out[col] = tca_orders[col]
        out[f"{col}_bps"] = (
            s * (tca_orders[col] - tca_orders["decision_price"]) / tca_orders["decision_price"] * 1e4
        )
    return out
