"""Test fixtures: an isolated Postgres database (simp_test), a temporary lake and a small
synthetic world (Jun 2025 - Mar 2026: three quarterly rebalances)."""

from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path

# setdefault: this module can be imported twice (as `conftest` and `tests.conftest`)
os.environ.setdefault("SIMP_LAKE_ROOT", tempfile.mkdtemp(prefix="simp-test-lake-"))
_LAKE = Path(os.environ["SIMP_LAKE_ROOT"])
os.environ.setdefault("SIMP_DATABASE_URL", "postgresql+psycopg://simp:simp@127.0.0.1:5432/simp_test")
os.environ["SIMP_QUEUE"] = "inline"
os.environ["SIMP_LOG_FORMAT"] = "console"

import pytest  # noqa: E402

from simp.config.run_config import RunConfig, Window  # noqa: E402

SMALL_START, SMALL_END = date(2025, 6, 2), date(2026, 3, 31)
WINDOW = Window(bootstrap=date(2025, 9, 2), start=date(2025, 9, 8), end=date(2026, 3, 27))


@pytest.fixture(scope="session")
def snapshot() -> str:
    from simp.data.lake.store import Lake
    from simp.data.pull import SNAPSHOT_DATASETS, build_derived, write_raw
    from simp.data.synthetic.generator import generate

    w = generate(7, SMALL_START, SMALL_END)
    lake = Lake(_LAKE)
    args = {"generator": "simp.data.synthetic", "seed": 7}
    write_raw(lake, w.tables, "SYNTHETIC", args, "test")
    build_derived(lake, w.tables, "SYNTHETIC", args)
    return lake.create_snapshot(SNAPSHOT_DATASETS)


@pytest.fixture(scope="session")
def md(snapshot: str):  # type: ignore[no-untyped-def]
    from simp.data.market import load_market

    return load_market(snapshot, str(_LAKE))


@pytest.fixture(scope="session")
def db(snapshot: str) -> str:
    from simp.db.schema import metadata
    from simp.db.seed import seed_reference, seed_reviews
    from simp.db.session import get_engine

    eng = get_engine()
    metadata.drop_all(eng)
    metadata.create_all(eng)
    seed_reference(eng)
    seed_reviews(snapshot, eng)
    return snapshot


def cfg(**kw: object) -> RunConfig:
    base: dict[str, object] = {"window": WINDOW.model_dump(), "initial_aum": 1_000_000_000.0}
    base.update(kw)
    return RunConfig.model_validate(base)


@pytest.fixture(scope="session")
def make_cfg():  # type: ignore[no-untyped-def]
    return cfg
