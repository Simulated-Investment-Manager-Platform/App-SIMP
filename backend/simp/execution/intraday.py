"""Intraday prices for one (security, session).

Synthetic snapshots rebuild the generator's minute paths from stored lake columns + seed.
Live snapshots use BMLL Trades Plus @SIP partitions when present (``lake/bmll/trades_plus``);
with daily data only, intraday prices fall back to the day VWAP (flag INTRADAY_FALLBACK_DAILY).
"""

from __future__ import annotations

import zlib
from datetime import datetime, time

import numpy as np

from simp.accounting.calendar import NY, ny_ts
from simp.data.market import MarketData
from simp.data.synthetic.generator import N_MINUTES, VOLUME_CURVE, intraday_paths

OPEN_MIN = 9 * 60 + 30


def minutes_from_open(t: time) -> float:
    return (t.hour * 60 + t.minute + t.second / 60) - OPEN_MIN


def ts_at(day_i_date: object, minute: float) -> datetime:
    total = OPEN_MIN + minute
    h, m = divmod(int(total), 60)
    s = int(round((total - int(total)) * 60))
    if s == 60:
        m, s = m + 1, 0
    return ny_ts(day_i_date, time(h, m, min(s, 59)))  # type: ignore[arg-type]


class IntradayService:
    def __init__(self, md: MarketData) -> None:
        self.md = md
        self._cache: dict[int, np.ndarray] = {}
        self.synthetic = md.data_seed is not None

    def paths(self, i: int) -> np.ndarray | None:
        if not self.synthetic:
            return None
        if i not in self._cache:
            md = self.md
            opens = np.nan_to_num(md.opn[i], nan=1.0)
            last = np.nan_to_num(md.prc[i] / (1 + md.dislocation_bps[i] / 1e4), nan=1.0)
            sig = np.nan_to_num(md.vol_intraday[i], nan=0.015)
            self._cache[i] = intraday_paths(int(md.data_seed or 0), md.sessions[i], np.maximum(opens, 1.0), np.maximum(last, 1.0), sig)
            if len(self._cache) > 8:
                self._cache.pop(next(iter(self._cache)))
        return self._cache[i]

    def minute_curve(self) -> np.ndarray:
        return VOLUME_CURVE

    def price_at(self, i: int, j: int, minute: float) -> float:
        """Mid price ``minute`` minutes after 09:30 (0..390)."""
        p = self.paths(i)
        if p is None:
            return float(self.md.vwap[i, j])
        row = p[j]
        if minute <= 0:
            return float(self.md.opn[i, j])
        k = min(minute, N_MINUTES) - 1
        lo = int(np.floor(k))
        if lo < 0:
            w = minute
            return float(self.md.opn[i, j] * (1 - w) + row[0] * w)
        hi = min(lo + 1, N_MINUTES - 1)
        f = k - lo
        return float(row[lo] * (1 - f) + row[hi] * f)

    def mid_at_seconds(self, i: int, j: int, seconds_from_open: float) -> float:
        """Second-resolution mid: minute interpolation + a deterministic sub-minute bridge."""
        base = self.price_at(i, j, seconds_from_open / 60.0)
        sec = int(seconds_from_open)
        h = zlib.crc32(f"{self.md.sessions[i]}:{j}:{sec}".encode()) / 2**32 - 0.5
        frac = (seconds_from_open % 60) / 60
        sig = float(np.nan_to_num(self.md.vol_intraday[i, j], nan=0.015)) / np.sqrt(N_MINUTES)
        return base * (1 + 0.6 * sig * h * np.sqrt(frac * (1 - frac) + 1e-9) * 2)

    def interval_vwap(self, i: int, j: int, t0: time, t1: time) -> tuple[float, bool]:
        p = self.paths(i)
        if p is None:
            return float(self.md.vwap[i, j]), True
        a, b = int(max(0, minutes_from_open(t0))), int(min(N_MINUTES, np.ceil(minutes_from_open(t1))))
        if b <= a:
            return self.price_at(i, j, a), False
        w = VOLUME_CURVE[a:b]
        return float((p[j, a:b] * w).sum() / w.sum()), False

    def schedule(self, t0: time, t1: time, n: int, front_load: float = 0.0) -> np.ndarray:
        """``n`` slice times (minutes from open) following the volume curve inside [t0, t1]."""
        a, b = max(0.0, minutes_from_open(t0)), min(float(N_MINUTES), minutes_from_open(t1))
        lo, hi = int(a), max(int(a) + 1, int(np.ceil(b)))
        w = VOLUME_CURVE[lo:hi].copy()
        if front_load:
            w *= np.exp(-front_load * np.linspace(0, 1, len(w)))
        cdf = np.cumsum(w) / w.sum()
        qs = (np.arange(n) + 0.5) / n
        idx = np.searchsorted(cdf, qs)
        return lo + np.minimum(idx, len(w) - 1) + 0.5

    @staticmethod
    def local(ts: datetime) -> datetime:
        return ts.astimezone(NY)
