r"""Synthetic daily investor flows (spec: Investor flows).

F_t = AUM_{t-1} (mu + sigma eps_t + J_t),  eps_t = phi eps_{t-1} + sqrt(1-phi^2) eta_t,
eta_t ~ t_nu / sqrt(nu/(nu-2)), J_t = jump with prob p, size U(jump_min, jump_max) bps.
"""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl

from simp.config.run_config import FlowConfig
from simp.rng import component_rng


class FlowProcess:
    def __init__(self, cfg: FlowConfig, seed: int) -> None:
        self.cfg = cfg
        self.seed = seed
        self.eps = 0.0
        self._file: dict[date, float] | None = None
        if cfg.preset == "file" and cfg.flow_file:
            df = pl.read_csv(Path(cfg.flow_file), try_parse_dates=True)
            self._file = {d: float(a) for d, a in df.select("date", "amount").iter_rows()}

    def draw(self, day: date, aum_prev: float) -> tuple[float, bool]:
        """Returns (net flow in USD, is_jump)."""
        c = self.cfg
        if not c.enabled:
            return 0.0, False
        if self._file is not None:
            return self._file.get(day, 0.0), False
        rng = component_rng(self.seed, "flows", day)
        eta = rng.standard_t(c.nu) / math.sqrt(c.nu / (c.nu - 2))
        self.eps = c.phi * self.eps + math.sqrt(1 - c.phi**2) * eta
        jump = rng.random() < c.jump_prob
        j_bps = rng.uniform(c.jump_min_bps, c.jump_max_bps) if jump else 0.0
        rate = (c.mu_bps + c.sigma_bps * self.eps + j_bps) / 1e4
        return aum_prev * rate, jump


def simulate_rates(cfg: FlowConfig, seed: int, days: list[date]) -> np.ndarray:
    """Flow rates (fraction of AUM) for a list of days — used by flow-regime previews."""
    fp = FlowProcess(cfg, seed)
    return np.array([fp.draw(d, 1.0)[0] for d in days])
