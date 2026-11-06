"""Trades Plus minute bars for T2, fetched on demand and cached in the lake.

A T2 run on live data asks for bars just before it executes each session: the (date, permno)
pairs of that day's intraday algo orders that the cache has not seen are pulled by the isolated
BMLL puller (``puller.py --t2-needs``, run as a subprocess in its own environment) and written to
``lake/bmll/tpm_cache/date=YYYY-MM-DD/part-<hash>.parquet``. A ledger records every pair asked
for, including pairs BMLL returned nothing for, so no pair is ever fetched twice. Cached bars are
historical and immutable; the run records the SHA-256 of every cache file it read.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from collections.abc import Callable, Iterable
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import polars as pl

from simp.data.lake.contracts import CONTRACTS, check_contract, conform
from simp.data.lake.store import Lake, sha256_file
from simp.errors import DataMissingError
from simp.settings import get_settings

if TYPE_CHECKING:
    from simp.data.market import MarketData

INTRADAY_STRATEGIES = ("HIGH_TOUCH", "ALGO_VWAP", "ALGO_TWAP", "ALGO_POV", "ALGO_IS")
NEEDS_COLS = ["date", "permno", "listing_id", "sip_listing_id", "ticker"]

Puller = Callable[[pl.DataFrame, Path, Callable[[str], None] | None], pl.DataFrame]  # needs -> bar rows


def resolve_needs(pairs: pl.DataFrame, smap: pl.DataFrame, ref: pl.DataFrame) -> pl.DataFrame:
    """(date, permno) -> + primary listing, @SIP listing and the BMLL ticker valid on that date.
    Pairs without an @SIP listing come back with a null ticker (T2 falls back for them)."""
    j = pairs.select(pl.col("date").cast(pl.Date), pl.col("permno").cast(pl.Int64)).unique()
    j = j.join(
        smap.select("permno", "listing_id", "sip_listing_id", "valid_from", "valid_to"),
        on="permno",
        how="left",
    )
    j = j.filter(
        pl.col("valid_from").is_null()
        | ((pl.col("date") >= pl.col("valid_from")) & (pl.col("date") <= pl.col("valid_to")))
    )
    sip = ref.filter(pl.col("MIC") == "@SIP").select(
        pl.col("ListingId").cast(pl.Int64).alias("sip_listing_id"),
        pl.col("Ticker").alias("ticker"),
        pl.col("ValidFrom").alias("tf"),
        pl.col("ValidTo").alias("tt"),
    )
    j = j.join(sip, on="sip_listing_id", how="left").filter(
        pl.col("ticker").is_null() | ((pl.col("date") >= pl.col("tf")) & (pl.col("date") <= pl.col("tt")))
    )
    return j.select(NEEDS_COLS).unique(["date", "permno"], keep="first").sort("date", "permno")


def _dotenv_bmll() -> dict[str, str]:
    """BMLL_* settings from backend/.env, so the puller subprocess logs in like the shell does."""
    try:
        from dotenv import dotenv_values
    except ImportError:  # pragma: no cover - python-dotenv ships with pydantic-settings
        return {}
    return {k: v for k, v in dotenv_values(".env").items() if k.startswith("BMLL_") and v}


def subprocess_puller(
    needs: pl.DataFrame, staging: Path, log: Callable[[str], None] | None = None
) -> pl.DataFrame:
    """Run ``puller.py --t2-needs`` in the puller's own uv environment (bmll pins numpy<2)."""
    s = get_settings()
    pdir = s.bmll_puller_dir
    uv = shutil.which("uv")
    if uv is None or not (pdir / "puller.py").exists():
        raise DataMissingError(f"cannot fetch Trades Plus bars: need `uv` on PATH and {pdir / 'puller.py'}")
    staging.mkdir(parents=True, exist_ok=True)
    csv = staging / "t2_needs.csv"
    needs.write_csv(csv)
    env = dict(os.environ)
    env.update({k: v for k, v in _dotenv_bmll().items() if k not in env})
    if s.bmll_key_path:
        env.setdefault("BMLL_KEY_PATH", str(s.bmll_key_path))
    env.pop("VIRTUAL_ENV", None)  # the puller has its own environment
    cmd = [uv, "run", "--project", str(pdir), "python", str(pdir / "puller.py"),
           "--t2-needs", str(csv), "--out", str(staging)]  # fmt: skip
    lines: list[str] = []
    with subprocess.Popen(
        cmd, cwd=pdir, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    ) as pr:  # noqa: S603
        assert pr.stdout is not None  # noqa: S101 - type narrowing
        for line in pr.stdout:
            lines.append(line.rstrip())
            if log is not None and (line.lstrip().startswith("[") or "queries over" in line):
                log("T2 bars:" + line.rstrip())
        code = pr.wait()
    if code != 0:
        tail = "\n".join(lines[-15:])
        raise DataMissingError(f"BMLL Trades Plus fetch failed (exit {code}):\n{tail}")
    out = staging / "trades_plus_minute.parquet"
    return pl.read_parquet(out) if out.exists() and out.stat().st_size else pl.DataFrame()


class BarCache:
    def __init__(self, lake: Lake, smap: pl.DataFrame, ref: pl.DataFrame, puller: Puller | None = None,
                 log: Callable[[str], None] | None = None) -> None:  # fmt: skip
        self.lake, self.smap, self.ref = lake, smap, ref
        self.puller = puller or subprocess_puller
        self.log = log or (lambda _m: None)
        self.base = lake.root / "bmll" / "tpm_cache"
        self.ledger_path = self.base / "fetched.parquet"
        self.used: dict[str, str] = {}  # cache file -> sha256, for the run's audit record
        self._ledger: set[tuple[date, int]] | None = None

    # ---------------------------------------------------------------- ledger
    def fetched(self) -> set[tuple[date, int]]:
        if self._ledger is None:
            self._ledger = set()
            if self.ledger_path.exists():
                self._ledger = {(d, int(p)) for d, p in pl.read_parquet(self.ledger_path).iter_rows()}
        return self._ledger

    def _record(self, pairs: Iterable[tuple[date, int]]) -> None:
        led = self.fetched() | set(pairs)
        self._ledger = led
        self.base.mkdir(parents=True, exist_ok=True)
        tmp = self.ledger_path.with_suffix(".tmp")
        pl.DataFrame({"date": [d for d, _ in led], "permno": [p for _, p in led]},
                     schema={"date": pl.Date, "permno": pl.Int64}).sort("date", "permno").write_parquet(tmp)  # fmt: skip
        tmp.replace(self.ledger_path)

    # ---------------------------------------------------------------- fetch
    def ensure(self, pairs: pl.DataFrame) -> int:
        """Fetch every (date, permno) in ``pairs`` the cache has not asked BMLL for. Returns the
        number of pairs fetched."""
        have = self.fetched()
        pairs = pairs.select(pl.col("date").cast(pl.Date), pl.col("permno").cast(pl.Int64)).unique()
        seen = pl.DataFrame({"date": [d for d, _ in have], "permno": [p for _, p in have]},
                            schema={"date": pl.Date, "permno": pl.Int64})  # fmt: skip
        todo = pairs.join(seen, on=["date", "permno"], how="anti")
        if not todo.height:
            return 0
        needs = resolve_needs(todo, self.smap, self.ref)
        ask = needs.drop_nulls(["ticker", "sip_listing_id", "listing_id"])
        days = ask["date"].n_unique() if ask.height else 0
        if ask.height:
            self.log(
                f"T2: fetching Trades Plus bars for {ask.height} (date, name) pairs over {days} sessions"
            )
            bars = self.puller(ask, self.lake.root / "_staging" / "t2", self.log)
            self._store(bars, ask)
        self._record((d, int(p)) for d, p in todo.select("date", "permno").iter_rows())
        return int(todo.height)

    def _store(self, bars: pl.DataFrame, asked: pl.DataFrame) -> None:
        if not bars.height:
            return
        c = CONTRACTS[("bmll", "trades_plus_minute")]
        bars = conform(bars, c)
        keys = asked.select(pl.col("date").alias("Date"), "permno")
        bars = bars.join(keys, on=["Date", "permno"], how="semi")  # only what was asked this time
        check_contract(bars, c)
        for (d,), part in bars.group_by(["Date"]):
            h = hashlib.sha256(part.sort("permno", "Minute").write_csv().encode()).hexdigest()[:16]
            f = self.base / f"date={d}" / f"part-{h}.parquet"
            f.parent.mkdir(parents=True, exist_ok=True)
            if not f.exists():
                part.sort("permno", "Minute").write_parquet(f)

    # ---------------------------------------------------------------- read
    def read_day(self, d: date) -> pl.DataFrame:
        files = sorted((self.base / f"date={d}").glob("part-*.parquet"))
        for f in files:
            self.used.setdefault(str(f.relative_to(self.lake.root)), sha256_file(f))
        if not files:
            return pl.DataFrame(schema=dict(CONTRACTS[("bmll", "trades_plus_minute")].columns))
        return pl.concat([pl.read_parquet(f) for f in files]).unique(
            ["permno", "Date", "Minute"], keep="first"
        )

    def loader(self, md: MarketData) -> Callable[[int], tuple[np.ndarray, np.ndarray, np.ndarray] | None]:
        """Session index -> bars from the cache, overlaid on any bars the snapshot carries."""
        from simp.data.market import bars_from_frame

        def load(i: int) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
            d = md.sessions[i]
            got = bars_from_frame(self.read_day(d), md.pos).get(d)
            base = md.bars.get(i)
            if got is None or base is None:
                return got if got is not None else base
            have = np.isfinite(got[0]).any(axis=1)
            return tuple(np.where(have[:, None], g, b) for g, b in zip(got, base, strict=True))  # type: ignore[return-value]

        return load

    def audit_hash(self) -> str:
        return hashlib.sha256(
            "\n".join(f"{k}:{v}" for k, v in sorted(self.used.items())).encode()
        ).hexdigest()


def available() -> tuple[bool, str]:
    """Whether this machine can fetch bars on demand (for the run pre-flight check)."""
    s = get_settings()
    if not s.t2_auto_fetch:
        return False, "automatic Trades Plus fetching is off (SIMP_T2_AUTO_FETCH=false)"
    if shutil.which("uv") is None:
        return False, "`uv` is not on PATH for the API/worker process"
    if not (s.bmll_puller_dir / "puller.py").exists():
        return False, f"BMLL puller not found at {s.bmll_puller_dir}"
    if not (s.bmll_key_path or os.environ.get("BMLL_KEY_PATH") or _dotenv_bmll().get("BMLL_KEY_PATH")):
        return False, "BMLL_KEY_PATH is not set for the API/worker process"
    return True, "missing Trades Plus minute bars are fetched from BMLL during the run and cached"


def run_pairs(run_id: str) -> pl.DataFrame:
    """(date, permno) of a run's intraday algo orders (New York trade date)."""
    from sqlalchemy import text

    from simp.accounting.calendar import NY
    from simp.db.session import get_engine

    with get_engine().connect() as c:
        rows = c.execute(
            text(
                "select distinct p.permno, t.start_ts from trader_order t join pm_order p "
                "on p.pm_order_id = t.pm_order_id where t.run_id = :r and t.strategy = any(:s)"
            ),
            {"r": run_id, "s": list(INTRADAY_STRATEGIES)},
        ).all()
    return pl.DataFrame(
        {"date": [ts.astimezone(NY).date() for _, ts in rows], "permno": [int(p) for p, _ in rows]},
        schema={"date": pl.Date, "permno": pl.Int64},
    ).unique()


def prior_run(fund_id: str, exclude: str) -> str | None:
    """The latest finished live-data run of the same fund: its orders seed a batch prefetch."""
    from sqlalchemy import text

    from simp.db.session import get_engine

    with get_engine().connect() as c:
        r = c.execute(
            text(
                "select run_id from run where fund_id = :f and run_id <> :x and status = 'DONE' "
                "and coalesce(data_label, '') <> 'SYNTHETIC' order by finished_at desc nulls last limit 1"
            ),
            {"f": fund_id, "x": exclude},
        ).first()
    return None if r is None else str(r[0])
