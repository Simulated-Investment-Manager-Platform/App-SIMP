r"""Order-level TCA (spec: TCA metrics per order) and the three-way repricing.

IS = delay + execution + explicit + opportunity, all x 1e4 bps, s = +1 buy / -1 sell:
  delay       = s (P_a - P_d) / P_d
  execution   = s (P_x - P_a) / P_d
  explicit    = (fees + commission) / (Q_x P_d)
  opportunity = s (Q_u / Q) (P_end - P_d) / P_d
Prices are compared on the decision-date share basis (split factor between decision and
execution applied), as each record keeps its own basis.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import time
from typing import Any

import numpy as np

from simp.analytics.weighted import AUC_EDGES, AUC_LABELS, SIZE_EDGES, SIZE_LABELS, bucket
from simp.config.run_config import CostModelParams
from simp.data.market import MarketData
from simp.domain.enums import AUCTION_STRATEGIES, Strategy, TimingRule, side_sign
from simp.execution.impact import arrival_is_bps, auction_impact_bps, participation_impact_bps
from simp.execution.intraday import IntradayService, minutes_from_open
from simp.runs.engine import EngineOutput

BENCH_OF = {
    "MOC": "close", "LOC": "close", "PORTFOLIO_TRADE": "close", "INTERNAL_CROSS": "close",
    "ALGO_VWAP": "vwap", "ALGO_TWAP": "vwap", "ALGO_POV": "vwap", "ALGO_IS": "arrival", "HIGH_TOUCH": "arrival",
}
MARKOUTS = (1, 5, 60, 300)


def split_factor(md: MarketData, j: int, i_from: int, i_to: int) -> float:
    f = 1.0
    for a in md.actions:
        if a.j == j and a.kind == "FS" and i_from < a.ex_i <= i_to:
            f *= a.factor
    return f


def _bps(x: float) -> float:
    return float(x * 1e4) if math.isfinite(x) else float("nan")


def build_tca(out: EngineOutput, md: MarketData, cost: CostModelParams, timing: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    intra = IntradayService(md)
    cal = md.cal
    trader_by_pm: dict[str, list[Any]] = defaultdict(list)
    for t in out.trader_orders:
        trader_by_pm[t.pm_order_id].append(t)
    fills_by_pm: dict[str, list[Any]] = defaultdict(list)
    for f in out.fills:
        fills_by_pm[f.pm_order_id].append(f)
    decision_type = {d.decision_id: d.decision_type for d in out.decisions}
    # aggregate same-side quantity per (date, name) for auction-impact repricing
    agg: dict[tuple[int, int, int], float] = defaultdict(float)
    for o in out.pm_orders:
        if o.status == "CANCELLED" and o.filled_qty == 0:
            continue
        agg[(cal.index(o.exec_date), md.j(o.permno), side_sign(o.side))] += o.quantity * o.quantity_adjustment_factor

    rows: list[dict[str, Any]] = []
    reprices: list[dict[str, Any]] = []
    for o in out.pm_orders:
        fills = fills_by_pm.get(o.pm_order_id, [])
        if not fills and o.filled_qty == 0 and o.status == "CANCELLED":
            continue
        j = md.j(o.permno)
        i = cal.index(o.exec_date)
        s = side_sign(o.side)
        f_adj = o.quantity_adjustment_factor
        Pd = float(o.decision_price)
        q_exec_total = o.quantity * f_adj
        qx_exec = sum(x.quantity for x in fills)
        notional_x = sum(x.quantity * x.price for x in fills)
        fees = sum(x.fees_usd for x in fills)
        comm = sum(x.commission_usd for x in fills)
        px_exec = notional_x / qx_exec if qx_exec else float("nan")
        traders = trader_by_pm.get(o.pm_order_id, [])
        tq = sum(t.quantity for t in traders)
        pa_exec = sum(t.arrival_price * t.quantity for t in traders) / tq if tq else float("nan")
        close = float(md.prc[i, j])
        vwap_day = float(md.vwap[i, j])
        # interval VWAP over the union of algo windows (notional-weighted), else day VWAP
        iv_num = iv_den = 0.0
        for t in traders:
            if t.strategy not in AUCTION_STRATEGIES:
                v, _ = intra.interval_vwap(i, j, t.start_ts.astimezone(intra.local(t.start_ts).tzinfo).time(),
                                           intra.local(t.end_ts).time())
                iv_num += v * t.quantity
                iv_den += t.quantity
        vwap_int = iv_num / iv_den if iv_den else vwap_day
        Px = px_exec * f_adj
        Pa = pa_exec * f_adj
        Pend = close * f_adj
        Q = o.quantity
        Qx = qx_exec / f_adj
        Qu = max(Q - Qx, 0.0)
        delay = s * (Pa - Pd) / Pd if Qx else float("nan")
        execution = s * (Px - Pa) / Pd if Qx else float("nan")
        explicit = (fees + comm) / (Qx * Pd) if Qx else float("nan")
        opp = s * (Qu / Q) * (Pend - Pd) / Pd
        isv = np.nansum([delay, execution, explicit]) + opp if Qx else opp
        main = max(traders, key=lambda t: t.quantity) if traders else None
        strat = main.strategy if main else ""
        bench = BENCH_OF.get(strat, "close")
        slip_close = s * (px_exec - close) / close if qx_exec else float("nan")
        slip_vwap = s * (px_exec - vwap_day) / vwap_day if qx_exec else float("nan")
        slip_arr = s * (px_exec - pa_exec) / pa_exec if qx_exec else float("nan")
        slip_bench = {"close": slip_close, "vwap": s * (px_exec - vwap_int) / vwap_int if qx_exec else float("nan"),
                      "arrival": slip_arr}[bench]
        adv = float(md.adv20[i, j])
        v_auc = float(md.auc_vol[i, j])
        med = float(md.auc_med20[i, j])
        pct_adv = q_exec_total / adv if adv and math.isfinite(adv) else float("nan")
        pct_auc = q_exec_total / v_auc if v_auc else float("nan")
        pct_exp = q_exec_total / med if med and math.isfinite(med) else float("nan")
        auc_q = sum(x.quantity for x in fills if x.liquidity in ("AUCTION", "CROSS"))
        cont = [x for x in fills if x.liquidity not in ("AUCTION", "CROSS")]
        cont_q = sum(x.quantity for x in cont)
        passive_q = sum(x.quantity for x in cont if x.liquidity in ("ADD", "MIDPOINT"))
        mk: dict[int, float] = {}
        for dt_s in MARKOUTS:
            num = den = 0.0
            for x in cont:
                sec = minutes_from_open(intra.local(x.exec_ts).time()) * 60
                m0 = intra.mid_at_seconds(i, j, sec)
                m1 = intra.mid_at_seconds(i, j, min(sec + dt_s, 23400))
                num += s * (m1 - m0) / m0 * x.quantity * x.price
                den += x.quantity * x.price
            mk[dt_s] = _bps(num / den) if den else float("nan")
        nxt_open = float("nan")
        if auc_q and i + 1 < md.D and math.isfinite(float(md.opn[i + 1, j])):
            nxt_open = s * (float(md.opn[i + 1, j]) * split_factor(md, j, i, i + 1) - close) / close
        flags = sorted({fl for x in fills for fl in (x.ref_snapshot.get("flags") or [])} | set(o.flags))
        venues: dict[str, float] = defaultdict(float)
        for x in fills:
            venues[x.venue_mic] += x.quantity
        imb = float(md.imbalance[i, j])
        rows.append(dict(
            run_id=o.run_id, pm_order_id=o.pm_order_id, date=o.exec_date, permno=o.permno, ticker=md.tickers[j], side=o.side,
            reason=o.reason, instruction=o.instruction, main_strategy=strat, main_broker=main.broker_id if main else None,
            main_venue=max(venues, key=lambda k: venues[k]) if venues else None,
            quantity=Q, filled_qty=Qx, notional=Q * Pd, exec_notional=notional_x, decision_price=Pd,
            arrival_price=Pa, avg_price=Px, close_price=Pend, vwap_day=vwap_day * f_adj, vwap_interval=vwap_int * f_adj,
            is_bps=_bps(isv), delay_bps=_bps(delay), execution_bps=_bps(execution), explicit_bps=_bps(explicit),
            opportunity_bps=_bps(opp), exec_cost_bps=_bps(np.nansum([execution, explicit])) if Qx else float("nan"),
            slip_close_bps=_bps(slip_close), slip_vwap_bps=_bps(slip_vwap), slip_arrival_bps=_bps(slip_arr),
            slip_benchmark_bps=_bps(slip_bench), pct_adv=pct_adv, pct_auction=pct_auc, pct_exp_auction=pct_exp,
            spread_bps=float(md.spread_bps[i, j]), vol_intraday=float(md.vol_intraday[i, j]),
            imbalance_side="BUY" if imb > 0 else "SELL" if imb < 0 else None, imbalance_notional=abs(imb) * close,
            size_bucket=str(bucket(np.array([pct_adv]), SIZE_EDGES, SIZE_LABELS)[0]),
            auction_bucket=str(bucket(np.array([pct_auc]), AUC_EDGES, AUC_LABELS)[0]),
            auction_share=auc_q / qx_exec if qx_exec else float("nan"),
            passive_share=passive_q / cont_q if cont_q else float("nan"),
            markout_1s=mk[1], markout_5s=mk[5], markout_60s=mk[60], markout_300s=mk[300],
            next_open_vs_close_bps=_bps(nxt_open), fees_usd=fees, commission_usd=comm,
            cost_usd=(np.nansum([execution, explicit]) * Qx * Pd) if Qx else 0.0, flags=flags, answer_key=o.answer_key,
        ))
        # ------------------------------------------------ three-way repricing (+ T0 and handoff)
        q = q_exec_total
        sig = float(np.nan_to_num(md.vol_intraday[i, j], nan=0.015))
        p_auc = float(md.auc_price[i, j]) if math.isfinite(float(md.auc_price[i, j])) else close
        same = (imb > 0 and s > 0) or (imb < 0 and s < 0)
        moc = p_auc * (1 + s * auction_impact_bps(agg[(i, j, s)], v_auc, sig, bool(same), cost) / 1e4)
        vw = vwap_day * (1 + s * participation_impact_bps(q, adv if math.isfinite(adv) else 0.0, sig, cost) / 1e4)
        arr_mid = intra.price_at(i, j, minutes_from_open(time(9, 31)))
        arr = arr_mid * (1 + s * arrival_is_bps(q, adv if math.isfinite(adv) else 0.0, sig, float(md.spread_bps[i, j]), cost) / 1e4)
        dtype = decision_type.get(o.decision_id, "")
        if dtype in ("QUARTERLY_REBALANCE",) and timing == TimingRule.EFFECTIVE_DATE:
            hi = min(i + 2, md.D - 1)
        elif timing == TimingRule.HANDOFF:
            hi = i
        else:
            hi = min(cal.index(cal.on_or_after(o.decision_ts.astimezone(intra.local(o.decision_ts).tzinfo).date())) + 1, md.D - 1)
            hi = max(hi, i) if dtype.startswith("FLOW") else hi
        hp = float(md.prc[hi, j]) * split_factor(md, j, i, hi) if math.isfinite(float(md.prc[hi, j])) else float("nan")
        reprices.append(dict(
            run_id=o.run_id, pm_order_id=o.pm_order_id, date=o.exec_date,
            t0_price=close * f_adj, t0_bps=_bps(s * (close * f_adj - Pd) / Pd),
            moc_price=moc * f_adj, moc_bps=_bps(s * (moc * f_adj - Pd) / Pd), moc_vs_close_bps=_bps(s * (moc - close) / close),
            vwap_price=vw * f_adj, vwap_bps=_bps(s * (vw * f_adj - Pd) / Pd),
            arrival_price=arr * f_adj, arrival_bps=_bps(s * (arr * f_adj - Pd) / Pd),
            handoff_price=hp * f_adj, handoff_bps=_bps(s * (hp * f_adj - Pd) / Pd), notional=Q * Pd,
        ))
    return rows, reprices


def strategy_is_auction(s: str) -> bool:
    return s in {str(x) for x in AUCTION_STRATEGIES} or s == Strategy.MOC
