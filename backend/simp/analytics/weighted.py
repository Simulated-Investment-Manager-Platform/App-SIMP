"""Rule zero: weight by notional. Weighted means, weighted quantiles, and a block bootstrap
by rebalance date (orders on one date share a market shock)."""

from __future__ import annotations

import numpy as np

SIZE_EDGES = [0.0, 0.001, 0.005, 0.01, 0.05, 0.10, np.inf]
SIZE_LABELS = ["<=0.1%", "0.1-0.5%", "0.5-1%", "1-5%", "5-10%", ">10%"]
AUC_EDGES = [0.0, 0.01, 0.05, 0.10, 0.25, np.inf]
AUC_LABELS = ["<=1%", "1-5%", "5-10%", "10-25%", ">25%"]


def wmean(x: np.ndarray, w: np.ndarray) -> float:
    m = np.isfinite(x) & np.isfinite(w) & (w > 0)
    if not m.any():
        return float("nan")
    return float(np.sum(x[m] * w[m]) / np.sum(w[m]))


def wquantile(x: np.ndarray, w: np.ndarray, q: float | list[float]) -> np.ndarray:
    m = np.isfinite(x) & np.isfinite(w) & (w > 0)
    x, w = x[m], w[m]
    if x.size == 0:
        return np.full(np.size(q), np.nan)
    o = np.argsort(x)
    x, w = x[o], w[o]
    cw = (np.cumsum(w) - 0.5 * w) / w.sum()
    out: np.ndarray = np.interp(np.atleast_1d(q), cw, x)
    return out


def bucket(values: np.ndarray, edges: list[float], labels: list[str]) -> np.ndarray:
    """Right-closed buckets: edges [0, a, b, ..., inf] -> (<=a], (a, b], ..."""
    v = np.asarray(values, float)
    idx = np.searchsorted(np.asarray(edges[1:-1]), v, side="left")
    out = np.array(labels, dtype=object)[np.clip(idx, 0, len(labels) - 1)]
    out[~np.isfinite(v)] = "n/a"
    return out


def block_bootstrap_ci(
    x: np.ndarray,
    w: np.ndarray,
    blocks: np.ndarray,
    *,
    n_boot: int = 2000,
    level: float = 0.90,
    seed: int = 7,
) -> tuple[float, float]:
    """CI of the notional-weighted mean, resampling whole blocks (dates) with replacement.
    With a single block, falls back to resampling orders."""
    m = np.isfinite(x) & np.isfinite(w) & (w > 0)
    x, w, blocks = x[m], w[m], blocks[m]
    if x.size == 0:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    ub, inv = np.unique(blocks, return_inverse=True)
    if len(ub) < 2:
        sums_nx, sums_n, k = x * w, w, len(x)
    else:
        k = len(ub)
        sums_nx = np.bincount(inv, weights=x * w, minlength=k)
        sums_n = np.bincount(inv, weights=w, minlength=k)
    idx = rng.integers(0, len(sums_n), (n_boot, len(sums_n)))
    est = sums_nx[idx].sum(1) / sums_n[idx].sum(1)
    a = (1 - level) / 2
    lo, hi = np.quantile(est, [a, 1 - a])
    return float(lo), float(hi)


def concentration_curve(notional: np.ndarray, points: int = 50) -> tuple[np.ndarray, np.ndarray, float]:
    """Cumulative notional share vs share of orders (largest first); returns top-10% share."""
    n = np.sort(np.asarray(notional, float))[::-1]
    if n.size == 0:
        return np.array([]), np.array([]), float("nan")
    c = np.cumsum(n) / n.sum()
    xs = np.linspace(0, 1, points + 1)[1:]
    ys = c[np.clip((xs * n.size).astype(int) - 1, 0, n.size - 1)]
    top10 = float(c[max(int(np.ceil(0.1 * n.size)) - 1, 0)])
    return xs, ys, top10
