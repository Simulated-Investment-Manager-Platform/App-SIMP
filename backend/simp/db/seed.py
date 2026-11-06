"""Reference data and review queues seeded into Postgres."""

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl
from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from simp.data.lake.store import Lake
from simp.data.market import classify_distribution
from simp.db import schema as S
from simp.db.session import get_engine, require_schema_at_head
from simp.desk.policy import POLICY_DIR
from simp.desk.reference import BROKERS, DESKS, VENUES
from simp.settings import get_settings

FUNDS = [
    dict(
        fund_id="SP500_INDEX",
        name="S&P 500 index fund",
        strategy_type="INDEX",
        base_ccy="USD",
        benchmark_id="CRSP_VWRETD",
        inception_date=datetime(2022, 6, 21).date(),
        initial_aum=10_000_000_000.0,
        cash_buffer_bps=10.0,
        fee_bps=3.0,
        description="The four-year S&P 500 index fund from the handoff (Jul 2022 - Jun 2026), CRSP cap-weight proxy.",
    ),
    dict(
        fund_id="SYNTH_GENERATOR",
        name="Phase 1 order generator",
        strategy_type="SYNTHETIC",
        base_ccy="USD",
        benchmark_id="CRSP_VWRETD",
        inception_date=datetime(2026, 1, 2).date(),
        initial_aum=10_000_000_000.0,
        cash_buffer_bps=0.0,
        fee_bps=0.0,
        description="Random multi-MIC order chains priced around BMLL VWAP with a known-answer key.",
    ),
]


def _upsert(engine: Engine, table: S.Table, rows: list[dict[str, object]], keys: list[str]) -> None:  # type: ignore[name-defined]
    if not rows:
        return
    with engine.begin() as c:
        stmt = pg_insert(table).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=keys)
        c.execute(stmt)


def seed_reference(engine: Engine | None = None) -> None:
    eng = engine or get_engine()
    _upsert(
        eng,
        S.broker,
        [
            dict(
                broker_id=b.broker_id,
                name=b.name,
                commission_bps=b.commission_bps,
                portfolio_fee_bps=b.portfolio_fee_bps,
                specialty=b.specialty,
            )
            for b in BROKERS.values()
        ],
        ["broker_id"],
    )
    _upsert(
        eng,
        S.venue,
        [
            dict(
                mic=v.mic,
                name=v.name,
                kind=v.kind,
                supports_auction=v.supports_auction,
                fee_remove=v.fee_remove,
                fee_add=v.fee_add,
                fee_auction=v.fee_auction,
                fee_midpoint=v.fee_midpoint,
            )
            for v in VENUES.values()
        ],
        ["mic"],
    )
    _upsert(eng, S.desk, [dict(desk_id=k, name=v) for k, v in DESKS.items()], ["desk_id"])
    _upsert(eng, S.fund, FUNDS, ["fund_id"])
    now = datetime.now(UTC)
    _upsert(
        eng,
        S.users,
        [
            dict(username=u, role=r, display_name=u.capitalize(), created_at=now)
            for u, r in get_settings().allowed_user_roles().items()
        ],
        ["username"],
    )
    _upsert(
        eng,
        S.desk_policy,
        [
            dict(name=p.stem, yaml=p.read_text(), updated_by="seed", updated_at=now)
            for p in POLICY_DIR.glob("*.yaml")
        ],
        ["name"],
    )


def seed_reviews(snapshot: str, engine: Engine | None = None) -> dict[str, int]:
    """Corporate-action review queue (ported from the notebook's decision CSVs) and the
    BMLL mapping review queue. Existing review decisions are never overwritten."""
    eng = engine or get_engine()
    require_schema_at_head(eng)
    lake = Lake()
    dist = lake.read(snapshot, "crsp", "stkdistributions")
    auto = {"CD": "APPROVED", "FS": "APPROVED", "SO": "APPROVED", "RI": "PENDING", "MG": "UNSUPPORTED"}
    rows = []
    for r in dist.iter_rows(named=True):
        kind = classify_distribution(r)  # engine type; the raw CIZ code is kept in source_ids
        st = auto.get(kind, "PENDING")
        note = None
        if kind == "CD" and r["dispaydt"] is None:
            note = "pay date missing: handled by the provisional dividend rule"
        elif kind == "SO":
            note = "spin-off valued at CRSP distribution amount (keep_securities only)"
        elif kind == "MG":
            note = "merger / delisting: no payoff invented; security excluded once declared"
        elif kind == "UNK":
            note = f"unclassified CRSP distype {r['distype']!r}: add it to DISTYPE_MAP (stops runs that hold the name)"
        rows.append(
            dict(
                permno=int(r["permno"]),
                ex_date=r["disexdt"],
                type=kind,
                seq=int(r.get("disseqnbr") or 0),
                pay_date=r["dispaydt"],
                declared_date=r.get("disdeclaredt"),
                amount=float(r["disdivamt"] or 0.0),
                factor=float(r["disfacshr"] or 0.0) + 1,
                review_status=st,
                reviewed_by="auto" if st == "APPROVED" else None,
                note=note,
                source_ids={"crsp": "stkdistributions", "distype": r["distype"], "snapshot": snapshot[:12]},
            )
        )
    for k in range(0, len(rows), 2000):
        _upsert(eng, S.corporate_action, rows[k : k + 2000], ["permno", "ex_date", "type", "seq"])
    q = lake.root / "derived" / "security_map" / "review_queue.parquet"
    n_map = 0
    if q.exists():
        df = pl.read_parquet(q)
        mrows = [
            dict(
                permno=int(r["permno"]),
                valid_from=r["valid_from"],
                ticker=r["ticker"],
                isin=r["isin"],
                valid_to=r["valid_to"],
                reason=r["reason"],
                status="OPEN",
                snapshot=snapshot,
            )
            for r in df.iter_rows(named=True)
        ]
        _upsert(eng, S.mapping_review, mrows, ["permno", "valid_from"])
        n_map = len(mrows)
    with eng.connect() as c:
        pending = c.execute(
            select(S.corporate_action.c.permno).where(S.corporate_action.c.review_status == "PENDING")
        ).all()
    return {"corporate_actions": len(rows), "pending": len(pending), "mapping_queue": n_map}
