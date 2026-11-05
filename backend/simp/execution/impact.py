r"""T1 impact models (spec: Execution models).

continuous:  IS_bps = 1/2 spread_bps + Y sigma_d sqrt(Q/ADV) x 1e4
participation (VWAP algos): Y sigma_d sqrt(Q/ADV) x 1e4
auction:     dp_bps = kappa sigma_d (Q/V_auc)^beta (1 + lambda 1[same side as imbalance]) x 1e4
"""

from __future__ import annotations

import math

from simp.config.run_config import CostModelParams


def participation_impact_bps(q: float, adv: float, sigma_d: float, p: CostModelParams) -> float:
    if adv <= 0 or q <= 0:
        return 0.0
    return p.Y * sigma_d * math.sqrt(q / adv) * 1e4


def arrival_is_bps(q: float, adv: float, sigma_d: float, spread_bps: float, p: CostModelParams) -> float:
    return 0.5 * spread_bps + participation_impact_bps(q, adv, sigma_d, p)


def auction_impact_bps(q: float, v_auc: float, sigma_d: float, same_side: bool, p: CostModelParams) -> float:
    if v_auc <= 0 or q <= 0:
        return 0.0
    return float(p.kappa * sigma_d * (q / v_auc) ** p.beta * (1 + p.lam * (1.0 if same_side else 0.0)) * 1e4)
