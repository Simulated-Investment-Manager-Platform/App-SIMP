# SIMP from zero to a running app

This guide goes from an empty machine to the SIMP web app running on **live WRDS (CRSP) + BMLL
data**, in this order:

1. [Prerequisites](#1-prerequisites)
2. [Clone the repository](#2-clone-the-repository)
3. [Start Postgres and Redis](#3-start-postgres-and-redis)
4. [Set up the backend](#4-set-up-the-backend)
5. [Load live data](#5-load-live-data-wrds--bmll)
6. [Start the API and workers](#6-start-the-api-and-workers)
7. [Set up the web app](#7-set-up-the-web-app)
8. [First run and verification](#8-first-run-and-verification)
9. [Production deployment](#9-production-deployment)
10. [Day-2 operations](#10-day-2-operations)

Two routes are covered throughout:
- **Native:** Python/Node on the host. Best for research work and for the live data pull.
- **Docker Compose:** the whole stack in containers. Best for a shared server. Section 9 covers it.

> Want to try the app before you have live access? Do sections 1–4, run `uv run simp data pull --source synthetic`
> instead of section 5, then continue at section 6. Everything works the same, labelled **Synthetic data**.

---

## 1. Prerequisites

### Accounts (live data)

| Account | What you need |
|---|---|
| **WRDS** | Username and password, with a **CRSP CIZ (v2)** subscription. The schemas used are `crsp_q_stock` and `crsp_q_indexes`. |
| **BMLL Data Lab** | SDK access with an **SSH key registered** to your account. US equity entitlements: daily analytics and classified trades on XNYS/XNAS/ARCX/BATS and `@SIP`. Optional: Trades Plus `@SIP` (for the T2 model) and Auction Imbalance. |
| **GitHub** (optional) | An OAuth app, if you want real sign-in for a shared server (section 9.3). |
| **Index vendor** (optional) | A licensed daily S&P 500 Total Return series, for benchmark (b). |

### Network

- **Outbound TCP to `wrds-pgdata.wharton.upenn.edu:9737`.** WRDS is reached over the raw Postgres
  protocol, so HTTPS-only or TLS-inspecting proxies will **not** work. Test it with:
  `nc -vz wrds-pgdata.wharton.upenn.edu 9737`. It should print `succeeded` / `open`.
- Outbound HTTPS to BMLL, PyPI and npm.

### Software

| Tool | Version | Install |
|---|---|---|
| git | any | your OS package manager |
| **uv** (Python manager) | ≥ 0.5 | `curl -LsSf https://astral.sh/uv/install.sh \| sh`. uv installs Python 3.12 for you. |
| **Node.js** | 22 LTS | <https://nodejs.org> or `nvm install 22` |
| **pnpm** | 10 | `corepack enable` (ships with Node 22) |
| **Postgres** | 16 | native or Docker (section 3) |
| **Redis** | 7 | native or Docker (section 3) |
| Docker + Compose | optional | <https://docs.docker.com/get-docker/>. Simplest way to get Postgres/Redis. |

Hardware: 4+ cores, 16 GB RAM and about 20 GB of free disk for the lake. A full four-year T1 run takes about a minute.

---

## 2. Clone the repository

```bash
git clone https://github.com/Simulated-Investment-Manager-Platform/App-SIMP.git
cd App-SIMP
git checkout claude/upbeat-johnson-fk7zqf     # until it is merged to the default branch
```

Layout:

```
App-SIMP/
  backend/                  Python package `simp` (engine, analytics, API, CLI, workers)
    connectors/bmll_puller/ isolated BMLL SDK puller (its own environment)
    migrations/             Alembic database migrations
    tests/                  pytest suite
    .env.example            configuration template
  frontend/                 Next.js web app
  docs/                     this guide, live-data-setup.md, operations.md
  docker-compose.yml        full stack in containers
```

---

## 3. Start Postgres and Redis

**Option A: Docker (easiest).** From the repo root:

```bash
docker compose up -d db redis
```

This gives Postgres on `localhost:5432` (user `simp`, password `simp`, database `simp`) and Redis on `localhost:6379`.

**Option B: native.** Install Postgres 16 and Redis 7 with your package manager, then:

```bash
sudo -u postgres psql -c "create role simp login password 'simp';"
sudo -u postgres psql -c "create database simp owner simp;"
sudo -u postgres psql -c "create database simp_test owner simp;"   # only needed to run the tests
redis-cli ping     # -> PONG
```

Use a stronger password than `simp` anywhere other than your laptop.

---

## 4. Set up the backend

```bash
cd backend
uv sync --all-extras            # creates .venv with Python 3.12 and exact pinned versions from uv.lock
cp .env.example .env
```

Edit **`backend/.env`**. It is git-ignored, so never commit it:

```ini
SIMP_DATABASE_URL=postgresql+psycopg://simp:simp@127.0.0.1:5432/simp
SIMP_REDIS_URL=redis://127.0.0.1:6379/0
SIMP_DATA_SOURCE=live
SIMP_QUEUE=dramatiq
SIMP_AUTH_MODE=dev
SIMP_ALLOWED_USERS=tanish:researcher,andrew:viewer,admin:admin
SIMP_SESSION_SECRET=<output of: openssl rand -hex 32>
SIMP_WEB_ORIGIN=http://localhost:3000
WRDS_USERNAME=<your WRDS username>
WRDS_PASSWORD=<your WRDS password>
BMLL_KEY_PATH=/absolute/path/to/your/bmll_ssh_private_key
```

What each setting does:

- `SIMP_ALLOWED_USERS`: who may use the app, with roles. **viewer** can read, **researcher** can submit runs, edit
  reviews and the desk policy, and **admin** can also manage users.
- `SIMP_AUTH_MODE=dev`: no login screen; requests act as user `tanish`, and you can switch user under Settings → Users. Use `oauth` on any shared server (section 9.3).
- `SIMP_QUEUE=dramatiq`: runs and data pulls go to the Redis workers. `inline` runs them inside the API process
  (handy for a quick try, but not for real use).

The CLI and API read `.env` from the directory they are started in, so **run `simp` commands from `backend/`**.

Create the database schema and reference data:

```bash
uv run simp db upgrade
```

Expected output: `database at head; reference data seeded`. This creates every table (the order chain, ledger, NAV,
runs, review queues and TCA tables) and seeds the brokers, venues, the default desk policy, the two funds and your users.

Optional check that the install is healthy (needs the `simp_test` database; with Docker Postgres create it with
`docker compose exec db createdb -U simp simp_test`):

```bash
uv run pytest -q          # about 3 minutes; 1 test (golden replay) skips until you add the handoff CSVs
```

---

## 5. Load live data (WRDS + BMLL)

All commands run from `backend/` unless shown otherwise. The window below covers the handoff (bootstrap 2022-06-21 →
2026-06-30), with three months of warm-up for ADV and auction medians and a month after for the handoff comparison fill.

### 5.1 Verify WRDS

```bash
uv run simp data check-wrds
```

You should see the WRDS release range and `columns match contract` for each table. If WRDS asks you to approve the
first connection from a new machine, approve it and re-run.

- `SourceUnavailableError` means network or credentials. Re-check section 1's `nc` test and `.env`.
- `SchemaMismatchError … missing documented columns [...]` means your CIZ release uses a different column name. Edit it
  in `simp/data/lake/contracts.py`, bump that contract's `version`, and re-run.

### 5.2 Pull CRSP

```bash
uv run simp data pull --source live --crsp-only --start 2022-03-01 --end 2026-07-31
uv run simp data export-isins --out isins.csv
```

The first command pulls S&P 500 membership, daily prices and returns, security identifiers, distributions, delistings
and index returns for every member in the window (batched 150 PERMNOs per query) into `backend/lake/crsp/`. The second
writes `isins.csv` (ISIN = `US` + CUSIP9 + check digit) for the BMLL lookup.

### 5.3 Pull BMLL

The BMLL SDK needs NumPy < 2, while SIMP's core uses NumPy 2, so the puller has its own environment:

```bash
cd connectors/bmll_puller
uv sync
uv run python puller.py --isins ../../isins.csv --start 2022-03-01 --end 2026-07-31 --out ../../../lake_staging/bmll
cd ../..
```

Set the credentials in this shell first and test the login:

```bash
export BMLL_KEY_PATH=/secure/path/to/bmll_ssh_private_key   # <key>.pub must sit next to it
export BMLL_USERNAME=<your BMLL API username>                # from data.bmlltech.com; not your Mac user@host
# export BMLL_LAB_ORG_ID=<org id>                            # organisation / Data Lab accounts only
uv run python puller.py --check-auth                          # must print: BMLL login OK
```

This writes `reference_listing`, `daily_analytics` and `classified_trades` Parquet files to `App-SIMP/lake_staging/bmll/`,
then prints the row counts and columns.

Optional Data Lab datasets go in the same folder. Run them as Data Lab jobs; `lab_job_spec()` in `puller.py` builds
the spec:
- `auction_imbalance.parquet`: closing imbalance detail. Without it, imbalance is treated as zero.
- Trades Plus `@SIP`: required for the **T2** execution model on live data.

### 5.4 Register BMLL and (optionally) the official S&P 500 TR

```bash
uv run simp data register-bmll ../lake_staging/bmll
uv run simp data register-vendor-tr /path/to/sp500_tr.csv      # optional; CSV columns: caldt,tr_return
```

If registration reports `missing columns ['…']`, the SDK used a column name the puller does not know. Add it to `RENAME`
in `connectors/bmll_puller/puller.py`, then re-run 5.3–5.4.

### 5.5 Build the live snapshot

```bash
uv run simp data pull --source live --reuse-crsp --start 2022-03-01 --end 2026-07-31
```

This builds the point-in-time PERMNO → BMLL listing map, the daily market figures (ADV20/60, auction volume and median,
spreads, VWAP, fallback flags) and the venue mix. It then pins a **snapshot hash** and seeds the review queues.
The output looks like:

```json
{ "snapshot": "3f…", "source": "live", "corporate_actions": 8123, "pending": 14, "mapping_queue": 6 }
```

Every run you start from now on records this snapshot hash, so its numbers can always be reproduced.

> More detail on every data step, plus troubleshooting: [live-data-setup.md](live-data-setup.md).

---

## 6. Start the API and workers

Use two terminals, both in `backend/`:

```bash
# terminal 1: API on http://localhost:8000 (interactive API docs at /docs)
uv run uvicorn simp.api.app:app --host 127.0.0.1 --port 8000
```

```bash
# terminal 2: workers for runs and data pulls
uv run dramatiq simp.runs.worker --queues runs data -p 4 -t 1
```

Check the API: `curl localhost:8000/health` should return `{"status":"ok"}`, and
`curl localhost:8000/api/data/coverage` should list your `CRSP` / `BMLL` / `CRSP+BMLL` datasets.

`-p 4` gives four worker processes, so four runs at a time. Each four-year run needs about 2 GB of RAM.

---

## 7. Set up the web app

The web app is a Next.js server. The browser only talks to it, and it forwards `/api/*` to the FastAPI server. That
keeps sign-in cookies and the live progress stream on one origin, so no CORS setup is needed.

```bash
cd ../frontend
pnpm install --frozen-lockfile        # exact versions from pnpm-lock.yaml
```

**Development** (hot reload):

```bash
pnpm dev                              # http://localhost:3000
```

**Production build:**

```bash
SIMP_API_URL=http://127.0.0.1:8000 pnpm build
pnpm start                            # http://localhost:3000
```

`SIMP_API_URL` tells the web server where the API is. It defaults to `http://127.0.0.1:8000`. For a production build it
is read **at build time**, so rebuild if the API address changes.

If you change the API (new endpoints or fields), regenerate the typed client while the API is running:

```bash
pnpm gen:api
```

Open **http://localhost:3000**. The top bar shows the data badge: green **CRSP+BMLL** on live data, orange
**Synthetic data** otherwise. The **Overview** health strip shows the WRDS release, the BMLL pull, mapping coverage and
the current snapshot.

---

## 8. First run and verification

### 8.1 Clear the review queues first (Data page)

1. **Mapping review.** Aim for at least **99.5% of index weight mapped** (shown on the Overview). For each unmapped
   name, enter its BMLL `ListingId` and click **Approve fix**, or **Exclude** it. Typical causes are share classes,
   dotted tickers and ticker changes.
2. **Corporate-action review.** Dividends and splits are auto-approved. Spin-offs are approved at the CRSP distribution
   amount (used only with `keep_securities`). Rights wait for your decision. Mergers are always unsupported: SIMP sells
   the name before the merger instead of inventing a payoff.

### 8.2 Run the handoff baseline, then the full fund

In the UI: **Runs → New run**, pick the **Handoff replay** preset, step through to **Review**, and check that every
data-availability item is ticked. Then press **Run simulation**. The run page shows live progress (data check →
mapping → simulate → analyse) and finishes in about a minute. Repeat with **S&P 500 index fund, T1 costs, flows**.

Or from the CLI:

```bash
cd ../backend
uv run simp run --preset handoff_replay
uv run simp run --preset index_t1_flows
uv run simp report <RUN_ID>
```

### 8.3 What to look at

- **Fund**: tracking difference and error vs the benchmark, NAV chart, holdings vs target, flows.
- **TCA**: notional-weighted cost by size against liquidity, method comparison, heatmap, the one-date study
  (2026-03-20 by default).
- **Blotter**: expand any order PM → trader → child → fill, and click it for the full lineage with an intraday chart.
- **Studies**: run the **TD decomposition chain** and the **Provisional dividend rule** study.

### 8.4 Golden replay against the handoff (recommended)

```bash
uv run python scripts/import_handoff.py /path/to/BMLL_TCA_Supervisor_Handoff/
uv run pytest -m golden
```

This passes when SIMP reproduces the handoff's `portfolio_nav.csv` within $0.01 per day. If a run stops instead, the run
page names the error and the session where it happened. Common causes:
- `CorporateActionError … unclassified distribution`: add the CRSP `distype` code to `DISTYPE_MAP` in
  `simp/data/market.py`.
- `MissingPriceError`: a held name has no close; prices are never forward-filled.

---

## 9. Production deployment

### 9.1 Docker Compose (whole stack)

```bash
cd App-SIMP
cp backend/.env.example .env          # Compose reads ./.env for variable substitution
# edit .env: SIMP_DATA_SOURCE=live, SIMP_SESSION_SECRET, SIMP_AUTH_MODE=oauth (+ OAuth vars), WRDS_*, BMLL_KEY_PATH
docker compose up -d --build
docker compose run --rm api simp db upgrade
```

Load data into the stack's lake volume, from the repo root:

```bash
# CRSP + ISINs (inside the stack; WRDS credentials reach only the worker container)
docker compose run --rm worker simp data pull --source live --crsp-only
docker compose run --rm -v "$PWD:/out" worker simp data export-isins --out /out/isins.csv
```

Run the BMLL puller on the host (section 5.3) with `--isins isins.csv --out lake_staging/bmll`, then register it and build the snapshot:

```bash
docker compose run --rm -v "$PWD/lake_staging:/staging" worker simp data register-bmll /staging/bmll
docker compose run --rm worker simp data pull --source live --reuse-crsp
```

The services are web on `:3000`, API on `:8000`, Postgres on `:5432` and Redis on `:6379`. Data lives in the named
volumes `pg` (database) and `lake` (Parquet lake).

### 9.2 HTTPS and a domain

Put a reverse proxy in front of the **web** service only, and keep `:8000` private. With Caddy:

```
simp.example.com {
    reverse_proxy 127.0.0.1:3000
}
```

Set `SIMP_WEB_ORIGIN=https://simp.example.com` and rebuild the web image if the API address changed.

### 9.3 Real sign-in (GitHub OAuth)

1. GitHub → Settings → Developer settings → **OAuth Apps → New OAuth App**.
   - Homepage URL: `https://simp.example.com`
   - Authorization callback URL: `https://simp.example.com/api/auth/callback`
2. Set these in `.env`, then restart the API:
   - `SIMP_AUTH_MODE=oauth`
   - `SIMP_OAUTH_CLIENT_ID=…`
   - `SIMP_OAUTH_CLIENT_SECRET=…`
   - `SIMP_SESSION_SECRET=<random>`
   - `SIMP_ALLOWED_USERS=<github-login>:admin,<github-login>:researcher,…`
3. Visitors now see **Sign in with GitHub**. Only GitHub logins on the allow-list get in, with their role. Admins add
   people under Settings → Users.

### 9.4 Secrets

Keep `WRDS_PASSWORD`, `SIMP_OAUTH_CLIENT_SECRET`, `SIMP_SESSION_SECRET` and the BMLL key file in your secret store. Give
them only to the **worker** (WRDS/BMLL) and **api** (OAuth/session). The browser never receives them, and SIMP never
logs them.

### 9.5 Backups

Back up Postgres (`pg_dump simp`) and the lake volume or folder. Raw WRDS and BMLL data stays in the lake and is
licensed: keep it on your storage only. Exports from the app carry derived tables only and are watermarked.

---

## 10. Day-2 operations

| Task | How |
|---|---|
| Refresh data | Repeat 5.2 → 5.5 (or **Data → Pull now**). Each pull pins a new snapshot; old runs keep theirs. |
| Re-check a run reproduces | `uv run simp replay <RUN_ID>`. It exits 1 if the output hash differs. |
| Change trading rules | **Settings → Desk policy**: the YAML editor validates live. **Save as…** creates a variant that new runs can pick. |
| Add a user | **Settings → Users** (admin), or `SIMP_ALLOWED_USERS` before first start |
| Upgrade the code | `git pull`, `uv sync --frozen`, `uv run simp db upgrade`, `pnpm install --frozen-lockfile && pnpm build`, then restart the API, workers and web |
| Logs | The API and workers log JSON lines with `run_id` and session date. A failed run's error and failing session are on its page. |
| Tests | Backend: `uv run pytest`. Frontend: `pnpm test`. End to end with the stack running: `pnpm exec playwright test`. |

### Quick reference: ports and processes

| Process | Command (native) | Port |
|---|---|---|
| Postgres 16 | service / `docker compose up -d db` | 5432 |
| Redis 7 | service / `docker compose up -d redis` | 6379 |
| API | `uv run uvicorn simp.api.app:app --port 8000` | 8000 |
| Workers | `uv run dramatiq simp.runs.worker --queues runs data` | none |
| Web | `pnpm start` (or `pnpm dev`) | 3000 |
