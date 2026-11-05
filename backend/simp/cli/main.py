"""SIMP command line: ``simp data pull``, ``simp map build``, ``simp run``, ``simp report``, ``simp replay``."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import typer

from simp.logging import configure_logging

app = typer.Typer(help="SIMP - Simulated Investment Manager Platform", no_args_is_help=True)
data_app = typer.Typer(help="Data pulls and the lake", no_args_is_help=True)
map_app = typer.Typer(help="PERMNO -> BMLL identifier map", no_args_is_help=True)
db_app = typer.Typer(help="Database", no_args_is_help=True)
lab_app = typer.Typer(help="BMLL Data Lab job specs", no_args_is_help=True)
app.add_typer(data_app, name="data")
app.add_typer(map_app, name="map")
app.add_typer(db_app, name="db")
app.add_typer(lab_app, name="lab")


def _echo(obj: Any) -> None:
    typer.echo(json.dumps(obj, indent=2, default=str))


@app.callback()
def _init() -> None:
    configure_logging()


# ------------------------------------------------------------------ db
@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply Alembic migrations and seed reference data."""
    from alembic import command
    from alembic.config import Config

    from simp.db.seed import seed_reference

    cfg = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).parents[2] / "migrations"))
    command.upgrade(cfg, "head")
    seed_reference()
    typer.echo("database at head; reference data seeded")


# ------------------------------------------------------------------ data
@data_app.command("pull")
def data_pull(
    source: str = typer.Option(None, help="synthetic | live (default: SIMP_DATA_SOURCE)"),
    seed: int = typer.Option(None, help="synthetic generator seed"),
    start: str = "2022-03-01",
    end: str = "2026-07-31",
) -> None:
    """Pull CRSP (WRDS) + BMLL into the lake, build derived datasets, pin a snapshot."""
    from datetime import date

    from simp.data.lake.store import Lake
    from simp.data.pull import pull_live, pull_synthetic
    from simp.db.seed import seed_reference, seed_reviews
    from simp.settings import get_settings

    s = get_settings()
    src = source or s.data_source
    t = time.time()
    lake = Lake()
    snap = (
        pull_synthetic(lake, seed or s.synthetic_seed)
        if src == "synthetic"
        else pull_live(lake, date.fromisoformat(start), date.fromisoformat(end))
    )
    seed_reference()
    res = seed_reviews(snap)
    _echo({"snapshot": snap, "source": src, "seconds": round(time.time() - t, 1), **res})


@data_app.command("register-bmll")
def register_bmll(directory: Path) -> None:
    """Register Parquet written by the isolated BMLL puller (contracts checked, manifests written)."""
    import polars as pl

    from simp.data.lake.store import Lake

    lake = Lake()
    for ds in ("reference_listing", "daily_analytics", "classified_trades", "auction_imbalance"):
        p = directory / f"{ds}.parquet"
        if not p.exists():
            typer.echo(f"skip {ds}: {p} not found")
            continue
        df = pl.read_parquet(p)
        dcol = None if ds == "reference_listing" else "Date"
        m = lake.write(
            "bmll",
            ds,
            df,
            date_col=dcol,
            query_args={"staged_from": str(directory)},
            sdk_version="bmll",
            data_label="BMLL",
        )
        typer.echo(f"{ds}: {m.row_count} rows, manifest {m.content_hash()[:12]}")


@data_app.command("manifests")
def data_manifests() -> None:
    from simp.data.lake.store import Lake

    _echo(
        [
            {k: m[k] for k in ("source", "dataset", "row_count", "data_label", "pulled_at", "manifest_hash")}
            for m in Lake().manifests()
        ]
    )


@data_app.command("check-wrds")
def check_wrds() -> None:
    """Connect to WRDS and verify every CIZ table's columns against the lake contracts."""
    from simp.data.crsp.wrds_loader import TABLES, WrdsClient

    w = WrdsClient()
    first, last = w.release_range()
    typer.echo(f"WRDS release covers {first}..{last}")
    for ds in TABLES:
        w.check_schema(ds)
        typer.echo(f"  {ds}: columns match contract")


# ------------------------------------------------------------------ mapping
@map_app.command("report")
def map_report(snapshot: str = typer.Option(None)) -> None:
    import polars as pl

    from simp.data.lake.store import Lake
    from simp.runs.orchestrator import latest_snapshot

    lake = Lake()
    snap = snapshot or latest_snapshot()
    sm = lake.read(snap, "derived", "security_map")
    q = pl.read_parquet(lake.root / "derived" / "security_map" / "review_queue.parquet")
    _echo(
        {
            "snapshot": snap,
            "rows": sm.height,
            "by_method": dict(sm.group_by("method").len().iter_rows()),
            "review_queue": q.with_columns(pl.all().cast(pl.Utf8)).to_dicts(),
        }
    )


@map_app.command("build")
def map_build() -> None:
    """Rebuild the map from the latest raw pulls and export bmll_identifier_mapping.csv for the notebook."""
    import polars as pl

    from simp.data.lake.store import Lake
    from simp.runs.orchestrator import latest_snapshot

    lake = Lake()
    snap = latest_snapshot()
    sm = lake.read(snap, "derived", "security_map")
    out = lake.root / "exports" / "bmll_identifier_mapping.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    sm.select(
        "permno",
        pl.col("listing_id").alias("bmll_id"),
        "valid_from",
        "valid_to",
        "isin",
        "ticker",
        "opol",
        "method",
    ).write_csv(out)
    typer.echo(f"wrote {out} ({sm.height} rows)")


# ------------------------------------------------------------------ runs
def _parse_sets(sets: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for s in sets:
        k, _, v = s.partition("=")
        try:
            val: Any = json.loads(v)
        except json.JSONDecodeError:
            val = v
        cur = out
        parts = k.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = val
    return out


@app.command("run")
def run_cmd(
    preset: str = typer.Option(
        "index_t1_flows", help="handoff_replay | index_t1_flows | index_t0_effective | phase1_generator"
    ),
    config: Path = typer.Option(None, help="JSON run config"),
    set_: list[str] = typer.Option(
        [], "--set", help="override, e.g. --set execution_model=T0 --set flows.sigma_bps=25"
    ),
    wait: bool = typer.Option(True, help="run in this process and wait"),
) -> None:
    """Submit (and by default execute) a run."""
    from simp.config.run_config import PRESETS, RunConfig
    from simp.runs.orchestrator import _merge, execute, submit

    base: dict[str, Any] = json.loads(config.read_text()) if config else dict(PRESETS[preset])
    cfg = RunConfig.model_validate(_merge(base, _parse_sets(set_)))
    rid = submit(cfg, "cli", enqueue=not wait)
    typer.echo(f"run {rid} submitted (config {cfg.config_hash()[:12]})")
    if wait:
        t = time.time()
        oh = execute(rid)
        typer.echo(f"run {rid} DONE in {time.time() - t:.1f}s, output hash {oh}")


@app.command("report")
def report_cmd(run_id: str) -> None:
    """Print a run's fund and TCA summary."""
    from sqlalchemy import select

    from simp.db import schema as S
    from simp.db.session import get_engine

    with get_engine().connect() as c:
        r = c.execute(select(S.run).where(S.run.c.run_id == run_id)).mappings().one()
    s = r["summary"] or {}
    _echo(
        {
            "run_id": run_id,
            "status": r["status"],
            "data": s.get("data_label"),
            "fund": s.get("fund"),
            "tca": s.get("tca"),
            "audit": s.get("audit"),
            "error": r["error"],
        }
    )


@app.command("replay")
def replay_cmd(run_id: str) -> None:
    """Re-run a run's exact config + snapshot and check the output hash matches."""
    from sqlalchemy import select

    from simp.config.run_config import RunConfig
    from simp.db import schema as S
    from simp.db.session import get_engine
    from simp.runs.orchestrator import execute, submit

    with get_engine().connect() as c:
        r = c.execute(select(S.run).where(S.run.c.run_id == run_id)).mappings().one()
    cfg = RunConfig.model_validate(r["config"])
    rid = submit(cfg.model_copy(update={"label": f"replay of {run_id}"}), "replay", enqueue=False)
    oh = execute(rid)
    ok = oh == r["output_hash"]
    typer.echo(f"replay {rid}: {'MATCH' if ok else 'MISMATCH'} ({oh[:16]} vs {str(r['output_hash'])[:16]})")
    raise typer.Exit(0 if ok else 1)


@app.command("study")
def study_cmd(
    kind: str = typer.Argument(
        ..., help="counterfactual_chain | dividend_rule | exclusions | cost_params | flow_regime"
    ),
    preset: str = "index_t1_flows",
) -> None:
    """Submit a counterfactual chain or sensitivity study (runs go to the worker queue)."""
    from simp.config.run_config import preset as mk
    from simp.runs.orchestrator import submit_study

    sid, runs = submit_study(kind, mk(preset), "cli")
    _echo({"study_id": sid, "runs": runs})


# ------------------------------------------------------------------ lab
@lab_app.command("t3-job")
def lab_t3(run_id: str, top: int = 50) -> None:
    """Write a Data Lab job spec for T3 (bmll2.simulation) validation of a run's largest orders."""
    from sqlalchemy import text

    from simp.db.session import get_engine

    with get_engine().connect() as c:
        rows = (
            c.execute(
                text(
                    "select pm_order_id, date, permno, side, filled_qty, exec_notional from tca_order where run_id=:r "
                    "order by exec_notional desc limit :n"
                ),
                {"r": run_id, "n": top},
            )
            .mappings()
            .all()
        )
    spec = {
        "kind": "t3_simulation",
        "run_id": run_id,
        "models": ["AlmgrenChrissModel", "KylesLambdaModel"],
        "exchange": "PriceTimeExchange",
        "actors": ["HistoricalActor", "HistoricalAuctionActor"],
        "orders": [dict(r) for r in rows],
    }
    p = Path(f"t3_job_{run_id}.json")
    p.write_text(json.dumps(spec, indent=1, default=str))
    typer.echo(f"wrote {p}: submit inside the Data Lab with bmll.compute.create_job")


# ------------------------------------------------------------------ services
@app.command("api")
def api_cmd(host: str = "0.0.0.0", port: int = 8000, reload: bool = False) -> None:  # noqa: S104
    import uvicorn

    uvicorn.run("simp.api.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
