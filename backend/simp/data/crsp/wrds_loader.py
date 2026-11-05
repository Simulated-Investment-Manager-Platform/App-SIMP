"""CRSP loaders over WRDS (CIZ / v2 schemas ``crsp_q_stock`` and ``crsp_q_indexes``).

Batched SQL (BATCH_SIZE = 150 PERMNOs per query, as in the handoff) with explicit release
checks. On first use each table's live columns are read from ``information_schema.columns``
and compared with the lake contract; a difference raises ``SchemaMismatchError`` naming the
missing columns, so a WRDS release change is caught before any data is written.

Credentials come from WRDS_USERNAME / WRDS_PASSWORD (deployment secret store) and are never
logged.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import date
from typing import Any

import polars as pl
import structlog

from simp.data.lake.contracts import CONTRACTS
from simp.errors import SchemaMismatchError, SourceUnavailableError
from simp.settings import Settings, get_settings

log = structlog.get_logger(__name__)
BATCH_SIZE = 150

# lake dataset -> (schema, table, date column used for the window filter)
TABLES: dict[str, tuple[str, str, str | None]] = {
    "dsf_v2": ("crsp_q_stock", "dsf_v2", "dlycaldt"),
    "stksecurityinfohist": ("crsp_q_stock", "stksecurityinfohist", None),
    "stkdistributions": ("crsp_q_stock", "stkdistributions", "disexdt"),
    "stkdelists": ("crsp_q_stock", "stkdelists", None),
    "dsp500list_v2": ("crsp_q_indexes", "dsp500list_v2", None),
    "dsp500": ("crsp_q_indexes", "dsp500", "caldt"),
}


def _batches(xs: Sequence[int], n: int = BATCH_SIZE) -> Iterator[Sequence[int]]:
    for i in range(0, len(xs), n):
        yield xs[i : i + n]


class WrdsClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.s = settings or get_settings()
        if not self.s.wrds_username or not self.s.wrds_password:
            raise SourceUnavailableError("WRDS_USERNAME / WRDS_PASSWORD are not set")
        self._conn: Any = None

    def connect(self) -> Any:
        if self._conn is None:
            import psycopg

            pw = self.s.wrds_password.get_secret_value() if self.s.wrds_password else ""
            try:
                self._conn = psycopg.connect(
                    host=self.s.wrds_host,
                    port=self.s.wrds_port,
                    dbname="wrds",
                    user=self.s.wrds_username,
                    password=pw,
                    sslmode="require",
                    connect_timeout=20,
                )
            except psycopg.OperationalError as e:
                raise SourceUnavailableError(
                    f"cannot reach WRDS at {self.s.wrds_host}:{self.s.wrds_port}: {e}"
                ) from e
        return self._conn

    def fetch(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> pl.DataFrame:
        with self.connect().cursor() as cur:
            cur.execute(sql, params)
            cols = [d.name for d in cur.description or []]
            rows = cur.fetchall()
        return pl.DataFrame(rows, schema=cols, orient="row") if rows else pl.DataFrame(schema=cols)

    # ---------------------------------------------------------------- checks
    def live_columns(self, dataset: str) -> set[str]:
        schema, table, _ = TABLES[dataset]
        return set(
            self.fetch(
                "select column_name from information_schema.columns where table_schema=%s and table_name=%s",
                (schema, table),
            )["column_name"].to_list()
        )

    def check_schema(self, dataset: str) -> None:
        schema, table, _ = TABLES[dataset]
        if dataset == "dsp500" and not self.live_columns(dataset):
            TABLES["dsp500"] = ("crsp", "dsp500", "caldt")  # legacy SIZ location of the S&P 500 index file
            schema, table, _ = TABLES[dataset]
        live = set(
            self.fetch(
                "select column_name from information_schema.columns where table_schema=%s and table_name=%s",
                (schema, table),
            )["column_name"].to_list()
        )
        c = CONTRACTS[("crsp", dataset)]
        missing = sorted(set(c.columns) - live - set(c.optional))
        if missing:
            raise SchemaMismatchError(
                f"{schema}.{table} is missing documented columns {missing}; live has {sorted(live)}"
            )

    def release_range(self) -> tuple[date, date]:
        df = self.fetch("select min(dlycaldt) as a, max(dlycaldt) as b from crsp_q_stock.dsf_v2")
        return df["a"][0], df["b"][0]

    # ---------------------------------------------------------------- loaders
    def membership(self) -> pl.DataFrame:
        self.check_schema("dsp500list_v2")
        return self.fetch("select permno, mbrstartdt, mbrenddt from crsp_q_indexes.dsp500list_v2")

    def table_for_permnos(self, dataset: str, permnos: Sequence[int], start: date, end: date) -> pl.DataFrame:
        self.check_schema(dataset)
        schema, table, dcol = TABLES[dataset]
        cols = ", ".join(sorted(set(CONTRACTS[("crsp", dataset)].columns) & self.live_columns(dataset)))
        frames = []
        for batch in _batches(sorted(permnos)):
            where = "permno = any(%s)"
            params: list[Any] = [list(batch)]
            if dcol:
                where += f" and {dcol} between %s and %s"
                params += [start, end]
            frames.append(self.fetch(f"select {cols} from {schema}.{table} where {where}", params))  # noqa: S608
            log.info("wrds.batch", dataset=dataset, n=len(batch), rows=frames[-1].height)
        return pl.concat(frames, how="vertical_relaxed") if frames else pl.DataFrame()

    def benchmark(self, start: date, end: date) -> pl.DataFrame:
        self.check_schema("dsp500")
        return self.fetch(
            "select caldt, vwretd, vwretx, sprtrn, spindx from crsp_q_indexes.dsp500 where caldt between %s and %s",
            (start, end),
        )


def _conform_all(raw: dict[tuple[str, str], pl.DataFrame]) -> dict[tuple[str, str], pl.DataFrame]:
    from simp.data.lake.contracts import conform

    return {k: conform(v, CONTRACTS[k]) for k, v in raw.items()}


def pull_crsp(start: date, end: date) -> dict[tuple[str, str], pl.DataFrame]:
    """Pull every CRSP table the platform needs for S&P 500 members active in [start, end]."""
    w = WrdsClient()
    first, last = w.release_range()
    if start < first or end > last:
        from simp.errors import DataWindowError

        raise DataWindowError(f"WRDS release covers {first}..{last}, pull asks {start}..{end}")
    mbr = w.membership()
    mbr = mbr.filter((pl.col("mbrenddt") >= start) & (pl.col("mbrstartdt") <= end))
    permnos = sorted(set(mbr["permno"].to_list()))
    out = {("crsp", "dsp500list_v2"): mbr, ("crsp", "dsp500"): w.benchmark(start, end)}
    for ds in ("dsf_v2", "stksecurityinfohist", "stkdistributions", "stkdelists"):
        out[("crsp", ds)] = w.table_for_permnos(ds, permnos, start, end)
    return _conform_all(out)
