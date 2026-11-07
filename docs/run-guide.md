# SIMP run guide (live data): every click, what each run means, how to show it

Every run in this guide uses **live data**: CRSP from WRDS and BMLL market data. Nothing here uses
the synthetic stand-in.

The guide assumes a fresh setup: the database was reset, `simp db upgrade` and
`simp data pull --source live --reuse-crsp …` have run, and the API, worker and frontend are up.

Fill in the **run log** (section 6) as you go. We'll go through the results run by run.

---

## 0. One-time setup for live-only runs

**1. Tell the app it is live.** Add this line to `backend/.env`, then restart the API and the worker:

```
SIMP_DATA_SOURCE=live
```

With this set, every run uses the newest **live** snapshot. A synthetic snapshot left in the lake can
never be picked up, and the Data page's pull selector defaults to `live`.

**2. Don't click Data → Pull jobs → Pull now.** With `live` it re-queries WRDS from scratch, which is
slow and unnecessary. With `synthetic` it creates a synthetic snapshot. To rebuild the live snapshot
from data you already have, use the terminal (from `backend/`):

```bash
uv run simp data pull --source live --reuse-crsp --start 2022-03-01 --end 2026-06-30
```

**3. For T2 (run R4)**, the shell that starts the API and the worker needs `uv` on its PATH and
`BMLL_KEY_PATH`, `BMLL_USERNAME` and `BMLL_LAB_ORG_ID`, exported or in `backend/.env`.

**4. Check the data in the app** (http://localhost:3000):

| Check | Click | What you want to see |
|---|---|---|
| Live data in use | **Overview** (left nav), data-health strip | A WRDS release, the BMLL pull status, a snapshot hash, and a data label of **CRSP** or **CRSP+BMLL**, never **SYNTHETIC** |
| Data source | **Data** (left nav), top stats | *Data source*: **live** |
| Mapping coverage | **Data** → tab **Mapping review** | Unmapped names are listed here. Runs exclude them (they are never dropped silently) |
| Corporate actions | **Data** → tab **Corporate-action review** | Cash dividends, splits and spin-offs are auto-approved. Rights issues show **PENDING** and mergers **UNSUPPORTED** |
| Snapshots | **Data** → tab **Lake & snapshots** | Your live snapshot at the top. Every run records its hash |

---

## 1. How to create any run: the New run wizard

**Runs** (left nav) → **New run** (top right). The wizard has a row of **preset buttons** at the top,
and seven numbered steps that you move through with **Next** / **Back**:

`1 Fund · 2 Window · 3 Timing rule · 4 Flows · 5 Desk policy · 6 Execution model · 7 Review`

**For every run:**

1. Click the **preset button** named in the run's section. This resets *every* field to that preset,
   so always click it first.
2. Go through steps 1–6 and make the field values match the run's table. Fields marked **change**
   differ from the preset; the rest are only there to check.
3. On step **1 Fund**, set **Run name** to the name given (for example `R1 Handoff replay`), so runs
   are easy to find later.
4. Step **7 Review** runs the *data-availability check*. Every line needs a green tick, and the
   **execution model** line must end in **"… on CRSP data"** (live CRSP; BMLL comes with it). If it
   ends in **"… on SYNTHETIC data"**, stop: step 1 of section 0 isn't in effect yet.
5. Click **Run simulation** (it stays disabled until every check passes).
6. You land on the run's page. Its stage goes `queued → data_check → mapping → (fetch_bars) → simulate
   → analyse → done`, with progress and a log. Copy the run ID into the run log.

On the flows step, **don't type into the numeric flow fields** unless the run says to: typing in any
of them switches the flow preset to `custom`.

**Default values, which most runs keep:**

| Step | Field | Default |
|---|---|---|
| 1 Fund | Seed | `42` |
| 1 Fund | Initial AUM ($) | `10000000000` |
| 2 Window | Bootstrap / Start / End | `2022-06-21` / `2022-07-01` / `2026-06-30` |
| 3 Timing rule | Decision offset (sessions before effective) | `5` |
| 3 Timing rule | Initial execution offset (sessions) | `1` |
| 3 Timing rule | Benchmark | `(a) CRSP cap-weighted with dividends` |
| 3 Timing rule | Exclusion policy | `exclude_securities` |
| 3 Timing rule | Provisional dividend rule | `on` |
| 4 Flows | Netting of flow vs rebalance orders | `on` |
| 5 Desk policy | Desk policy | `default` |
| 5 Desk policy | MOC share of expected auction volume (override) | *blank* (policy default 5%) |
| 6 Execution model | Y / kappa / beta / lam / auction_cap / t2_interval_cap | `0.7` / `0.5` / `0.5` / `0.5` / `0.25` / `0.1` |

---

## 2. The five runs, click by click

### R1. Handoff replay: does SIMP reproduce the notebook on live data?

**Preset button:** `Handoff replay (T0, handoff timing, no flows)`

| Step | Field | Value |
|---|---|---|
| 1 Fund | Fund | `S&P 500 index fund` |
| | Run name | **change** → `R1 Handoff replay` |
| | Strategy | `index_replication` |
| | Management fee (bps / yr) | `0` |
| 2 Window | Bootstrap / Start / End | `2022-06-21` / `2022-07-01` / `2026-06-30` |
| 3 Timing rule | Rule | `HANDOFF` |
| | Everything else | defaults |
| 4 Flows | Preset | `none (handoff)` |
| 5 Desk policy | | defaults |
| 6 Execution model | Model card | **T0** (highlighted) |
| 7 Review | Execution model line | `T0 available on CRSP data` |

**What it answers:** with the notebook's own assumptions (decide on the Monday close, fill at
Tuesday's close, zero cost, no investor flows, no fee), does SIMP rebuild the notebook's fund from
live CRSP?

**Compare with the handoff:** ending NAV $21.07bn (unit NAV 210.72, +107.2%); 1,010 sessions;
volatility 16.2%; max drawdown −18.9%; 8,452 orders (484 initial + 7,968 rebalance over 16
decisions); top 10% of rebalance orders carrying about 85% of $5.74bn traded; 0.6–3.3% of index
weight excluded per quarter.

**Where:** **Fund** (pick R1 in the run selector at the top): KPIs and *Fund vs benchmark*. Then
**Runs** → R1, for the run record and lineage audit. Then **Blotter** (pick R1), for the order count.

**What it signifies:** trust. Every later number differs from R1 only by the effects we switch on.

### R2. Effective-date timing: the timing effect

**Preset button:** `S&P 500 index fund, T0, effective-date timing`

| Step | Field | Value |
|---|---|---|
| 1 Fund | Fund | `S&P 500 index fund` |
| | Run name | **change** → `R2 T0 effective-date` |
| | Management fee (bps / yr) | `0` |
| 2 Window | | defaults |
| 3 Timing rule | Rule | `EFFECTIVE_DATE` |
| | Decision offset | `5` |
| 4 Flows | Preset | `none (handoff)` |
| 5 Desk policy | | defaults |
| 6 Execution model | Model card | **T0** |
| 7 Review | Execution model line | `T0 available on CRSP data` |

**What it answers:** what does trading at the index's own effective close (the third Friday; Juneteenth
2026 moves to Jun 18), deciding 5 sessions earlier, do to tracking difference compared with the
notebook's Monday/Tuesday timing?

**Where:** **Runs** → tick R1 and R2 → **Compare (2)**. **Timing effect = TD(R1) − TD(R2)**.

**What it signifies:** this is the clean tracking baseline before any trading cost. The brief expects
TD within **±20 bps**.

### R3. T1 costs with investor flows: the headline run

**Preset button:** `S&P 500 index fund, T1 costs, flows`. This is also what the wizard opens with.

| Step | Field | Value |
|---|---|---|
| 1 Fund | Fund | `S&P 500 index fund` |
| | Run name | **change** → `R3 T1 flows` |
| | Management fee (bps / yr) | `3` |
| 2 Window | | defaults |
| 3 Timing rule | Rule | `EFFECTIVE_DATE`; the rest defaults |
| 4 Flows | Preset | `default (σ 10 bps, jumps)`: mu_bps `0`, sigma_bps `10`, phi `0.3`, nu `4`, jump_prob `0.005`, jump_min_bps `-200`, jump_max_bps `-50`, cash_buffer_bps `10`, min_ticket_usd `5000`, min_ticket_nav_bps `0.1`; netting `on` |
| 5 Desk policy | | defaults |
| 6 Execution model | Model card | **T1** |
| 7 Review | Execution model line | `T1 available on CRSP data` |

**What it answers:** what does running a $10bn S&P 500 fund cost through real BMLL closing auctions
and intraday markets, with daily investor flows and a 3 bps fee, and how much tracking difference is
that?

**Where:**
- **Fund** (R3): TD net vs gross, TE, the TD decomposition, cash drag, flows, weights vs target,
  excluded names.
- **TCA** (R3): the main analysis.
- **TCA → One rebalance date**: one quarter, order by order.
- **Blotter** (R3): orders and lineage.

**What it signifies:** cost drag (**TD(R2) − TD(R3)**, but note R3 also adds flows and the fee; S1
separates them) and where cost comes from: the auction or continuous trading, which size buckets,
which names.

### R4. T2: R3 replayed against real Trades Plus minute bars

Start this right after R3 finishes. It prefetches the bars for R3's orders in one batch.

**Preset button:** `S&P 500 index fund, T1 costs, flows`, then change the model.

| Step | Field | Value |
|---|---|---|
| 1 Fund | Run name | **change** → `R4 T2 flows` (the preset's name says T1) |
| | Management fee (bps / yr) | `3` |
| 2–5 | | exactly as R3 |
| 6 Execution model | Model card | **change** → click **T2** |
| | t2_interval_cap | `0.1` (10% of each minute's volume) |
| 7 Review | Execution model line | `T2: missing Trades Plus minute bars are fetched from BMLL during the run and cached; orders BMLL has no bars for fall back, flagged INTRADAY_FALLBACK_DAILY` |

If the execution model line is red, it names what's missing: `uv`, the puller, or the BMLL variables
for the API/worker shell (section 0, step 3).

On the run page, expect stage **fetch_bars** (the batch prefetch from R3), then **simulate**, with
`T2 bars: [n/N] …` lines in the log as BMLL queries complete. The first T2 run is slow, because it
downloads from BMLL. Later ones reuse the cache.

**What it answers:** does replaying real minute volume and NBBO mids change the cost estimate
compared with T1?

**Where:**
- **Runs** → tick R3 and R4 → **Compare (2)**: exec cost and TD side by side.
- **TCA** for R4: compare *Method comparison* and *Cost by size against liquidity* with R3.
- The fallback banner: how much notional had no bars. Above 1% of notional, the TCA is marked
  *provisional*.

**What it signifies:** a check on the T1 model against what the market actually traded each minute.
For the index fund, the default desk sends most of the rebalance to the closing auction. T2 changes
only the afternoon VWAP remainder of large orders, so a small T1 vs T2 gap is itself a finding: an
index fund's cost lives in the auction.

### R5 (optional). Known-answer test of the TCA, on live market data

This run's **orders** are generated at random with costs planted on purpose. Its **market data is
live**: real BMLL VWAPs, real names, real calendar. It tests the measuring instrument, not the fund,
so skip it if you only want fund results.

**Preset button:** `Phase 1 random order generator`

| Step | Field | Value |
|---|---|---|
| 1 Fund | Fund | `Phase 1 order generator` |
| | Run name | **change** → `R5 TCA known-answer` |
| | Strategy | `synthetic_generator` (the order generator, not synthetic data) |
| | Management fee (bps / yr) | `0` |
| 2 Window | Bootstrap / Start / End | `2026-01-02` / `2026-01-05` / `2026-03-31` |
| 3 Timing rule | | preset values (not used by the generator) |
| 4 Flows | Preset | `none (handoff)` |
| 5 Desk policy | | defaults |
| 6 Execution model | Model card | **T1** (T0 is refused for the generator) |
| 7 Review | Execution model line | `T1 available on CRSP data` |

**What it answers:** if we know the true cost of every order, does the TCA measure it? The measured
notional-weighted cost should recover the planted cost **within 0.5 bps**.

**Where:** **TCA** (R5).

**What it signifies:** if the TCA can't recover a cost we planted, nothing it says about R3 and R4
can be trusted. It's the first thing to show a skeptical quant.

---

## 3. The five studies, click by click

**Studies** (left nav). Each study is a card with a **Start study** button. One click queues all its
runs, with a message like *"Cost-model parameters: 9 runs queued"*.

- The study appears in the **Studies** list on the left, showing `done/total runs`. Click it to see
  its runs (each links to its run page) and, once they finish, the **Results** table.
- A study has no form. Its base configuration is always the **R3 settings** (`S&P 500 index fund,
  T1 costs, flows`: effective-date timing, T1, default flows, 3 bps fee, the full live window). It
  uses the latest live snapshot.
- Start studies **after R3 and R4**. The worker runs queued runs one after another.

| ID | Card to click | Runs | What each run changes from R3's settings | Question it answers | Read |
|---|---|---|---|---|---|
| **S1** | **TD decomposition chain** | 6 | Cumulative: (1) baseline: HANDOFF, T0, no flows, no fee → (2) + effective-date timing → (3) + investor flows → (4) + T1 costs → (5) + 3 bps fee → (6) + keep securities with approved corporate actions | How much tracking difference each effect causes, in a fixed printed order (effects interact, so the order matters) | **Results** waterfall: one bar per effect, plus total TD |
| **S2** | **Cost-model parameters** | 9 | base; Y ×0.5, ×1.5; κ ×0.5, ×1.5; β ×0.5, ×1.5; MOC auction share 2%, 10% | Which calibration choice moves total cost and TD the most | **Results**: Δ TD and exec cost vs base |
| **S3** | **Flow regime** | 6 | Flow σ of 5 / 10 / 25 bps a day × cash buffer of 10 / 50 bps | How flow intensity and the buffer drive cost, cash drag and TE | **Results**; **Fund** → flows for any step |
| **S4** | **Exclusion policy** | 2 | `exclude_securities` vs `keep_securities` | The TD effect of leaving out names with unresolved corporate actions (0.6–3.3% of index weight a quarter in the handoff) | **Results**; **Fund** → *Index weight excluded per decision* |
| **S5** | **Provisional dividend rule** | 2 | Rule on vs off | The NAV, TD and TE effect of booking declared but unconfirmed dividends | **Results**: Prov. dividends column |

**Before S4 and S1** (their `keep_securities` step): **Data** → tab **Corporate-action review**. For
each PENDING row, click **Approve** or **Reject**. Mergers (`MG`) can't be approved: they stay
excluded. `keep_securities` keeps only names whose actions are approved. Every run records the
review set's hash.

**In total:** R1–R4 (4 runs), plus R5 if you want it, plus 25 study runs.

---

## 4. Navigating the app

The left nav, top to bottom:

| Page | What it is | What to click / look at |
|---|---|---|
| **Overview** | Funds, recent runs, data health | AUM, TD net, TE and exec cost of the latest run; CRSP/BMLL/mapping status |
| **Fund** | The fund report for one run | **Run selector** at the top (pick the run). Then: TD net/gross, TE, return, vol, drawdown, Sharpe, ending NAV; *Fund vs benchmark*; *Daily active return*; *Rolling tracking error* (63 days); *Tracking-difference decomposition*; *Index weight excluded per decision*; *Weights vs target*; *Excluded names*; flows |
| **Runs** | All runs | Click a row for its page (config, data hashes, code version, output hash, live log). **New run** (top right). Tick 2+ checkboxes → **Compare (n)**: side by side, with the TD change per step |
| **Blotter** | Every order at four levels | Pick a run. PM order → trader orders → child orders → fills. Click a row for its lineage (decision, policy rule, broker, venue); expand to drill down |
| **TCA** | Cost analysis for one run | Pick a run. *Method comparison*; *Cost by size against liquidity*; *Weighted cost: %ADV × %auction volume* heatmap; *Cost vs Q / auction volume*; *Concentration of rebalance notional*; *Flow vs rebalance netting*; *Cost by date beside tracking difference*; *Where the methods disagree most* (click a point to open its lineage) |
| **TCA → One rebalance date** | One quarter end to end | Pick a date. Each order's auction price and volume, imbalance, ADV, spread, VWAP; three repricings; *MOC cost vs Q / auction volume*; *Named orders*. Its "T2 replay of the top ten" panel shows *T2 unavailable* on live data; use run R4 for T2 |
| **Market explorer** | One security's BMLL data | Search a ticker. Close vs VWAP, auction volume (spikes on index dates), venue share, PERMNO ↔ BMLL mapping history, corporate actions. The intraday chart is empty on live data unless the snapshot carries Trades Plus bars |
| **Studies** | Decomposition and sensitivity sets | Start a study; click it for runs and Results |
| **Data** | Lake, snapshots, review queues | Tabs: *Lake & snapshots*, *Pull jobs* (don't click Pull now; see section 0), *Mapping review*, *Corporate-action review* |
| **Settings** | Brokers, venues, users, desk policy | Commission schedules, venue fees, the **Allow-list** (users and roles), the **Desk policy (YAML)** |

### Reading the TCA

- **Notional-weighted:** every average is weighted by traded dollars. The top 10% of orders carry
  about 85% of the dollars, so a simple average would let 530 orders under $10k outvote a $104m order.
  Confidence intervals come from a block bootstrap by date (2,000 resamples).
- **Implementation shortfall = delay + execution + explicit + opportunity**, against the PM's
  decision price:
  - **delay:** decision price to arrival mid. For orders decided at a prior close, this is the
    handoff's overnight "slippage" (−250 to +180 bps). It is market drift, reported separately from
    trading cost.
  - **execution:** arrival mid to average fill price. This is the trading cost.
  - **explicit:** commissions and venue fees.
  - **opportunity:** the cost of quantity left unfilled at the deadline.
- **Repricings:** the same orders repriced at:
  - **T0** (the close, the zero-cost baseline),
  - **MOC** (closing auction),
  - **VWAP** (day VWAP),
  - **ARRIVAL** (arrival mid),
  - **HANDOFF** (the notebook's next-close fill).

  **ACTUAL** is what the run's model produced.
- **Sign:** positive cost means worse. Costs are in bps with $ beside them.
- **Size buckets:** order size as % of ADV20 and as % of expected closing-auction volume.
- **Netting:** opposite-side flow and rebalance quantity crossed internally. The saving is in $ and bps
  of NAV.
- **Provisional:** more than 1% of notional used a fallback: auction price from CRSP, auction volume
  from the 20-day median, or no Trades Plus bars in T2.

### Reading the fund report

- **TD (tracking difference):** annualised fund return minus annualised benchmark return, **net**
  (after costs and fees) and **gross** (before). Negative means the fund lagged.
- **TE (tracking error):** standard deviation of the daily active return × √252.
- **Cost drag:** TD(T0) − TD(Tk) on the same orders.
- **Cash drag:** return lost to the cash buffer and undeployed flows.
- **Timing effect:** TD(handoff timing) − TD(effective-date timing).

### The settings behind the runs

| Setting | What it changes |
|---|---|
| **Timing rule** | HANDOFF: decide Monday, fill Tuesday close (the notebook). EFFECTIVE_DATE: trade at the index's effective close, deciding 5 sessions earlier |
| **Execution model** | **T0**: close, zero cost. **T1**: auction impact `κ·σ·(Q/V_auc)^β·(1+λ·same-side)`, and continuous `½ spread + Y·σ·√(Q/ADV)` from BMLL daily data. **T2**: real Trades Plus minute bars, at most 10% of each minute's volume, NBBO mid, aggressive slices pay half the spread. **T3**: BMLL simulator, Data Lab only (not available in the app) |
| **Flows** | AR(1) Student-t daily flows (σ 10 bps) with a 0.5%-a-day redemption jump of −50 to −200 bps, dealt at the pre-flow unit NAV |
| **Desk policy (default)** | Rebalance orders (TARGET_CLOSE): a small basket goes to a portfolio trade (Broker C, guaranteed close); an order within 5% of expected auction goes MOC; a larger one sends 5% to MOC and works the rest with VWAP 13:00–15:50. Flow and cash-sweep orders go MOC |
| **Exclusion policy** | Leave out, or keep, names with pending or unsupported corporate actions |
| **Provisional dividends** | Book declared but unconfirmed dividends, or don't |

A run stops on purpose (status **FAILED**, with the session and reason) on a missing price, an
unclassified corporate action on a held name, a cash shortfall, a NAV identity break or look-ahead.
These are guarantees, not bugs: note the session and error in the run log.

---

## 5. Showing this to BMLL

### The story

1. SIMP turns a research notebook into a working asset manager **on BMLL data**. Every decision
   becomes PM order → trader orders → child orders → fills, executed against BMLL closing auctions and
   intraday markets.
2. It **reproduces the notebook first** (R1), so every later difference is attributable.
3. It **measures execution cost the way a desk would**: implementation shortfall by stage,
   notional-weighted, repriced several ways, with the instrument validated on planted costs (R5).
4. It puts **cost beside tracking difference** (R3, S1).
5. **Real intraday liquidity** (R4) checks the cost model against Trades Plus minute by minute.

### A 15-minute demo path

1. **Overview**: live data label, snapshot hash, mapping coverage.
2. **Fund** → R1: "the notebook's fund, rebuilt from live CRSP."
3. **Studies** → S1 → Results waterfall: where TD comes from, one effect at a time.
4. **TCA** → R3: *Cost by size against liquidity* and the *%ADV × %auction* heatmap.
5. **TCA → One rebalance date**: *Named orders* and *MOC cost vs Q / auction volume*.
6. **Blotter** → R3: open one large order's lineage down to its fills.
7. **Runs** → Compare R3 and R4: the T1 model vs real Trades Plus liquidity.
8. **TCA** → R5: the TCA recovering planted costs.
9. **Market explorer**: one name's auction volume around index dates, venue share, mapping history.

### Caveats to state up front

- **Costs are modelled, not actual executions.** T1 is a calibrated model and T2 a replay against
  real prints.
- **Auction imbalance is treated as zero** (the Auction Imbalance detail dataset isn't pulled yet).
  The same-side-imbalance term of the auction model never applies, so MOC cost is understated on days
  the fund trades with the imbalance.
- **T3 (the BMLL simulator) is not wired in yet.**
- **The benchmark is CRSP's cap-weighted S&P 500 with dividends** unless an official S&P 500 TR file
  was registered.
- **Excluded names** (unresolved corporate actions, no BMLL mapping) are left out and reported. S4
  quantifies the effect.
- Every run carries its data snapshot hash, review-set hash, desk-policy hash and code version, so any
  number can be rebuilt.

### Access and data rights

- Give BMLL colleagues **viewer** access: **Settings** → **Allow-list**, add the username with role
  `viewer`. For a hosted setup, use GitHub OAuth (`SIMP_AUTH_MODE=oauth`). Viewers see everything but
  can't start runs or studies.
- **Exports refuse raw vendor data and are watermarked.** Share charts, aggregates and order-level TCA,
  not CRSP or BMLL extracts. The WRDS and BMLL licences apply.

---

## 6. Run log (fill this in)

| ID | Run name | Run / study ID | Status | TD net (bps) | TE (bps) | Exec cost (bps, wtd) | Notes |
|---|---|---|---|---|---|---|---|
| R1 | R1 Handoff replay | | | | | 0 (T0) | Ending NAV vs $21.07bn: |
| R2 | R2 T0 effective-date | | | | | 0 (T0) | Timing effect vs R1: |
| R3 | R3 T1 flows | | | | | | |
| R4 | R4 T2 flows | | | | | | Fallback share: |
| R5 | R5 TCA known-answer (optional) | | | n/a | n/a | | Recovered vs planted: |
| S1 | TD decomposition chain | | | | | | |
| S2 | Cost-model parameters | | | | | | |
| S3 | Flow regime | | | | | | |
| S4 | Exclusion policy | | | | | | |
| S5 | Provisional dividend rule | | | | | | |
