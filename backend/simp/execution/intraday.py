"""Intraday prices for one (security, session).

Synthetic snapshots rebuild the generator's minute paths from stored lake columns + seed.
Live snapshots use BMLL Trades Plus @SIP minute bars when the snapshot has them
(``bmll/trades_plus_minute``: NBBO mid, continuous VWAP and volume per minute); names/sessions
without bars fall back to the day VWAP (flag INTRADAY_FALLBACK_DAILY on T2 fills).
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
        # live data with Trades Plus minute bars: in the snapshot, or fetched on demand (T2 runs)
        self.live = bool(md.bars) or md.bar_loader is not None
        self._live: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray] | None] = {}

    def bars(self, i: int) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        if self.md.bar_loader is None:
            return self.md.bars.get(i)
        if i not in self._live:  # small LRU: a long run never holds every session's bars
            self._live[i] = self.md.bar_loader(i)
            if len(self._live) > 8:
                self._live.pop(next(iter(self._live)))
        return self._live[i]

    def forget(self, i: int) -> None:
        """Drop session ``i`` from the LRU (after new bars were fetched for it)."""
        self._live.pop(i, None)

    def has_bars(self, i: int, j: int) -> bool:
        b = self.bars(i)
        return b is not None and bool(np.isfinite(b[0][j]).any())

    def minute_volume(self, i: int, j: int) -> np.ndarray | None:
        """Real addressable volume per minute (live bars), else None (use curve x daily volume)."""
        b = self.bars(i)
        return b[2][j] if b is not None and self.has_bars(i, j) else None

    def paths(self, i: int) -> np.ndarray | None:
        if not self.synthetic:
            b = self.bars(i)
            return None if b is None else b[0]
        if i not in self._cache:
            md = self.md
            opens = np.nan_to_num(md.opn[i], nan=1.0)
            last = np.nan_to_num(md.prc[i] / (1 + md.dislocation_bps[i] / 1e4), nan=1.0)
            sig = np.nan_to_num(md.vol_intraday[i], nan=0.015)
            self._cache[i] = intraday_paths(
                int(md.data_seed or 0), md.sessions[i], np.maximum(opens, 1.0), np.maximum(last, 1.0), sig
            )
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
        if not self.synthetic and not np.isfinite(row).any():
            return float(self.md.vwap[i, j])
        if minute <= 0:
            return float(row[0] if not self.synthetic else self.md.opn[i, j])
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
        if not self.synthetic:
            return base  # live bars: no synthetic sub-minute noise on real quotes
        sec = int(seconds_from_open)
        h = zlib.crc32(f"{self.md.sessions[i]}:{j}:{sec}".encode()) / 2**32 - 0.5
        frac = (seconds_from_open % 60) / 60
        sig = float(np.nan_to_num(self.md.vol_intraday[i, j], nan=0.015)) / np.sqrt(N_MINUTES)
        return float(base * (1 + 0.6 * sig * h * np.sqrt(frac * (1 - frac) + 1e-9) * 2))

    def interval_vwap(self, i: int, j: int, t0: time, t1: time) -> tuple[float, bool]:
        p = self.paths(i)
        if p is None:
            return float(self.md.vwap[i, j]), True
        a, b = int(max(0, minutes_from_open(t0))), int(min(N_MINUTES, np.ceil(minutes_from_open(t1))))
        if b <= a:
            return self.price_at(i, j, a), False
        if not self.synthetic:
            if not self.has_bars(i, j):
                return float(self.md.vwap[i, j]), True
            _, vw, vol = self.bars(i)  # type: ignore[misc]
            v = vol[j, a:b]
            if v.sum() <= 0:
                return self.price_at(i, j, (a + b) / 2), False
            return float((vw[j, a:b] * v).sum() / v.sum()), False
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
