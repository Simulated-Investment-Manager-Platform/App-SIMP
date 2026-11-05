# Live data setup: WRDS (CRSP) + BMLL

This guide takes SIMP from the bundled **synthetic** lake to **live CRSP (via WRDS) and BMLL** data.
Every step is a command in this repository, and each step says what success looks like and what to do when it fails.

The flow:

```
WRDS (CRSP CIZ) --1--> lake/crsp/*        --2--> isins.csv --3--> BMLL puller (isolated env)
                                                                    |
vendor S&P 500 TR (optional) --5--> lake/vendor/sp500_tr            v
                                                     lake_staging/bmll/*.parquet --4--> lake/bmll/*
                                                                    |
                         --6--> derived/security_map, market_daily, venue_share + pinned snapshot
                         --7--> review queues (mapping, corporate actions) --8--> runs
```

---

## 0. What you need

| Item | Detail |
|---|---|
| **WRDS account** | With a **CRSP CIZ (v2)** subscription: schemas `crsp_q_stock` (`dsf_v2`, `stksecurityinfohist`, `stkdistributions`, `stkdelists`) and `crsp_q_indexes` (`dsp500list_v2`, `dsp500`). If `dsp500` is not in `crsp_q_indexes` in your release, the loader falls back to legacy `crsp.dsp500`. |
| **BMLL account** | **Data Lab SDK** access with an **SSH key registered** to your account. Entitlements for US equities: daily analytics and classified trades on the primary venues (XNYS, XNAS, ARCX, BATS) plus `@SIP`. Optional: Trades Plus `@SIP` (needed for T2 on live data) and the Auction Imbalance dataset. |
| **Official S&P 500 TR** (optional) | A licensed daily total-return series (Compustat or another vendor). It is benchmark (b); the default benchmark (a) is CRSP `vwretd`. |
| **Network** | Outbound **TCP to `wrds-pgdata.wharton.upenn.edu:9737`**. This is the raw Postgres protocol, so HTTPS-only or TLS-intercepting proxies will not work. You also need outbound HTTPS to BMLL. |
| **Software** | Python 3.12 + [uv](https://docs.astral.sh/uv/), Node 22 + pnpm, Postgres 16, Redis 7 (or `docker compose`). |

> The sandbox SIMP was built in could reach neither WRDS nor BMLL. The live connectors are
> implemented against the documented APIs and covered by an offline test
> (`backend/tests/test_live_path.py`), but **your first live pull is the first real contact**.
> Steps 1 and 4 are built to fail loudly, naming any column or table that differs.

---

## 1. Install and configure secrets

```bash
git clone <repo> && cd App-SIMP/backend
uv sync --all-extras
cp .env.example .env
```

Edit `backend/.env` (it is git-ignored; in production use your secret store):

```ini
SIMP_DATABASE_URL=postgresql+psycopg://simp:simp@127.0.0.1:5432/simp
SIMP_REDIS_URL=redis://127.0.0.1:6379/0
SIMP_DATA_SOURCE=live
WRDS_USERNAME=your_wrds_user
WRDS_PASSWORD=your_wrds_password
BMLL_KEY_PATH=/secure/path/to/bmll_ssh_private_key
```

Credentials are read only in `simp/settings.py`. They are never logged, returned by the API or stored on a run.
WRDS may ask you to approve the first connection from a new machine (multi-factor); approve it and re-run.

Then create the database:

```bash
uv run simp db upgrade      # Alembic migrations + brokers, venues, desk policy, users
```

---

## 2. Verify WRDS before pulling anything

```bash
uv run simp data check-wrds
```

**Success:**

```
WRDS release covers 1925-12-31..2026-xx-xx
  dsf_v2: columns match contract
  stksecurityinfohist: columns match contract
  ...
```

**Failures:**

| Error | Meaning | Fix |
|---|---|---|
| `SourceUnavailableError: cannot reach WRDS ...` | Network or credentials | Check that TCP 9737 is open and `WRDS_USERNAME`/`WRDS_PASSWORD` are set; approve any MFA prompt |
| `SchemaMismatchError: crsp_q_stock.dsf_v2 is missing documented columns [...]` | Your CIZ release names a column differently | Edit the column in `backend/simp/data/lake/contracts.py` (or mark it `optional`), **bump that contract's `version`**, re-run |

---

## 3. Pull CRSP (step 1 of the live pull)

```bash
uv run simp data pull --source live --crsp-only --start 2022-03-01 --end 2026-07-31
```

- The window starts three months before the 2022-06-21 bootstrap so ADV20/ADV60 and the
  20-session auction median are warm. It ends after June 2026 so the handoff's Tuesday-close
  comparison fill exists.
- Members of the S&P 500 at any point in the window are pulled in batches of 150 PERMNOs.
  The loader casts WRDS `numeric` values to the lake types and checks each table's contract on write.
- A window outside the WRDS release raises `DataWindowError` (both bounds are always checked).

## 4. Export the ISIN list for BMLL

```bash
uv run simp data export-isins --out isins.csv
```

ISIN = `US` + CUSIP9 + check digit, one row per `stksecurityinfohist` interval, so CUSIP changes are covered.

---

## 5. Pull BMLL with the isolated puller

The `bmll` SDK (≥ 1.25) pins NumPy < 2 while SIMP's core uses NumPy 2, so the puller has its own environment.

```bash
cd connectors/bmll_puller
uv sync
export BMLL_KEY_PATH=/secure/path/to/bmll_ssh_private_key
uv run python puller.py --isins ../../isins.csv --start 2022-03-01 --end 2026-07-31 --out ../../../lake_staging/bmll
cd ../..
```

It runs three steps and prints row counts and columns:

1. `bmll.reference.query(object_type="Listing", ISIN=[...])` → `reference_listing.parquet`
   (primary listing on the OPOL, the `@SIP` listing and siblings).
2. `bmll.time_series.query(metric=[VWAP, TWAP, ClosingAuctionPrice|Executed, Spread|RelTWA, Volatility|Intraday, SweepToFill|*, AuctionOrderImbalance|*, AuctionDislocation|ClosingLast, Volume], frequency="D")` on the primary listings → `daily_analytics.parquet`.
3. `bmll.time_series.classified_trades(notional_currency="USD")` → `classified_trades.parquet`.
   This supplies closing-auction volume (`LIT_CLOSING_AUCTION`), the venue mix by `ExecutionVenue`, and the volume for ADV.

**Optional Data Lab datasets** (run as scheduled Data Lab jobs; `lab_job_spec()` in `puller.py`
builds the spec; submit with `bmll.compute.create_job` inside the Lab):

- **Auction Imbalance:** save it as `lake_staging/bmll/auction_imbalance.parquet` with columns
  `ListingId, Date, ImbalanceSide, ImbalanceQuantity, PairedQuantity, ReferencePrice, IndicativeUncrossingPrice`.
  Without it SIMP still runs, but closing imbalance is zero, so the auction-impact λ term never applies.
- **Trades Plus `@SIP`:** needed for **T2** on live data. Size the pull first with
  `estimate_trades_plus(...)` (`bmll.data.query(..., estimate=True)`).
- **T3** (`bmll2.simulation`) only runs inside the Lab. `uv run simp lab t3-job RUN_ID` writes the order list for it.

## 6. Register the BMLL files

```bash
uv run simp data register-bmll ../lake_staging/bmll
```

Each file is cast to the contract types, checked and written under `lake/bmll/` with a manifest.
If the SDK returned a column under a name the puller does not know, this fails with
`ContractViolation: missing columns ['…']`. Add the SDK name to `RENAME` in
`connectors/bmll_puller/puller.py` (e.g. `"ObjectId": "ListingId"`) and re-run steps 5–6. Only the renamed files change.

## 7. (Optional) Official S&P 500 Total Return

```bash
uv run simp data register-vendor-tr sp500_tr.csv     # columns: caldt,tr_return (daily decimal return)
```

Runs can then pick `benchmark: SP500_TR`, and the fund report shows the cap-weight-proxy gap (a) − (b).

## 8. Build the live snapshot

```bash
uv run simp data pull --source live --reuse-crsp --start 2022-03-01 --end 2026-07-31
```

This reuses the CRSP pull from step 3, builds the PERMNO → BMLL map (ISIN first, then ticker + OPOL
with a 1 bp CRSP-vs-BMLL close check on five sample days), `market_daily` (ADV20/60, auction
median, fallback flags) and `venue_share`. It pins a **snapshot hash** and seeds the review queues:

```json
{"snapshot": "…", "source": "live", "corporate_actions": 8xxx, "pending": n, "mapping_queue": m}
```

Every dataset is now labelled `CRSP+BMLL` instead of `SYNTHETIC`, and the UI badge turns green.

---

## 9. Review queues (do this before trusting a run)

Start the stack (API, worker, web; see the README) and open **Data**.

1. **Lake & snapshots:** every dataset shows `CRSP` / `BMLL` / `CRSP+BMLL`, with first and last dates covering the window.
2. **Mapping review.** Target: **≥ 99.5% of traded notional mapped** (shown on the Overview health strip and on every run).
   For each open name, enter the correct BMLL `ListingId` and **Approve fix**, or **Exclude**. Typical causes: share classes
   (GOOG/GOOGL), dotted tickers (BRK.B), ticker changes. Runs exclude unmapped names (listed) or stop, depending on
   `unmapped_policy`.
3. **Corporate-action review.** Cash dividends and splits are auto-approved. Spin-offs are approved for
   `keep_securities` at the CRSP distribution amount. Rights are pending. Mergers are always unsupported
   (no merger payoff is ever invented). The review set is hashed into every run.

**Check the distribution-type mapping once per release.** The engine understands `CD` (cash dividend),
`FS` (split), `SO` (spin-off), `RI` (rights) and `MG` (merger/delisting). Other CRSP `distype` codes are
classified from their amounts (a share factor alone means a split, a cash amount alone means a dividend).
Anything else becomes `UNK` and **stops the run** if the fund holds the security, naming the PERMNO and ex-date.
Add the code to `DISTYPE_MAP` in `backend/simp/data/market.py`. Also confirm two unit conventions on a known name:
`shrout` should be in thousands (SIMP multiplies by 1,000), and `primaryexch` codes should map through `EXCH_TO_MIC`
in `simp/data/mapping/isin.py` (N→XNYS, Q→XNAS, A→XASE, P→ARCX, Z→BATS).

## 10. Run on live data

```bash
uv run simp run --preset handoff_replay      # T0 + handoff timing: the golden-replay baseline
uv run simp run --preset index_t1_flows      # effective-date timing, flows, T1 costs
uv run simp study counterfactual_chain       # TD decomposition (worker queue)
```

Or use **Runs → New run** in the UI. The review step's data-availability check confirms the snapshot,
window, execution-model availability (T2 needs Trades Plus; T3 is Lab-only) and the mapping queue.

**Golden replay against the handoff:**

```bash
uv run python scripts/import_handoff.py /path/to/BMLL_TCA_Supervisor_Handoff/
uv run pytest -m golden
```

It checks that `portfolio_nav.csv` is reproduced within $0.01 per day over the 1,010 sessions, and checks the order count against `orderbook.csv`.

**Reproducibility:** `uv run simp replay RUN_ID` re-runs the same config and snapshot, and exits 1 unless the output hash matches.

---

## Rights and refreshes

- Raw WRDS and BMLL rows stay in `lake/` (git-ignored). Exports are limited to derived, order-level and aggregate
  tables, are watermarked "derived from CRSP (WRDS) and BMLL; licensed — not for redistribution", and refuse raw tables.
- To refresh, repeat steps 3–8. Each pull writes new manifests and a new snapshot hash. Old runs keep pointing at
  their old snapshot, so their numbers never change under them.
- Docker: put the same variables in the worker container's environment (secrets reach only the workers; see
  `docker-compose.yml`), then `docker compose run --rm worker simp data pull --source live ...`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `psql`/loader hangs, then times out | Port 9737 blocked or a proxy in the path; WRDS needs direct TCP |
| `DataMissingError: BMLL daily_analytics not pulled` | Run steps 5–6 before step 8 |
| `DataMissingError: no CRSP dsf_v2 in the lake` with `--reuse-crsp` | Run step 3 first |
| Mapping coverage well below 99.5% | Entitlements missing for a venue, or `primaryexch` codes not in `EXCH_TO_MIC` |
| Run fails `CorporateActionError … unclassified distribution` | Add the CRSP `distype` code to `DISTYPE_MAP` |
| Run fails `MissingPriceError` | A held name has no CRSP close that session; prices are never forward-filled. Check the pull window or the exclusion |
| TCA marked *provisional* | Over 1% of notional used a BMLL fallback (auction price from CRSP, or auction volume from the 20-day median). Pull the missing BMLL days |
| T2 option rejected | Trades Plus `@SIP` not in the lake |
