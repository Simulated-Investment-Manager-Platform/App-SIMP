"""Engine/session helpers and fast bulk inserts (COPY on Postgres)."""

from __future__ import annotations

import io
import json
import math
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, Table, create_engine, insert

from simp.settings import get_settings


@lru_cache(maxsize=4)
def get_engine(url: str | None = None) -> Engine:
    return create_engine(url or get_settings().database_url, pool_pre_ping=True, future=True)


def _cell(v: Any) -> str:
    if hasattr(v, "item") and not isinstance(v, str | bytes):
        v = v.item()  # numpy scalar -> Python (repr of np.float64 is not valid SQL text)
    if v is None:
        return r"\N"
    if isinstance(v, float):
        if not math.isfinite(v):
            return r"\N"
        return repr(float(v))
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=UTC)
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, bool):
        return "t" if v else "f"
    if isinstance(v, dict | list):
        s = json.dumps(v, default=_json_default)
    else:
        s = str(v)
    return s.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


def _json_default(o: Any) -> Any:
    if isinstance(o, datetime | date):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(type(o))


def clean_json(v: Any) -> Any:
    """Make numpy scalars / NaN JSON-safe."""
    if isinstance(v, dict):
        return {k: clean_json(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [clean_json(x) for x in v]
    if hasattr(v, "item") and not isinstance(v, str):
        v = v.item()
    if isinstance(v, Decimal):
        v = float(v)
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def bulk_insert(engine: Engine, table: Table, columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    if engine.dialect.name == "postgresql":
        buf = io.StringIO()
        for r in rows:
            buf.write("\t".join(_cell(clean_json(v) if isinstance(v, dict | list) else v) for v in r))
            buf.write("\n")
        raw = engine.raw_connection()
        try:
            with raw.cursor() as cur:  # type: ignore[attr-defined]
                cols = ", ".join(f'"{c}"' for c in columns)
                with cur.copy(f'COPY "{table.name}" ({cols}) FROM STDIN') as cp:
                    cp.write(buf.getvalue())
            raw.commit()
        finally:
            raw.close()
    else:
        with engine.begin() as conn:
            conn.execute(insert(table), [dict(zip(columns, (clean_json(v) for v in r), strict=True)) for r in rows])
    return len(rows)
