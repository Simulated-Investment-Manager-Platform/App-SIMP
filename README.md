# SIMP: Simulated Investment Manager Platform

SIMP runs a synthetic asset manager end to end. Funds decide on CRSP data. Decisions become
**PM orders → trader orders → broker child orders → fills**. Those orders execute against BMLL
market and auction data, and the results are measured with notional-weighted fund-level and
order-level analytics. The four-year S&P 500 index fund handoff (Jul 2022 – Jun 2026) is the
first fund on the platform.

```
backend/    Python 3.12 package `simp`: core library, engine, analytics, FastAPI, Typer CLI, Dramatiq workers
frontend/   Next.js 15 + TypeScript + Tailwind: Aceternity-style showcase layer, plus a dense working layer
            (TanStack Table/Query/Virtual, ECharts, nuqs URL state, Radix)
docker-compose.yml   Postgres 16, Redis 7, API, workers, web
```

> **Data status.** This build sandbox can reach neither WRDS nor BMLL. WRDS is Postgres on port 9737,
> which the sandbox's TLS-intercepting proxy cannot carry, and no BMLL SSH key is present. So the
> bundled lake is a **schema-identical synthetic source**: CIZ v2 column names for CRSP, Data Lab
> names for BMLL. It is labelled `SYNTHETIC` in every manifest, API response, export watermark and
> screen. The live connectors are implemented: `simp/data/crsp/wrds_loader.py` checks the live CIZ
> columns against the lake contracts on first connect, and `backend/connectors/bmll_puller` targets
> the real `bmll` SDK API. Switch over with `SIMP_DATA_SOURCE=live` on a machine with access; see
> [Going live](#going-live).

## Quick start (local)

```bash
# backend
cd backend
uv sync --all-extras
export SIMP_DATABASE_URL=postgresql+psycopg://simp:simp@127.0.0.1:5432/simp
uv run simp db upgrade                       # Alembic migrations + reference data (brokers, venues, desk policy, users)
uv run simp data pull                        # synthetic lake (~30 s); pins a snapshot hash
uv run uvicorn simp.api.app:app --port 8000  # API (OpenAPI at /docs)
uv run dramatiq simp.runs.worker --queues runs data   # workers (Redis)

# frontend
cd frontend
pnpm install --frozen-lockfile
pnpm build && pnpm start                     # http://localhost:3000 (proxies /api to :8000)
```

Or run the whole stack with Docker: `docker compose up -d --build`, then
`docker compose run --rm api simp db upgrade` and `docker compose run --rm api simp data pull`.

### CLI

| Command | What it does |
|---|---|
| `simp data pull [--source synthetic\|live]` | Pull CRSP + BMLL into the lake, build derived datasets, pin a snapshot |
| `simp data check-wrds` | Connect to WRDS; verify every CIZ table's columns against the contracts |
| `simp data register-bmll DIR` | Register Parquet written by the isolated BMLL puller |
| `simp map report` / `simp map build` | Mapping coverage + review queue / export `bmll_identifier_mapping.csv` |
| `simp run --preset index_t1_flows [--set k=v]` | Submit and execute a run (`handoff_replay`, `index_t0_effective`, `phase1_generator`) |
| `simp report RUN_ID` | Fund + TCA summary |
| `simp replay RUN_ID` | Re-run the same config + snapshot; exit 1 unless the output hash matches |
| `simp study counterfactual_chain\|dividend_rule\|exclusions\|cost_params\|flow_regime` | Run studies |
| `simp lab t3-job RUN_ID` | Data Lab job spec for T3 (`bmll2.simulation`) validation of the largest orders |

## What is implemented (mapped to the spec)

**Domain model.** `pm_order`, `trader_order`, `program`, `child_order` and `fill` hold the order
chain, with foreign keys from fill to child to trader to PM order to decision to run. The ledger
side is `ledger_entry` (fills, T+2/T+1 settlement, dividends, flows, fees), `nav_daily`,
`position_daily`, `flow`, `target_weight`, `corporate_action` and `security_map`. Every run ends
with an audit that checks 100% fill lineage, no quantity overruns, timestamps running forward and
explicit `EXPIRED`/`CANCELLED` statuses for any leftover quantity.

**Engine** (`simp/runs/engine.py`) runs a deterministic session loop: pre-open corporate actions
and settlement → decisions → desk → execution → post-close accounting. The NAV identity is checked
every session against an independent P&L path, and any break stops the run with a named error.
- Index replication with **effective-date timing**: decide at the announcement close (default 5
  sessions before), trade the third-Friday closing auction; Juneteenth 2026 moves the June trade to
  Thursday. **Handoff timing** (decide Monday, fill Tuesday) is kept for comparison. Off-cycle adds
  and deletes get their own decisions.
- Investor flows follow `F_t = AUM_{t-1}(μ + σε_t + J_t)`: AR(1) with Student-t innovations plus a
  redemption jump. They deal at pre-flow unit NAV. Cash-flow orders use a minimum ticket, are MOC on
  the flow date, and are **netted** against same-day rebalance orders through internal crosses (the
  saving is reported).
- Desk policy is a **YAML rules table** (`simp/desk/policies/default.yaml`, editable in Settings and
  validated): MOC up to 5% of expected auction volume, a VWAP remainder from 13:00 to 15:50, a
  portfolio trade for baskets under $25k, IS or high-touch for arrival orders, and a 25%
  auction-volume spill cap.
- Execution models: **T0** close proxy; **T1** daily BMLL data with the spec's auction and
  square-root impact models; **T2** tick replay capped at 10% of each interval's volume. **T3**
  (`bmll2.simulation`) is Data Lab–only: the engine raises with instructions and
  `simp lab t3-job` writes the job spec.
- Hard stops: missing price, unreviewed or unsupported corporate action on a held name, fractional
  split share, same-day split plus dividend, and negative net liquid cash. No merger payoff is ever
  invented: names with a declared unsupported event are excluded and exited before the ex-date.
- Look-ahead guard; seeded PCG64 draws per (seed, component, date); deterministic ULIDs.

**Phase 1 generator.** Orders per day are Poisson(λ = 40). Names are drawn by ADV20 weight. Size
follows a truncated-Pareto participation. Fills are priced at `VWAP·(1 + s·c + ξ)`, and the
injected cost and noise are stored as an answer key. The known-answer test recovers the injected
cost within 0.5 bps, notional-weighted.

**Analytics.** Everything is notional-weighted, with weighted quantiles and a block bootstrap by
date (2,000 resamples).
- **Implementation shortfall** is split into delay, execution, explicit and opportunity. Each order
  also gets slippage vs close / VWAP / arrival, Q/ADV20, Q/auction and Q/expected auction, spread,
  volatility, imbalance, markouts at +1s/5s/60s/300s and next open vs close.
- **Repricing.** Every order is repriced three ways (MOC, VWAP, arrival), plus T0 and the handoff's
  Tuesday close.
- **Size buckets and heatmap.** Cost by Q/ADV20 bucket and by Q/auction bucket, with CIs, and a
  heatmap of the two.
- **Run-level reports.** Concentration curves, netting savings, and a fallback report (marked
  *provisional* above 1% of notional).
- **Fund.** TD and TE (net and gross) against CRSP `vwretd` or the official TR, cash drag, beta,
  drawdown and worst days. **Counterfactual chains** decompose TD. Sensitivity studies cover the
  provisional dividend rule, the exclusion policy, cost parameters (±50%, MOC share 2–10%) and the
  flow regime.
- **The one-date study** (default 2026-03-20) gives one row per order with BMLL auction, imbalance,
  ADV, spread and VWAP fields, the three repricings, a T2 replay of the top ten, and the largest
  orders called out by name.

**Data.** The lake is Parquet with per-pull manifests (SHA-256 per file) and schema contracts
checked on write. Runs pin a **snapshot hash**, and also record the hash of the corporate-action
review set and of the desk policy. The PERMNO → ISIN → ListingId map is point in time: ISIN first,
then a ticker + OPOL fallback with a 1 bp price check. Unmapped names go to a review queue. Exports
come from a derived-tables allow-list with a watermark, and raw CRSP/BMLL tables are refused.

**API** (`/api`, OpenAPI at `/docs`): funds, runs (submit, SSE events, NAV, benchmark, flows,
holdings, compare), orders at four levels plus lineage, TCA (groups, heatmap, scatter, methods,
outliers, concentration, one-date study), securities, data coverage, review queues, exports,
settings and studies. The TypeScript client is generated from the schema (`pnpm gen:api`).

**UI**: Overview (spotlight header, bento grid, data-health strip) · Fund (performance, holdings,
flows, orders, TCA) · Runs (stepped new-run form with a data-availability check, live progress over
SSE, failure with the failing session, compare) · Blotter (PM → trader → child → fill, with a
lineage drawer, timeline and intraday chart) · TCA · One-date study · Market explorer · Studies ·
Data (manifests, pull jobs, mapping and corporate-action review) · Settings (users and roles,
brokers, venues, desk policy YAML editor). Dark by default, light supported. Buy is blue and sell is
orange, and cost uses a diverging scale. Every filter lives in the URL.

## Engineering standards (Andrew's housekeeping items)

| Item | Where |
|---|---|
| `research_pipeline.py` + `scripts/` | Split into `simp/` modules; notebook shim `simp/compat/research_pipeline.py`; scripts in `backend/scripts/` |
| Pinned versions | `backend/uv.lock`, `frontend/pnpm-lock.yaml`; Docker images built from the lockfiles; CI runs `uv lock --check` |
| Config assert (`and` binds before `or`) | Pydantic `Window` validator + `check_release` (always checks both bounds); tested |
| Replay cell swallowing errors | Typed `SimpError`s; runs end `FAILED` with class, message and failing session (shown in the UI); ruff `BLE`/`S110` ban catch-and-print |
| Provisional dividend rule | First-class sensitivity study (`dividend_rule`) |

**Checks.** ruff (lint + format), `mypy --strict` on `simp/` (clean) and pytest run in CI.

**Tests.** `backend/tests` covers ledger property tests (Hypothesis), order-chain invariants,
known-answer TCA, the timing regression, mapping (ISIN-first, 1 bp price check, review queue),
determinism, T2 caps, the API contract (OpenAPI snapshot), exports rights and roles. The golden
replay against the handoff CSVs runs when they are present in `backend/tests/golden/handoff/`.
`frontend` has Vitest unit tests and a Playwright journey (submit run → watch progress → TCA →
drill to a fill).

## Going live

**Full step-by-step guide: [docs/live-data-setup.md](docs/live-data-setup.md)** (accounts, secrets, WRDS check, CRSP pull, BMLL puller, registration, review queues, golden replay, troubleshooting). In short:

1. **WRDS.** Set `WRDS_USERNAME`/`WRDS_PASSWORD` on a host that can reach
   `wrds-pgdata.wharton.upenn.edu:9737`, then run `uv run simp data check-wrds`. This verifies the
   CIZ columns before any pull.
2. **BMLL.** In `backend/connectors/bmll_puller` (an isolated env, because `bmll` ≥ 1.25 pins
   NumPy < 2), run `uv run python puller.py --listings listings.csv --start … --end … --out
   ../../lake_staging/bmll` with `BMLL_KEY_PATH` set. Then run `uv run simp data register-bmll
   lake_staging/bmll`. Trades Plus @SIP, auction imbalance detail and T3 run as Data Lab jobs.
3. `SIMP_DATA_SOURCE=live uv run simp data pull`, then re-run the presets. With the handoff CSVs in
   `backend/tests/golden/handoff/`, `pytest -m golden` checks the replay.

## Known limits

- Numbers in this repository's demo database come from the synthetic source and are **not market
  data**. Cost levels are model outputs (T1/T2), never actual executions.
- Real CRSP data is needed to hit the spec's 1,010-session count. The rule-based NYSE calendar used
  for the synthetic source gives 1,002 sessions for Jul 2022 – Jun 2026.
- OAuth is GitHub (`SIMP_AUTH_MODE=oauth`) and was not exercised here. Dev mode signs users in from
  the allow-list.
