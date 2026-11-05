"""Run lifecycle: submit -> prepare data -> simulate -> analyse -> DONE / FAILED.

A run is fully described by its config, the data snapshot hashes (lake snapshot, corporate-
action review set, desk policy) and the git SHA. ``output_hash`` hashes the business content
of NAV, orders and fills (not ids), so ``simp replay`` can prove bit-for-bit reproduction.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
import traceback
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import structlog
from sqlalchemy import select, update

from simp.analytics.fund import concentration_by_date, fund_report
from simp.analytics.tca import build_tca
from simp.config.run_config import FlowConfig, RunConfig
from simp.data.lake.store import Lake
from simp.data.market import load_market
from simp.db import schema as S
from simp.db.session import bulk_insert, clean_json, get_engine
from simp.desk.policy import DeskPolicy, load_policy
from simp.domain.enums import RunStatus
from simp.domain.ids import run_id_for
from simp.errors import DataMissingError, SimpError
from simp.runs.engine import Engine, EngineOutput
from simp.settings import get_settings

log = structlog.get_logger(__name__)


def code_version() -> str:
    v = os.environ.get("SIMP_CODE_VERSION")
    if v:
        return v
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,  # noqa: S607
                             cwd=Path(__file__).parent).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "."], capture_output=True, text=True,  # noqa: S607
                               cwd=Path(__file__).parents[1]).stdout.strip()
        return sha + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unversioned"


def _now() -> datetime:
    return datetime.now(UTC)


def emit(run_id: str, kind: str, payload: dict[str, Any]) -> None:
    with get_engine().begin() as c:
        c.execute(S.run_event.insert().values(run_id=run_id, ts=_now(), kind=kind, payload=clean_json(payload)))


def _set(run_id: str, **vals: Any) -> None:
    with get_engine().begin() as c:
        c.execute(update(S.run).where(S.run.c.run_id == run_id).values(**{k: clean_json(v) if isinstance(v, dict) else v for k, v in vals.items()}))


# ---------------------------------------------------------------------------- inputs
def ca_reviews() -> tuple[dict[tuple[int, str, str], str], str]:
    with get_engine().connect() as c:
        rows = c.execute(select(S.corporate_action.c.permno, S.corporate_action.c.ex_date, S.corporate_action.c.type,
                                S.corporate_action.c.review_status)).all()
    d = {(int(p), e.isoformat(), t): s for p, e, t, s in rows}
    h = hashlib.sha256(json.dumps(sorted((f"{k[0]}|{k[1]}|{k[2]}", v) for k, v in d.items())).encode()).hexdigest()
    return d, h


def desk_policy_for(name: str) -> tuple[DeskPolicy, str]:
    with get_engine().connect() as c:
        row = c.execute(select(S.desk_policy.c.yaml).where(S.desk_policy.c.name == name)).first()
    if row:
        text = row[0]
        pol = load_policy(text)
    else:
        from simp.desk.policy import POLICY_DIR

        text = (POLICY_DIR / f"{name}.yaml").read_text()
        pol = load_policy(name)
    return pol, hashlib.sha256(text.encode()).hexdigest()


def latest_snapshot() -> str:
    snaps = Lake().snapshots()
    if not snaps:
        raise DataMissingError("no data snapshot in the lake — run `simp data pull` (or Data -> Pull) first")
    return str(snaps[-1]["snapshot_hash"])


# ---------------------------------------------------------------------------- submit
def submit(cfg: RunConfig, user: str = "cli", *, enqueue: bool = True) -> str:
    if cfg.data_snapshot is None:
        cfg = cfg.model_copy(update={"data_snapshot": latest_snapshot()})
    Lake().snapshot(cfg.data_snapshot or "")  # raises if missing
    h = cfg.config_hash()
    run_id = run_id_for(h, f"{time.time_ns()}:{user}")
    with get_engine().begin() as c:
        if c.execute(select(S.fund.c.fund_id).where(S.fund.c.fund_id == cfg.fund_id)).first() is None:
            c.execute(S.fund.insert().values(
                fund_id=cfg.fund_id, name=cfg.name, strategy_type="SYNTHETIC" if cfg.strategy == "synthetic_generator" else "INDEX",
                base_ccy="USD", benchmark_id=cfg.benchmark, inception_date=cfg.window.bootstrap, initial_aum=cfg.initial_aum,
                cash_buffer_bps=cfg.flows.cash_buffer_bps, fee_bps=cfg.fee_bps))
        c.execute(S.run.insert().values(
            run_id=run_id, fund_id=cfg.fund_id, name=cfg.label or cfg.name, config=json.loads(cfg.model_dump_json()),
            config_hash=h, code_version=code_version(), status=RunStatus.QUEUED, stage="queued", progress=0.0,
            submitted_by=user, submitted_at=_now(), study_id=cfg.study_id, study_step=cfg.study_step))
    emit(run_id, "stage", {"stage": "queued", "config_hash": h})
    if enqueue:
        dispatch(run_id)
    return run_id


def dispatch(run_id: str) -> None:
    if get_settings().queue == "inline":
        threading.Thread(target=execute_safely, args=(run_id,), daemon=True, name=f"run-{run_id}").start()
    else:
        from simp.runs.worker import run_actor

        run_actor.send(run_id)


def execute_safely(run_id: str) -> None:
    try:
        execute(run_id)
    except Exception:  # noqa: BLE001 - recorded on the run row by execute(); the thread has nowhere to re-raise
        log.error("run.failed", run_id=run_id)


# ---------------------------------------------------------------------------- execute
def execute(run_id: str) -> str:
    structlog.contextvars.bind_contextvars(run_id=run_id)
    with get_engine().connect() as c:
        row = c.execute(select(S.run).where(S.run.c.run_id == run_id)).mappings().one()
    cfg = RunConfig.model_validate(row["config"])
    session_box: dict[str, str | None] = {"session": None}
    t0 = time.time()
    try:
        _set(run_id, status=RunStatus.PREPARING, stage="data_check", started_at=_now())
        emit(run_id, "stage", {"stage": "data_check"})
        lake = Lake()
        snap = cfg.data_snapshot or latest_snapshot()
        lake.verify_snapshot(snap)
        md = load_market(snap, str(lake.root))
        reviews, rh = ca_reviews()
        policy, ph = desk_policy_for(cfg.desk_policy)
        policy = _patched_policy(policy, cfg)
        hashes = {"lake_snapshot": snap, "ca_reviews": rh, "desk_policy": ph, "data_label": md.data_label}
        _set(run_id, data_snapshot_hashes=hashes, data_label=md.data_label, stage="mapping")
        emit(run_id, "stage", {"stage": "mapping"})
        coverage = mapping_coverage(md)
        emit(run_id, "log", {"msg": f"BMLL mapping: {coverage['mapped_names']}/{coverage['names']} names mapped, "
                                    f"{coverage['unmapped_member_weight']:.4%} of index weight unmapped"})
        _set(run_id, status=RunStatus.RUNNING, stage="simulate")
        emit(run_id, "stage", {"stage": "simulate"})

        last = [0.0]

        def progress(p: dict[str, Any]) -> None:
            session_box["session"] = p["session"]
            frac = p["i"] / max(p["n"] - 1, 1)
            if time.time() - last[0] > 0.4 or frac >= 1:
                last[0] = time.time()
                _set(run_id, progress=frac)
                emit(run_id, "progress", p)

        eng = Engine(cfg, md, run_id, progress=progress, ca_reviews=reviews, policy=policy)
        out = eng.run()
        t_sim = time.time() - t0
        _set(run_id, status=RunStatus.ANALYSING, stage="analyse", progress=1.0)
        emit(run_id, "stage", {"stage": "analyse", "sim_seconds": round(t_sim, 2)})
        tca_rows, rep_rows = build_tca(out, md, cfg.cost, str(cfg.timing.rule))
        persist(run_id, out, tca_rows, rep_rows)
        audit = lineage_audit(run_id)
        nav = [asdict(r) for r in out.nav]
        summary = build_summary(cfg, out, nav, tca_rows, coverage, audit, md.data_label)
        summary["timing"] = {"simulate_s": round(t_sim, 2), "total_s": round(time.time() - t0, 2)}
        oh = output_hash(out)
        _set(run_id, status=RunStatus.DONE, stage="done", finished_at=_now(), summary=summary, output_hash=oh)
        emit(run_id, "done", {"output_hash": oh, "seconds": round(time.time() - t0, 2)})
        log.info("run.done", output_hash=oh, seconds=round(time.time() - t0, 2))
        return oh
    except Exception as e:
        err = {"type": type(e).__name__, "message": str(e), "session": str(getattr(e, "session", None) or session_box["session"]),
               "expected": isinstance(e, SimpError), "traceback": traceback.format_exc()[-6000:]}
        _set(run_id, status=RunStatus.FAILED, stage="failed", finished_at=_now(), error=err)
        emit(run_id, "error", err)
        raise
    finally:
        structlog.contextvars.unbind_contextvars("run_id")


def _patched_policy(policy: DeskPolicy, cfg: RunConfig) -> DeskPolicy:
    share = cfg.cost.model_dump().get("moc_auction_share")
    if share is None:
        return policy
    data = policy.model_dump()
    for r in data["rules"]:
        if r["when"].get("max_pct_expected_auction") is not None:
            r["when"]["max_pct_expected_auction"] = share
        for leg in r["then"]:
            if leg.get("pct_expected_auction") is not None:
                leg["pct_expected_auction"] = share
    return DeskPolicy.model_validate(data)


def mapping_coverage(md: Any) -> dict[str, Any]:
    names = int(md.N)
    mapped = int(md.mapped.sum())
    last = md.D - 1
    cap = np.nan_to_num(md.shrout * md.prc)
    mem = md.member
    wt = (cap * mem * (~md.mapped)[None, :]).sum(1) / np.maximum((cap * mem).sum(1), 1)
    return {"names": names, "mapped_names": mapped, "unmapped_permnos": [int(p) for p in md.permnos[~md.mapped]],
            "unmapped_member_weight": float(np.nanmax(wt)), "unmapped_member_weight_last": float(wt[last])}


# ---------------------------------------------------------------------------- persistence
def persist(run_id: str, out: EngineOutput, tca_rows: list[dict[str, Any]], rep_rows: list[dict[str, Any]]) -> None:
    eng = get_engine()
    bulk_insert(eng, S.decision, ["decision_id", "run_id", "fund_id", "decision_type", "snapshot_date", "effective_date", "decision_ts",
                                  "target_weights_ref"],
                [(d.decision_id, d.run_id, d.fund_id, d.decision_type, d.snapshot_date, d.effective_date, d.decision_ts,
                  d.target_weights_ref) for d in out.decisions])
    bulk_insert(eng, S.target_weight, ["run_id", "decision_id", "permno", "weight", "weight_source", "excluded_flag", "exclusion_reason"],
                [(t.run_id, t.decision_id, t.permno, t.weight, t.weight_source, t.excluded_flag, t.exclusion_reason) for t in out.target_weights])
    cols = ["pm_order_id", "run_id", "fund_id", "decision_id", "permno", "listing_id", "side", "quantity", "reason", "decision_ts",
            "decision_price", "decision_price_source", "instruction", "deadline_ts", "urgency", "limit_price", "status", "created_by",
            "exec_date", "quantity_adjustment_factor", "filled_qty", "answer_key", "flags"]
    bulk_insert(eng, S.pm_order, cols, [tuple(getattr(o, c) for c in cols) for o in out.pm_orders])
    bulk_insert(eng, S.program, ["program_id", "run_id", "broker_id", "trade_date", "n_orders", "notional", "fee_bps"],
                [(p.program_id, p.run_id, p.broker_id, p.trade_date, p.n_orders, p.notional, p.fee_bps) for p in out.programs])
    cols = ["trader_order_id", "pm_order_id", "run_id", "desk_id", "strategy", "program_id", "broker_id", "quantity", "filled_qty",
            "start_ts", "end_ts", "arrival_price", "params", "status", "commission_bps"]
    bulk_insert(eng, S.trader_order, cols, [tuple(getattr(t, c) for c in cols) for t in out.trader_orders])
    cols = ["child_order_id", "trader_order_id", "run_id", "broker_id", "venue_mic", "order_type", "quantity", "limit_price", "sent_ts",
            "tif", "status"]
    bulk_insert(eng, S.child_order, cols, [tuple(getattr(c_, c) for c in cols) for c_ in out.children])
    cols = ["fill_id", "child_order_id", "trader_order_id", "pm_order_id", "run_id", "broker_id", "venue_mic", "exec_ts", "price",
            "quantity", "liquidity", "market_state", "fees_usd", "commission_usd", "fill_model", "ref_snapshot", "permno", "side"]
    bulk_insert(eng, S.fill, cols, [tuple(getattr(f, c) for c in cols) for f in out.fills])
    cols = ["entry_id", "run_id", "fund_id", "ts", "account", "permno", "amount", "quantity", "ref_type", "ref_id"]
    bulk_insert(eng, S.ledger_entry, cols, [tuple(getattr(e, c) for c in cols) for e in out.ledger])
    cols = ["flow_id", "run_id", "fund_id", "date", "amount_usd", "units", "unit_nav_used", "generator_seed", "is_jump"]
    bulk_insert(eng, S.flow, cols, [tuple(getattr(f, c) for c in cols) for f in out.flows])
    cols = ["run_id", "fund_id", "date", "cash", "equity_value", "receivables", "payables", "nav", "units", "unit_nav", "fund_return",
            "gross_return", "flow_usd", "bench_return_tr", "bench_return_pr", "bench_return_official", "cash_weight", "fees_usd",
            "trading_cost_usd"]
    bulk_insert(eng, S.nav_daily, cols, [tuple(getattr(r, c) for c in cols) for r in out.nav])
    bulk_insert(eng, S.position_daily, ["run_id", "date", "permno", "shares", "close_price", "market_value", "split_factor_applied"],
                out.positions)
    bulk_insert(eng, S.netting, ["run_id", "date", "permno", "crossed_shares", "saved_notional", "reasons"],
                [(run_id, n["date"], n["permno"], n["crossed_shares"], n["saved_notional"], n["reasons"]) for n in out.netting])
    bulk_insert(eng, S.ca_event, ["run_id", "date", "permno", "kind", "shares", "amount", "factor", "booked", "provisional"],
                [(run_id, e["date"], e["permno"], e["kind"], e["shares"], e["amount"], e["factor"], e["booked"], e.get("provisional", False))
                 for e in out.ca_events])
    if tca_rows:
        cols = list(tca_rows[0].keys())
        bulk_insert(eng, S.tca_order, cols, [tuple(r[c] for c in cols) for r in tca_rows])
    if rep_rows:
        cols = list(rep_rows[0].keys())
        bulk_insert(eng, S.reprice, cols, [tuple(r[c] for c in cols) for r in rep_rows])


def lineage_audit(run_id: str) -> dict[str, Any]:
    """Spec: 100% of fills link to a trader order and a PM order; quantities never overrun;
    timestamps run forward."""
    q = {
        "fills": "select count(*) from fill where run_id=:r",
        "orphan_fills": "select count(*) from fill f left join trader_order t using (trader_order_id) left join pm_order p "
                        "on p.pm_order_id=t.pm_order_id where f.run_id=:r and (t.trader_order_id is null or p.pm_order_id is null)",
        "fill_overruns": "select count(*) from (select t.trader_order_id from trader_order t join fill f using (trader_order_id) "
                         "where t.run_id=:r group by t.trader_order_id, t.quantity having sum(f.quantity) > t.quantity + 1e-6) x",
        "trader_overruns": "select count(*) from (select p.pm_order_id from pm_order p join trader_order t using (pm_order_id) "
                           "where p.run_id=:r group by p.pm_order_id, p.quantity, p.quantity_adjustment_factor "
                           "having sum(t.quantity) > p.quantity * p.quantity_adjustment_factor + 1e-6) x",
        "ts_violations": "select count(*) from fill f join child_order c using (child_order_id) join trader_order t "
                         "on t.trader_order_id=f.trader_order_id join pm_order p on p.pm_order_id=t.pm_order_id where f.run_id=:r and "
                         "not (p.decision_ts <= t.start_ts and t.start_ts <= c.sent_ts and c.sent_ts <= f.exec_ts and f.exec_ts <= t.end_ts)",
        "unexplained_leftovers": "select count(*) from trader_order where run_id=:r and filled_qty < quantity - 1e-6 "
                                 "and status not in ('CANCELLED','EXPIRED')",
    }
    from sqlalchemy import text

    with get_engine().connect() as c:
        res = {k: int(c.execute(text(v), {"r": run_id}).scalar() or 0) for k, v in q.items()}
    res["lineage_complete"] = res["orphan_fills"] == 0
    res["passed"] = all(res[k] == 0 for k in ("orphan_fills", "fill_overruns", "trader_overruns", "ts_violations", "unexplained_leftovers"))
    if not res["passed"]:
        from simp.errors import OrderInvariantError

        raise OrderInvariantError(f"order-chain audit failed: {res}")
    return res


def build_summary(cfg: RunConfig, out: EngineOutput, nav: list[dict[str, Any]], tca_rows: list[dict[str, Any]],
                  coverage: dict[str, Any], audit: dict[str, Any], label: str) -> dict[str, Any]:
    import polars as pl

    from simp.analytics.aggregate import cost_frame, fallback_report, summarise

    fr = fund_report(nav, cfg.window.start, cfg.benchmark) if cfg.strategy == "index_replication" else {"available": False}
    tca = pl.DataFrame(tca_rows, infer_schema_length=None) if tca_rows else pl.DataFrame()
    tca_sum: dict[str, Any] = {}
    if tca.height:
        empty = pl.DataFrame({"pm_order_id": []})
        for m in ("exec", "is", "slip_benchmark"):
            tca_sum[m] = summarise(cost_frame(tca, empty, "ACTUAL", m), n_boot=500)
        tca_sum["fallbacks"] = fallback_report(tca)
        traded = float(tca["exec_notional"].sum())
        unm = set(coverage["unmapped_permnos"])
        un_traded = float(tca.filter(pl.col("permno").is_in(list(unm)))["exec_notional"].sum()) if unm else 0.0
        coverage["traded_notional_mapped_share"] = 1 - un_traded / traded if traded else 1.0
    avg_nav = float(np.mean([r["nav"] for r in nav])) if nav else 0.0
    saved = float(sum(n["saved_notional"] for n in out.netting))
    prov = [e for e in out.ca_events if e["kind"] == "CD" and e.get("provisional")]
    summ = {
        "data_label": label, "fund": fr, "tca": tca_sum, "mapping": coverage, "audit": audit,
        "counts": {"decisions": len(out.decisions), "pm_orders": len(out.pm_orders), "trader_orders": len(out.trader_orders),
                   "child_orders": len(out.children), "fills": len(out.fills), "flows": len(out.flows), "programs": len(out.programs),
                   "ledger_entries": len(out.ledger)},
        "netting": {"events": len(out.netting), "saved_notional": saved, "saved_bps_of_nav": saved / avg_nav * 1e4 if avg_nav else 0.0},
        "excluded_weight": {str(k): v for k, v in sorted(out.excluded_weight.items())},
        "flags": out.flags,
        "provisional_dividends": {"events": len(prov), "booked": sum(1 for e in prov if e["booked"]),
                                  "usd": float(sum(e["amount"] for e in prov)),
                                  "by_security": _by_sec(prov)},
        "concentration": concentration_by_date(tca_rows) if tca_rows else [],
    }
    return clean_json(summ)  # type: ignore[no-any-return]


def _by_sec(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    agg: dict[int, dict[str, Any]] = {}
    for e in events:
        a = agg.setdefault(e["permno"], {"permno": e["permno"], "events": 0, "usd": 0.0})
        a["events"] += 1
        a["usd"] += float(e["amount"])
    return sorted(agg.values(), key=lambda x: -x["usd"])


def output_hash(out: EngineOutput) -> str:
    h = hashlib.sha256()
    for r in out.nav:
        h.update(f"{r.date}|{r.nav:.6f}|{r.units:.8f}|{r.cash:.6f}\n".encode())
    for o in out.pm_orders:
        h.update(f"{o.exec_date}|{o.permno}|{o.side}|{o.quantity:.4f}|{o.reason}|{o.status}|{o.filled_qty:.4f}\n".encode())
    for f in sorted(out.fills, key=lambda f: (f.exec_ts, f.permno, f.side, f.quantity, f.price, f.venue_mic)):
        h.update(f"{f.exec_ts.isoformat()}|{f.permno}|{f.side}|{f.quantity:.4f}|{f.price:.6f}|{f.venue_mic}|{f.fill_model}\n".encode())
    return h.hexdigest()


# ---------------------------------------------------------------------------- studies
CHAIN_STEPS = [
    ("baseline: handoff timing, T0, no flows, no fee", {"timing": {"rule": "HANDOFF"}, "execution_model": "T0",
                                                        "flows": FlowConfig.from_preset("none").model_dump(), "fee_bps": 0.0}),
    ("+ effective-date timing", {"timing": {"rule": "EFFECTIVE_DATE"}}),
    ("+ investor flows", {"flows": FlowConfig().model_dump()}),
    ("+ execution costs (T1)", {"execution_model": "T1"}),
    ("+ management fee", {"fee_bps": 3.0}),
    ("+ keep securities with approved corporate actions", {"exclusion_policy": "keep_securities"}),
]


def _merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def sensitivity_steps(kind: str) -> list[tuple[str, dict[str, Any]]]:
    if kind == "dividend_rule":
        return [("provisional dividend rule ON", {"provisional_dividends": True}), ("provisional dividend rule OFF", {"provisional_dividends": False})]
    if kind == "exclusions":
        return [("exclude_securities", {"exclusion_policy": "exclude_securities"}), ("keep_securities", {"exclusion_policy": "keep_securities"})]
    if kind == "cost_params":
        steps: list[tuple[str, dict[str, Any]]] = [("base", {})]
        for p in ("Y", "kappa", "beta"):
            for m in (0.5, 1.5):
                base = {"Y": 0.7, "kappa": 0.5, "beta": 0.5}[p]
                steps.append((f"{p} x{m}", {"cost": {p: base * m}}))
        for share in (0.02, 0.10):
            steps.append((f"MOC auction share {share:.0%}", {"cost": {"moc_auction_share": share}}))
        return steps
    if kind == "flow_regime":
        return [(f"sigma {s} bps, buffer {b} bps", {"flows": {"sigma_bps": float(s), "cash_buffer_bps": float(b)}})
                for s in (5, 10, 25) for b in (10, 50)]
    raise ValueError(f"unknown sensitivity {kind}")


def submit_study(kind: str, base: RunConfig, user: str, name: str | None = None) -> tuple[str, list[str]]:
    from simp.domain.ids import ulid_from

    sid = ulid_from(_now(), int.from_bytes(hashlib.sha256(f"{kind}{time.time_ns()}".encode()).digest()[:10], "big"))
    if kind == "counterfactual_chain":
        steps, cumulative = CHAIN_STEPS, True
    else:
        steps, cumulative = sensitivity_steps(kind), False
    with get_engine().begin() as c:
        c.execute(S.study.insert().values(study_id=sid, kind=kind, name=name or kind.replace("_", " "),
                                          base_config=json.loads(base.model_dump_json()),
                                          steps=[{"label": lbl, "overrides": ov} for lbl, ov in steps], created_by=user, created_at=_now()))
    run_ids = []
    cfgd = json.loads(base.model_dump_json())
    for k, (lbl, ov) in enumerate(steps):
        cfgd = _merge(cfgd, ov) if cumulative else _merge(json.loads(base.model_dump_json()), ov)
        cfgd.update({"study_id": sid, "study_step": k, "label": f"{name or kind}: {lbl}"})
        run_ids.append(submit(RunConfig.model_validate(cfgd), user))
    return sid, run_ids
