"""Data pull jobs (run by the ``data`` worker queue or inline)."""

from __future__ import annotations

import hashlib
import threading
import time
import traceback
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select, update

from simp.db import schema as S
from simp.db.session import clean_json, get_engine
from simp.domain.ids import ulid_from
from simp.settings import get_settings


def create_job(kind: str, source: str, args: dict[str, Any], user: str) -> str:
    now = datetime.now(UTC)
    jid = ulid_from(now, int.from_bytes(hashlib.sha256(f"{kind}{time.time_ns()}".encode()).digest()[:10], "big"))
    with get_engine().begin() as c:
        c.execute(S.data_job.insert().values(job_id=jid, kind=kind, source=source, status="QUEUED", args=args, created_by=user, created_at=now))
    if get_settings().queue == "inline":
        threading.Thread(target=_safe, args=(jid,), daemon=True).start()
    else:
        from simp.runs.worker import data_actor

        data_actor.send(jid)
    return jid


def _safe(jid: str) -> None:
    try:
        execute_job(jid)
    except Exception:  # noqa: BLE001 - recorded on the job row by execute_job
        return


def execute_job(jid: str) -> dict[str, Any]:
    from simp.data.lake.store import Lake
    from simp.data.pull import pull_live, pull_synthetic
    from simp.db.seed import seed_reviews

    eng = get_engine()
    with eng.connect() as c:
        job = c.execute(select(S.data_job).where(S.data_job.c.job_id == jid)).mappings().one()
    with eng.begin() as c:
        c.execute(update(S.data_job).where(S.data_job.c.job_id == jid).values(status="RUNNING"))
    try:
        lake = Lake()
        a = job["args"] or {}
        if job["source"] == "synthetic":
            snap = pull_synthetic(lake, int(a.get("seed", get_settings().synthetic_seed)))
        else:
            snap = pull_live(lake, date.fromisoformat(a.get("start", "2022-03-01")), date.fromisoformat(a.get("end", "2026-07-31")))
        reviews = seed_reviews(snap)
        result = {"snapshot": snap, **reviews}
        with eng.begin() as c:
            c.execute(update(S.data_job).where(S.data_job.c.job_id == jid).values(status="DONE", result=clean_json(result),
                                                                              finished_at=datetime.now(UTC)))
        return result
    except Exception as e:
        with eng.begin() as c:
            c.execute(update(S.data_job).where(S.data_job.c.job_id == jid).values(
                status="FAILED", error=f"{type(e).__name__}: {e}\n{traceback.format_exc()[-3000:]}", finished_at=datetime.now(UTC)))
        raise
