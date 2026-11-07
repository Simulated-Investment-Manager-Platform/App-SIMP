# SIMP run guide: what to run, what each run means, how to show it

This guide assumes a fresh setup: the database was reset, `simp db upgrade` and
`simp data pull --source live --reuse-crsp …` have run, and the API, worker and frontend are up.
It lists the runs to make in order, what each one answers, where to look in the app, and how to
present the results to BMLL.

Fill in the **run log** at the end as you go. We'll go through the results run by run.

---

## 0. Before the first run (5 minutes)

Open the app at http://localhost:3000.

| Check | Where | What you want to see |
|---|---|---|
| Data source is live | **Overview**, data-health strip | The WRDS release in use, the BMLL pull status, and a snapshot hash. The data label reads **CRSP+BMLL**, not SYNTHETIC |
| Mapping coverage | **Data**, Mapping coverage | Close to 100% of index weight mapped. Names listed under **Unmapped names** are excluded from runs (they are listed, never dropped silently) |
| Corporate-action queue | **Data**, Corporate-action review queue | Cash dividends, splits and spin-offs are auto-approved. Rights issues are **PENDING** and mergers are **UNSUPPORTED** |
| Snapshot | **Data**, Snapshots | One snapshot pinned. Every run records this hash |

Pending corporate actions don't block runs. With the default *exclusion policy* (`exclude_securities`),
a name with an unreviewed or unsupported action is left out of the index decision, and the excluded
weight is reported. You only need to review them before the `keep_securities` runs (S4, and the last
step of S1).

---

## 1. What a run is

A run simulates the S&P 500 index fund (or the Phase 1 order generator) day by day over a window.
It is fully described by its **config**, the **data snapshot hash**, the corporate-action review set,
the desk policy and the code version. The same inputs give the same output hash.

Each session goes through these steps:

1. **Pre-open:** corporate actions (dividends, splits, spin-offs, delistings) and settlement.
   Settlement is T+2, and T+1 from 2024-05-28.
2. **Morning:** investor flows are drawn, and the PM makes decisions (quarterly rebalance, flow
   orders, cash sweep). Each decision becomes a **PM order**.
3. **Desk:** the desk policy turns PM orders into **trader orders** (MOC, VWAP algo, IS algo,
   high-touch, portfolio trade). Opposite flow and rebalance quantities are netted internally.
4. **Execution:** the execution model fills **child orders**, producing **fills**.
5. **Post-close:** NAV is computed twice (from the ledger and from the P&L path), and the two must
   agree. Then a lineage audit runs: every fill traces back to a decision.

A run stops (status **FAILED**, with the session and reason) on a missing price, an unclassified
corporate action on a held name, a cash shortfall, a NAV identity break, or look-ahead. These are
deliberate stops, never silent fixes.

### The settings that matter

| Setting | Options | What it changes |
|---|---|---|
| **Timing rule** | `HANDOFF` / `EFFECTIVE_DATE` | HANDOFF copies the notebook: decide on the Monday close, fill at Tuesday's close. EFFECTIVE_DATE trades at the index's effective close (third Friday; Juneteenth 2026 moves to Jun 18), deciding 5 sessions earlier |
| **Execution model** | `T0` `T1` `T2` (`T3`) | How fills are priced (next table) |
| **Flows** | preset `none` / `default` / `notebook_seeded` / `custom` / file | Investor subscriptions and redemptions. The default is a daily AR(1) process with Student-t shocks: σ 10 bps a day, plus a 0.5%-a-day chance of a −50 to −200 bps redemption jump. Flows are dealt at the pre-flow unit NAV |
| **Management fee** | bps per year | Accrued daily out of NAV |
| **Exclusion policy** | `exclude_securities` / `keep_securities` | Leave out, or keep, names with pending or unsupported corporate actions |
| **Provisional dividend rule** | on / off | Whether a declared but not yet confirmed dividend is booked |
| **Benchmark** | `CRSP_VWRETD` / `SP500_TR` | CRSP's cap-weighted S&P 500 with dividends, or the official TR if a vendor file was registered |
| **Desk policy** | `default` | The routing rules, shown and editable in **Settings** |
| **Cost parameters** | Y, κ, β, λ, auction cap, T2 interval cap | Calibration of the cost models |

### Execution models

| Model | Fill price | What it is for |
|---|---|---|
| **T0** | The close, at zero cost | The notebook's world. It isolates the return path from trading |
| **T1** | Closing auction: `κ·σ·(Q/V_auc)^β·(1+λ·same-side-as-imbalance)`. Continuous: `½ spread + Y·σ·√(Q/ADV)` | Daily-data cost model using BMLL auction price, auction volume, spread and volatility. The workhorse |
| **T2** | The algo slices its order through the day against **real BMLL Trades Plus minute bars**, at most 10% of each minute's real volume. Aggressive slices pay half the spread, and prices come from the NBBO mid | A check on T1 using intraday liquidity. Bars are fetched from BMLL automatically during the run and cached |
| **T3** | BMLL simulator (`bmll2.simulation`), Data Lab only | Not available in the app yet |

**How the default desk routes the index fund's orders:**
- Rebalance orders are `TARGET_CLOSE`. A small basket goes to a portfolio trade (Broker C, fixed fee,
  guaranteed close). An order that fits within 5% of expected auction volume goes to the closing
  auction (MOC). A larger order sends 5% of expected auction volume to MOC and works the remainder
  with a **VWAP algo from 13:00 to 15:50**.
- Flow and cash-sweep orders go to MOC.

So for the index fund, **T2 differs from T1 only on the VWAP remainder of the large orders**. If the
T1 vs T2 gap is small, that is itself a finding: almost all of an index fund's cost is in the auction.

---

## 2. The runs to make, in order

Five single runs, then five studies, for **30 runs** in total. Single runs are started from
**Runs → New run** with a **Preset**. Studies are started from **Studies**, and every study's base
config is the `index_t1_flows` preset.

### R1. Handoff replay: does SIMP reproduce the notebook?

- **New run → Preset: Handoff replay**, which sets HANDOFF timing, T0, no flows and no fee.
- **Question:** with the notebook's own assumptions, does SIMP rebuild the notebook's fund?
- **Compare against the handoff:**

  | Measure | Handoff value |
  |---|---|
  | Ending NAV | $21.07bn, unit NAV 210.72 (+107.2%) |
  | Sessions | 1,010 |
  | Annualised volatility | 16.2% |
  | Max drawdown | −18.9% |
  | Orders | 8,452 (484 initial + 7,968 rebalance over 16 decision dates) |
  | Concentration | Top 10% of rebalance orders carry about 85% of the $5.74bn traded |
  | Excluded index weight | 0.6–3.3% per quarter |

- **Where:** **Fund** (KPIs, NAV vs benchmark), **Runs → run** (summary, audit), **Blotter** (order count).
- **What it signifies:** this establishes trust. Every later number differs from this one only by the
  effects we switch on. A close match means the accounting, corporate actions and timing are right.
  The strict, order-by-order golden test against the notebook's CSVs is
  `scripts/import_handoff.py` + `pytest -m golden`. That needs the handoff files, and we can run it
  together.

### R2. Index fund, T0, effective-date timing: the timing effect

- **Preset: Index T0 effective-date**: T0, no flows, no fee, EFFECTIVE_DATE timing.
- **Question:** what does trading at the index's effective close, instead of the Tuesday after a
  Monday decision, do to tracking difference?
- **Read:** compare TD with R1 (**Runs → Compare**, pick R1 and R2). **Timing effect = TD(R1) − TD(R2)**.
- **What it signifies:** trading on the day the index itself rebalances removes most of the drift
  between decision and fill. This is the benchmark-tracking baseline before any trading cost. The
  brief expects TD within **±20 bps** before costs.

### R3. Index fund, T1, flows: the headline realistic run

- **Preset: Index T1 flows**: EFFECTIVE_DATE, T1, default flows, 3 bps fee, the default desk.
- **Question:** what does it cost to run a $10bn S&P 500 fund through real closing auctions and
  intraday markets, with investor flows, and how much tracking difference does that cost?
- **Where:**
  - **Fund**: TD net vs gross, TE, the TD decomposition, cash drag, flows panel, weights vs target,
    excluded names.
  - **TCA**: the main page (section 3).
  - **TCA → One rebalance date**: one quarter, order by order.
  - **Blotter**: PM order → trader orders → child orders → fills, with lineage.
- **What it signifies:** cost drag (**TD(T0) − TD(T1)** on the same orders) is the price of real
  execution. The TCA shows where that cost comes from: the auction or continuous trading, which size
  buckets, and which names.

### R4. The same as R3, but T2: the real intraday-liquidity check

- **New run → Preset: Index T1 flows, then change Execution model to T2.** Start it right after R3
  finishes. It prefetches bars for R3's orders in one batch (the run shows stage `fetch_bars`), then
  fetches anything still missing day by day.
- The first T2 run is the slow one, because it downloads from BMLL. Later T2 runs reuse the cache.
- **Question:** does replaying real minute volume and real mids change the cost estimate from T1?
- **Read:** **Runs → Compare R3 vs R4** (exec cost, TD). Then the TCA *Method comparison* and the
  *cost by size against liquidity* charts for both runs.
  - The **fallback report** (TCA, provisional banner) shows how much notional had no bars
    (`INTRADAY_FALLBACK_DAILY`).
  - Above 1% of notional on fallbacks, the TCA is marked *provisional*.
- **What it signifies:** T1 is a model, and T2 is that model checked against what the market actually
  traded each minute. Agreement makes the T1 numbers more credible. A gap shows where the square-root
  model is too optimistic or too pessimistic, for example on large orders in thin afternoons.

### R5. Phase 1 order generator: the known-answer test of the TCA itself

- **Preset: Phase 1 generator**: a synthetic fund, January to March 2026, T1.
- It sends random multi-venue order chains with **costs injected on purpose**: VWAP-day, arrival and
  target-close orders, with Pareto-distributed sizes (10% of orders carry about 85% of notional, as in
  the handoff). The generator keeps the true injected cost of every order.
- **Question:** if we know the true cost, does the TCA measure it?
- **Read:** **TCA** for this run. The measured notional-weighted cost should recover the injected cost
  **within 0.5 bps**.
- **What it signifies:** this tests the measuring instrument, not the market. If the TCA can't recover
  a cost we planted, nothing it says about R3 and R4 can be trusted. It is the first thing to show a
  skeptical quant.

### Studies (from Studies → choose a kind → Start)

| ID | Study | Runs | What changes | Question it answers | Where to read |
|---|---|---|---|---|---|
| **S1** | **TD decomposition chain** (`counterfactual_chain`) | 6 | Cumulative steps: handoff baseline (HANDOFF, T0, no flows, no fee) → + effective-date timing → + flows → + T1 costs → + 3 bps fee → + keep securities with approved corporate actions | How much of the fund's tracking difference each effect causes, in a fixed, printed order (effects interact, so the order matters) | **Studies → Results** waterfall; **Fund →** TD decomposition |
| **S2** | **Cost-model parameters** (`cost_params`) | 9 | Base; Y, κ, β each at ×0.5 and ×1.5; MOC auction share at 2% and 10% | How sensitive are total cost and TD to the cost model's calibration? Which parameter matters most? | **Studies → Results**; **Compare** |
| **S3** | **Flow regime** (`flow_regime`) | 6 | Flow volatility σ of 5, 10 and 25 bps a day × cash buffer of 10 and 50 bps | How investor-flow intensity and the cash buffer drive cost, cash drag and TE | **Studies → Results**; **Fund →** flows panel |
| **S4** | **Exclusion policy** (`exclusions`) | 2 | `exclude_securities` vs `keep_securities` | What leaving out names with unresolved corporate actions (0.6–3.3% of index weight per quarter in the handoff) costs in TD | **Studies → Results**; **Fund →** Index weight excluded per decision |
| **S5** | **Provisional dividend rule** (`dividend_rule`) | 2 | Rule on vs off | The NAV, TD and TE effect of booking declared but unconfirmed dividends | **Studies → Results** |

For S4 and S1's last step, review the **PENDING** items in **Data → Corporate-action review queue**
first. `keep_securities` only keeps names whose actions are approved.

Studies run on the `index_t1_flows` base, so they price with **T1**. Run them after R3 and R4, so you
already know what T1 vs T2 looks like on the base case.

---

## 3. Navigating the app

| Page | What it is | What to look at |
|---|---|---|
| **Overview** | Funds and recent runs, data-health strip | AUM, TD net, TE and exec cost of the latest run; WRDS/BMLL/mapping status |
| **Fund** | The fund's report for a chosen run | TD net/gross, TE, return, vol, drawdown, Sharpe, ending NAV; fund vs benchmark; daily active return; rolling 63-day TE; **TD decomposition**; excluded index weight per decision; weights vs target; flows |
| **Runs** | All runs, status, stage, progress | Click a run for its record (config, data hashes, code version, output hash) and live log. **New run** starts one; **Compare** shows runs side by side with the TD change per step |
| **Blotter** | Every order at four levels | PM order → trader orders → child orders → fills. Click a row for its lineage (which decision, which policy rule, which venue). Columns show notional, %ADV, %auction, weighted cost |
| **TCA** | Cost analysis for one run, all notional-weighted | Method comparison; cost by size against liquidity; %ADV × %auction heatmap; cost vs Q/auction volume; concentration; netting savings; cost by date beside TD; IS breakdown (delay, execution, explicit, opportunity); fallback report |
| **TCA → One rebalance date** | One quarter end to end | Each order's auction price and volume, imbalance, ADV, spread, VWAP; three repricings; MOC cost vs Q/auction volume; the largest orders by name; a T2 replay of the top ten (live data, when bars exist) |
| **Market explorer** | One security's BMLL data | Close vs VWAP, auction volume (spikes on index dates), intraday mid, venue share, PERMNO ↔ BMLL mapping history, corporate actions |
| **Studies** | Sensitivity and decomposition sets | Start a study; read its results table and waterfall |
| **Data** | Lake, snapshots, review queues | Datasets and manifests, snapshots, pull jobs, unmapped names, corporate-action review |
| **Settings** | Brokers, venues, users, desk policy | Commission schedules, venue fees, the allow-list, the desk policy YAML (validated live; each run records its hash) |

### Reading the TCA (the vocabulary)

- **Notional-weighted:** every average is weighted by traded dollars. The top 10% of orders carry
  about 85% of the dollars, so a simple average would let 530 orders under $10k outvote a $104m order.
  Confidence intervals come from a block bootstrap by date (2,000 resamples).
- **Implementation shortfall (IS) = delay + execution + explicit + opportunity**, against the PM's
  decision price:
  - **delay:** decision price to arrival mid. For orders decided at a prior close, this is the
    handoff's overnight "slippage" (−250 to +180 bps). It is market drift, not trading cost, and is
    reported separately.
  - **execution:** arrival mid to average fill price. This is the trading cost.
  - **explicit:** commissions and venue fees.
  - **opportunity:** the cost of quantity left unfilled at the deadline.
- **Repricings (Method comparison):** the same orders repriced as if filled at:
  - **T0** (the close, zero-cost baseline),
  - **MOC** (in the closing auction),
  - **VWAP** (at the day VWAP),
  - **ARRIVAL** (at the arrival mid),
  - **HANDOFF** (the notebook's next-close fill).

  **ACTUAL** is what the run's model produced. *Where the methods disagree most* lists the orders
  whose benchmark choice changes the answer the most.
- **Sign convention:** positive cost means worse. Costs are in bps with $ beside them.
- **Size buckets:** order size as % of ADV20 and as % of expected closing-auction volume.
- **Netting:** opposite-side flow and rebalance quantity crossed internally at the close. The saving
  is reported in $ and bps of NAV.
- **Provisional:** more than 1% of notional used a fallback: auction price from CRSP, auction volume
  from the 20-day median, or no Trades Plus bars in T2.

### Reading the fund report

- **TD (tracking difference):** annualised fund return minus annualised benchmark return. It is
  reported **net** (after costs and fees) and **gross** (before). Negative means the fund lagged.
- **TE (tracking error):** standard deviation of the daily fund-minus-benchmark return, × √252.
- **Cost drag:** TD(T0) − TD(Tk) on the same orders.
- **Cash drag:** return lost to the cash buffer and undeployed flows.
- **Timing effect:** TD(handoff timing) − TD(effective-date timing).

---

## 4. Showing this to BMLL

### What the story is

1. **SIMP turns a research notebook into a working asset manager on BMLL data.** Every decision
   becomes a PM order, then trader orders, child orders and fills, executed against BMLL closing
   auctions and intraday markets.
2. **It reproduces the notebook first** (R1), so every later difference is attributable.
3. **It measures execution cost the way a desk would.** Implementation shortfall is split into stages,
   notional-weighted, and repriced several ways. The measuring instrument is itself validated with
   planted costs (R5).
4. **It puts cost beside tracking difference** (R3, S1). The question an index manager actually asks
   is "how many bps of TD does trading cost me, and where?"
5. **Real intraday liquidity** (R4) checks the cost model against Trades Plus minute by minute.

### A suggested demo path (15 minutes)

1. **Overview**: data health (CRSP+BMLL, snapshot hash, mapping coverage).
2. **R1 on Fund**: "this is the notebook's fund, rebuilt."
3. **S1 waterfall**: where TD comes from, one effect at a time.
4. **R3 on TCA**: cost by size against liquidity, and the %ADV × %auction heatmap.
5. **TCA → One rebalance date**: the largest orders by name; MOC cost vs Q/auction volume.
6. **Blotter**: click one large order and walk its lineage down to the fills.
7. **R3 vs R4 in Compare**: the T1 model against real Trades Plus liquidity.
8. **R5**: "and here is the proof the TCA measures what we plant."
9. **Market explorer**: one name's auction volume around index dates, venue share, mapping history.

### What to say up front (the honest caveats)

- **Costs are modelled, never actual executions.** T1 is a calibrated model and T2 a replay against
  real prints. Neither is a real fill.
- **Auction imbalance is treated as zero.** The Auction Imbalance detail dataset isn't pulled yet, so
  the same-side-imbalance term (λ) of the auction-impact model never applies. This understates MOC
  cost on days when the fund trades with the imbalance.
- **T3 (the BMLL simulator) is not wired in yet.**
- **The benchmark is the CRSP cap-weighted S&P 500 proxy** unless an official S&P 500 TR file was
  registered (**Data**).
- **Excluded names:** names with unresolved corporate actions or no BMLL mapping are left out and
  reported (S4 quantifies it).
- Every run carries its data snapshot hash, the review set hash, the desk policy hash and the code
  version, so any number can be rebuilt.

### Access and data rights

- Give BMLL colleagues **viewer** accounts: **Settings → Allow-list**, or GitHub OAuth for a hosted
  setup (`SIMP_AUTH_MODE=oauth`). Viewers can read everything but can't start runs.
- **Exports refuse raw vendor data and are watermarked.** Share charts, aggregates and order-level
  TCA, not CRSP or BMLL extracts. WRDS and BMLL licences apply to the underlying data.

---

## 5. Run log (fill this in)

| ID | What | Preset / study | Run or study ID | Status | TD net (bps) | TE (bps) | Exec cost (bps, wtd) | Notes |
|---|---|---|---|---|---|---|---|---|
| R1 | Handoff replay | `handoff_replay` | | | | | 0 (T0) | Ending NAV vs $21.07bn: |
| R2 | T0, effective-date timing | `index_t0_effective` | | | | | 0 (T0) | Timing effect vs R1: |
| R3 | T1, flows (headline) | `index_t1_flows` | | | | | | |
| R4 | T2, flows | `index_t1_flows` + T2 | | | | | | Fallback share: |
| R5 | Generator known-answer | `phase1_generator` | | | n/a | n/a | | Recovered vs injected: |
| S1 | TD decomposition chain | `counterfactual_chain` | | | | | | |
| S2 | Cost parameters | `cost_params` | | | | | | |
| S3 | Flow regime | `flow_regime` | | | | | | |
| S4 | Exclusion policy | `exclusions` | | | | | | |
| S5 | Dividend rule | `dividend_rule` | | | | | | |

When a run fails, note the **session** and **error type** from its log. These are deliberate stops,
and each one tells us something about the data.
