"""Execution models T0-T3 (spec: Execution models). Each fill records the model that made it.

T0  close proxy   — CRSP close, zero cost (handoff baseline)
T1  daily data    — MOC: close auction price + auction impact; algos: interval path + participation
                    impact; IS / high touch: path + half spread + square-root impact
T2  tick replay   — algo slices fill against interval prints, capped at 10% of each interval's
                    volume; aggressive slices pay the prevailing half spread; leftovers expire
T3  BMLL simulator — bmll2.simulation inside the Data Lab only (raises outside it)
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import time, timedelta

import numpy as np

from simp.config.run_config import CostModelParams
from simp.data.market import VENUES, MarketData
from simp.desk.reference import VENUES as VENUE_REF
from simp.domain.enums import (
    ChildStatus,
    ExecutionModel,
    Liquidity,
    MarketState,
    OrderType,
    Strategy,
    Tif,
    TraderStatus,
    side_sign,
)
from simp.domain.ids import IdFactory
from simp.domain.records import ChildOrder, Fill, TraderOrder
from simp.errors import ExecutionModelUnavailable
from simp.execution.impact import arrival_is_bps, auction_impact_bps, participation_impact_bps
from simp.execution.intraday import IntradayService, minutes_from_open, ts_at
from simp.rng import component_rng

CLOSE = time(16, 0)


@dataclass
class DayResult:
    children: list[ChildOrder]
    fills: list[Fill]


def _round_px(p: float, liquidity: str) -> float:
    return round(p, 4) if liquidity in (Liquidity.MIDPOINT, Liquidity.CROSS) else round(p, 2)


def _fee(venue: str, liquidity: str, qty: float) -> float:
    v = VENUE_REF.get(venue)
    if v is None:
        return 0.0
    per = {
        Liquidity.REMOVE: v.fee_remove,
        Liquidity.ADD: v.fee_add,
        Liquidity.AUCTION: v.fee_auction,
        Liquidity.MIDPOINT: v.fee_midpoint,
    }.get(Liquidity(liquidity), 0.0)
    return round(per * qty, 4)


class Executor:
    def __init__(
        self,
        md: MarketData,
        intraday: IntradayService,
        model: ExecutionModel,
        cost: CostModelParams,
        ids: IdFactory,
        run_id: str,
        seed: int,
    ) -> None:
        self.md, self.intra, self.model, self.cost, self.ids = md, intraday, model, cost, ids
        self.run_id, self.seed = run_id, seed
        if model == ExecutionModel.T3:
            raise ExecutionModelUnavailable(
                "T3 runs bmll2.simulation (PriceTimeExchange + HistoricalActor/HistoricalAuctionActor, "
                "AlmgrenChrissModel/KylesLambdaModel) inside the BMLL Data Lab only. Submit it with "
                "`simp lab t3-job --run <run_id>`; results are imported as fills with fill_model=T3."
            )
        if model == ExecutionModel.T2 and not intraday.synthetic:
            raise ExecutionModelUnavailable(
                "T2 tick replay needs BMLL Trades Plus @SIP partitions in the lake (lake/bmll/trades_plus); "
                "pull them with a Data Lab job for the dates in this run."
            )

    # ------------------------------------------------------------------ helpers
    def _ref(self, i: int, j: int, minute: float) -> dict[str, float]:
        mid = self.intra.price_at(i, j, minute)
        half = mid * float(self.md.spread_bps[i, j]) / 2e4
        return {"bid": round(mid - half, 4), "ask": round(mid + half, 4), "mid": round(mid, 4)}

    def _sigma(self, i: int, j: int) -> float:
        v = float(self.md.vol_intraday[i, j])
        return v if math.isfinite(v) and v > 0 else 0.015

    def _adv(self, i: int, j: int) -> float:
        a = float(self.md.adv20[i, j])
        return a if math.isfinite(a) and a > 0 else float(np.nan_to_num(self.md.volume[i, j], nan=1e6))

    def _child(
        self,
        to: TraderOrder,
        venue: str,
        otype: str,
        qty: float,
        sent_minute: float,
        tif: str,
        limit: float | None,
    ) -> ChildOrder:
        sent = max(ts_at(self.md.sessions[self._i], sent_minute), to.start_ts)
        return ChildOrder(
            self.ids.new(sent),
            to.trader_order_id,
            self.run_id,
            to.broker_id,
            venue,
            otype,
            qty,
            limit,
            sent,
            tif,
            str(ChildStatus.NEW),
        )

    def _fill(
        self,
        to: TraderOrder,
        ch: ChildOrder,
        minute: float,
        price: float,
        qty: float,
        liq: str,
        state: str,
        model: str,
        ref: dict[str, object],
        commission_bps: float | None = None,
    ) -> Fill:
        ts = ts_at(self.md.sessions[self._i], minute)
        ts = max(ts, ch.sent_ts)
        ts = min(ts, to.end_ts)
        px = _round_px(price, liq)
        cb = to.commission_bps if commission_bps is None else commission_bps
        return Fill(
            self.ids.new(ts),
            ch.child_order_id,
            to.trader_order_id,
            to.pm_order_id,
            self.run_id,
            to.broker_id,
            ch.venue_mic,
            ts,
            px,
            qty,
            liq,
            state,
            _fee(ch.venue_mic, liq, qty) if model != "T0_CLOSE" else 0.0,
            round(cb * px * qty / 1e4, 4),
            model,
            ref,
            to.permno,
            to.side,
        )

    # ------------------------------------------------------------------ main
    def execute(self, i: int, orders: list[TraderOrder]) -> DayResult:
        self._i = i
        children: list[ChildOrder] = []
        fills: list[Fill] = []
        moc_q: dict[tuple[int, int], float] = defaultdict(float)
        for o in orders:
            if o.strategy in (Strategy.MOC, Strategy.LOC):
                moc_q[(self.md.j(o.permno), side_sign(o.side))] += o.quantity
        rng = component_rng(self.seed, "execution", self.md.sessions[i])
        for o in orders:
            j = self.md.j(o.permno)
            if not math.isfinite(float(self.md.prc[i, j])):
                o.status = str(TraderStatus.EXPIRED)
                continue
            if self.model == ExecutionModel.T0:
                c, f = self._t0(o, i, j)
            elif "answer_key" in o.params:
                c, f = self._generator(o, i, j, rng)
            elif o.strategy == Strategy.INTERNAL_CROSS:
                c, f = self._cross(o, i, j)
            elif o.strategy == Strategy.PORTFOLIO_TRADE:
                c, f = self._portfolio(o, i, j)
            elif o.strategy in (Strategy.MOC, Strategy.LOC):
                c, f = self._auction(o, i, j, moc_q[(j, side_sign(o.side))])
            elif self.model == ExecutionModel.T2:
                c, f = self._t2(o, i, j, rng)
            else:
                c, f = self._t1_continuous(o, i, j, rng)
            filled = sum(x.quantity for x in f)
            o.filled_qty = filled
            o.status = str(
                TraderStatus.FILLED
                if filled >= o.quantity - 1e-9
                else TraderStatus.EXPIRED
                if filled == 0
                else TraderStatus.PARTIAL
            )
            if o.status == TraderStatus.PARTIAL:
                o.status = str(
                    TraderStatus.EXPIRED
                )  # window closed with quantity left: explicit, never silent
                o.params = {**o.params, "unfilled": o.quantity - filled}
            for ch in c:
                q = sum(x.quantity for x in f if x.child_order_id == ch.child_order_id)
                ch.status = str(
                    ChildStatus.FILLED
                    if q >= ch.quantity - 1e-9
                    else ChildStatus.CANCELLED
                    if q == 0
                    else ChildStatus.PARTIAL
                )
            children += c
            fills += f
        return DayResult(children, fills)

    # ------------------------------------------------------------------ T0
    def _t0(self, o: TraderOrder, i: int, j: int) -> tuple[list[ChildOrder], list[Fill]]:
        venue = self.md.primary[j]
        ch = self._child(
            o, venue, str(OrderType.MOC), o.quantity, minutes_from_open(time(15, 45)), str(Tif.AT_CLOSE), None
        )
        px = float(self.md.prc[i, j])
        f = self._fill(
            o,
            ch,
            390,
            px,
            o.quantity,
            str(Liquidity.AUCTION),
            str(MarketState.CLOSING_AUCTION),
            "T0_CLOSE",
            {"close": px, "mid": px},
            commission_bps=0.0,
        )
        f.fees_usd = 0.0
        return [ch], [f]

    def _cross(self, o: TraderOrder, i: int, j: int) -> tuple[list[ChildOrder], list[Fill]]:
        ch = self._child(
            o,
            "INTERNAL",
            str(OrderType.CROSS),
            o.quantity,
            minutes_from_open(time(15, 50)),
            str(Tif.AT_CLOSE),
            None,
        )
        px = float(self.md.prc[i, j])
        f = self._fill(
            o,
            ch,
            390,
            px,
            o.quantity,
            str(Liquidity.CROSS),
            str(MarketState.CLOSING_AUCTION),
            "NETTING_CROSS",
            {"close": px, "mid": px},
            commission_bps=0.0,
        )
        return [ch], [f]

    def _portfolio(self, o: TraderOrder, i: int, j: int) -> tuple[list[ChildOrder], list[Fill]]:
        venue = self.md.primary[j]
        ch = self._child(
            o, venue, str(OrderType.MOC), o.quantity, minutes_from_open(time(15, 45)), str(Tif.AT_CLOSE), None
        )
        px = float(self.md.prc[i, j])
        f = self._fill(
            o,
            ch,
            390,
            px,
            o.quantity,
            str(Liquidity.AUCTION),
            str(MarketState.CLOSING_AUCTION),
            f"{self.model}_PT_CLOSE_GUARANTEE",
            {"close": px, "mid": px},
        )
        f.fees_usd = 0.0
        return [ch], [f]

    def _auction(self, o: TraderOrder, i: int, j: int, q_agg: float) -> tuple[list[ChildOrder], list[Fill]]:
        md = self.md
        venue = md.primary[j]
        ch = self._child(
            o,
            venue,
            str(OrderType.MOC if o.strategy == Strategy.MOC else OrderType.LOC),
            o.quantity,
            minutes_from_open(time(15, 45)),
            str(Tif.AT_CLOSE),
            o.params.get("limit"),
        )
        flags = []
        p_auc = float(md.auc_price[i, j])
        if md.auc_price_fb[i, j] or not math.isfinite(p_auc):
            p_auc = float(md.prc[i, j])
            flags.append("AUCTION_FALLBACK_CRSP")
        v_auc = float(md.auc_vol[i, j])
        if md.auc_vol_fb[i, j]:
            flags.append("AUCVOL_FALLBACK_MEDIAN")
        s = side_sign(o.side)
        same = (md.imbalance[i, j] > 0 and s > 0) or (md.imbalance[i, j] < 0 and s < 0)
        dp = auction_impact_bps(q_agg, v_auc, self._sigma(i, j), bool(same), self.cost)
        px = p_auc * (1 + s * dp / 1e4)
        ref: dict[str, object] = {
            "auction_price": p_auc,
            "auction_volume": v_auc,
            "imbalance": float(md.imbalance[i, j]),
            "reference_price": round(float(md.prc[i, j] / (1 + md.dislocation_bps[i, j] / 1e4)), 4),
            "impact_bps": round(dp, 4),
            "q_over_auction": q_agg / v_auc if v_auc > 0 else None,
            "mid": p_auc,
            "flags": flags,
        }
        f = self._fill(
            o,
            ch,
            390,
            px,
            o.quantity,
            str(Liquidity.AUCTION),
            str(MarketState.CLOSING_AUCTION),
            f"{self.model}_AUCTION",
            ref,
        )
        return [ch], [f]

    # ------------------------------------------------------------------ continuous (T1)
    def _slices(
        self, o: TraderOrder, i: int, j: int, rng: np.random.Generator
    ) -> tuple[np.ndarray, np.ndarray, list[str]]:
        notional = o.quantity * float(self.md.prc[i, j])
        n = int(np.clip(round(3 + 4 * math.log10(1 + notional / 50_000)), 3, 30))
        n = int(min(n, max(1, o.quantity)))
        front = 2.5 if o.strategy == Strategy.ALGO_IS else 0.0
        t0, t1 = (
            o.start_ts.astimezone(self.intra.local(o.start_ts).tzinfo).time(),
            self.intra.local(o.end_ts).time(),
        )
        minutes = self.intra.schedule(t0, t1, n, front)
        base = np.floor(o.quantity / n)
        qty = np.full(n, base)
        qty[: int(o.quantity - base * n)] += 1
        keep = qty > 0
        venues = list(rng.choice(VENUES, n, p=self.md.venue_share[j]))
        return minutes[keep], qty[keep], [v for v, k in zip(venues, keep, strict=True) if k]

    def _t1_continuous(
        self, o: TraderOrder, i: int, j: int, rng: np.random.Generator
    ) -> tuple[list[ChildOrder], list[Fill]]:
        md = self.md
        s = side_sign(o.side)
        sig, adv, spr = self._sigma(i, j), self._adv(i, j), float(md.spread_bps[i, j])
        if o.strategy in (Strategy.ALGO_IS, Strategy.HIGH_TOUCH):
            imp = arrival_is_bps(o.quantity, adv, sig, spr, self.cost)
        else:
            imp = participation_impact_bps(o.quantity, adv, sig, self.cost)
        minutes, qty, venues = self._slices(o, i, j, rng)
        children, fills = [], []
        for m, q, v in zip(minutes, qty, venues, strict=True):
            liq = (
                Liquidity.MIDPOINT
                if v == "DARK"
                else (Liquidity.ADD if rng.random() < 0.45 else Liquidity.REMOVE)
            )
            otype = OrderType.MIDPOINT_PEG if v == "DARK" else OrderType.LIMIT
            ref = self._ref(i, j, float(m))
            px = ref["mid"] * (1 + s * imp / 1e4)
            ch = self._child(
                o, v, str(otype), float(q), float(m) - 0.05, str(Tif.DAY), round(px * (1 + s * 0.002), 2)
            )
            children.append(ch)
            fills.append(
                self._fill(
                    o,
                    ch,
                    float(m),
                    px,
                    float(q),
                    str(liq),
                    str(MarketState.CONTINUOUS_TRADING),
                    f"T1_{o.strategy}",
                    {**ref, "impact_bps": round(imp, 4)},
                )
            )
        return children, fills

    # ------------------------------------------------------------------ T2
    def _t2(
        self, o: TraderOrder, i: int, j: int, rng: np.random.Generator
    ) -> tuple[list[ChildOrder], list[Fill]]:
        md = self.md
        s = side_sign(o.side)
        curve = self.intra.minute_curve()
        cont = max(float(np.nan_to_num(md.volume[i, j])) - float(md.auc_vol[i, j]), 0.0)
        a = int(max(0, minutes_from_open(self.intra.local(o.start_ts).time())))
        b = int(min(390, math.ceil(minutes_from_open(self.intra.local(o.end_ts).time()))))
        w = curve[a:b] / curve[a:b].sum()
        target = np.cumsum(o.quantity * w)
        done = 0.0
        children: list[ChildOrder] = []
        fills: list[Fill] = []
        bucket: ChildOrder | None = None
        for k, m in enumerate(range(a, b)):
            cap = math.floor(self.cost.t2_interval_cap * cont * curve[m])
            want = math.floor(target[k] - done)
            q = float(min(max(want, 0), cap, o.quantity - done))
            if (m - a) % 5 == 0 or bucket is None:
                v = str(rng.choice(VENUES, p=md.venue_share[j]))
                bucket = self._child(
                    o,
                    v,
                    str(OrderType.MIDPOINT_PEG if v == "DARK" else OrderType.LIMIT),
                    float(min(o.quantity - done, max(cap, 1) * 5)),
                    m,
                    str(Tif.DAY),
                    None,
                )
                children.append(bucket)
            if q <= 0:
                continue
            behind = (target[k] - done - q) > 0 or want > cap
            ref = self._ref(i, j, m + 0.5)
            half = ref["ask"] - ref["mid"]
            liq = (
                Liquidity.MIDPOINT
                if bucket.venue_mic == "DARK"
                else Liquidity.REMOVE
                if behind
                else Liquidity.ADD
            )
            px = ref["mid"] + (s * half if liq == Liquidity.REMOVE else 0.0)
            fills.append(
                self._fill(
                    o,
                    bucket,
                    m + 0.5,
                    px,
                    q,
                    str(liq),
                    str(MarketState.CONTINUOUS_TRADING),
                    "T2_REPLAY",
                    {**ref, "interval_volume": cont * curve[m], "cap": cap},
                )
            )
            done += q
        # child quantities = what each child was actually worked for
        by_child: dict[str, float] = defaultdict(float)
        for f in fills:
            by_child[f.child_order_id] += f.quantity
        for ch in children:
            ch.quantity = (
                max(by_child.get(ch.child_order_id, 0.0), 1.0)
                if by_child.get(ch.child_order_id)
                else ch.quantity
            )
        return children, fills

    # ------------------------------------------------------------------ Phase 1 generator
    def _generator(
        self, o: TraderOrder, i: int, j: int, rng: np.random.Generator
    ) -> tuple[list[ChildOrder], list[Fill]]:
        """Price = VWAP x (1 + s c + xi): c injected cost, xi order-level noise (both in the answer key)."""
        key = o.params["answer_key"]
        s = side_sign(o.side)
        vwap = float(self.md.vwap[i, j])
        minutes, qty, venues = self._slices(o, i, j, rng)
        jit = rng.normal(0, 3.0, len(qty))
        jit -= (jit * qty).sum() / qty.sum()  # slice jitter nets to zero at order level
        children, fills = [], []
        for m, q, v, e in zip(minutes, qty, venues, jit, strict=True):
            liq = (
                Liquidity.MIDPOINT
                if v == "DARK"
                else (Liquidity.ADD if rng.random() < 0.45 else Liquidity.REMOVE)
            )
            px = vwap * (1 + s * (key["injected_cost_bps"] + key["noise_bps"] + e) / 1e4)
            ch = self._child(
                o,
                v,
                str(OrderType.MIDPOINT_PEG if v == "DARK" else OrderType.LIMIT),
                float(q),
                float(m) - 0.05,
                str(Tif.DAY),
                None,
            )
            children.append(ch)
            ref = self._ref(i, j, float(m))
            fills.append(
                self._fill(
                    o,
                    ch,
                    float(m),
                    round(px, 4),
                    float(q),
                    str(liq),
                    str(MarketState.CONTINUOUS_TRADING),
                    "GEN_VWAP",
                    {**ref, "vwap_day": vwap},
                )
            )
            fills[-1].price = round(px, 4)
        return children, fills


def end_of_window(o: TraderOrder) -> timedelta:
    return o.end_ts - o.start_ts
