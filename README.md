# Stock AI Agent

An automated portfolio risk and factor analysis system. A single unified pipeline fetches live broker holdings, runs Fama-French 5-factor regressions on every watchlist stock, computes a full risk dashboard, and delivers a bilingual (English + Chinese) HTML email report after each US market close.

All trading decisions remain manual. The system exists to give you clean, quantitative input for those decisions.

---

## Known Limitations

### Backtest Survivorship Bias

The walk-forward backtest (`RUN_MODE=backtest`) fetches the **current** S&P 500 constituent list from Wikipedia. Companies removed through bankruptcy, forced acquisition, or index ejection are **never included** in the historical universe. This overstates out-of-sample Sharpe ratios and understates drawdowns. Treat figures as **upper-bound estimates**.

Eliminating the bias requires a point-in-time constituent database (CRSP, Compustat, EODHD). Every backtest result is flagged `[!] SURVIVORSHIP BIAS`.

### VaR Model Risk: Normal Distribution Assumption

Plain EWMA VaR (`var_95_ewma`) underestimates tail losses by 15–30% in stress regimes because equity returns are negatively skewed and fat-tailed. Three estimates are always computed side-by-side:

| Metric | Method | Role |
|---|---|---|
| `var_95_ewma` / `var_99_ewma` | Normal EWMA | Reference — underestimates in stress |
| `var_95_cf` / `var_99_cf` | **Cornish-Fisher** — adjusts for empirical skewness and excess kurtosis | **Primary alert trigger and report display** |
| `var_99_evt` | **EVT / Generalized Pareto** (peaks-over-threshold) | Most reliable for extreme quantiles; falls back to historical if < 20 exceedances |

For 21-day horizon tail risk the Monte Carlo DCC-GARCH + Hawkes simulation provides the most comprehensive fat-tail coverage.

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│               UNIFIED PIPELINE  (main.py)                │
│                                                          │
│  Holdings   IBKR Flex Query XML                         │
│  fetched    T212 REST API                               │
│  from:      eToro (mock/CSV)                            │
│             ↓ fallback to local JSON cache              │
│      │                                                   │
│      ▼                                                   │
│  Price Loader   yfinance equity prices + FX rates       │
│                 EUR base: EURUSD=X, EURGBP=X, etc.      │
│                 Staleness checks; auto_adjust=True       │
│      │                                                   │
│      ▼                                                   │
│  FF5 Factor Model   (RUN_MODE=full)                     │
│    ┌────────────────────────────────────────────────┐    │
│    │ pandas-datareader → Ken French FF5 daily data  │    │
│    │ OLS per watchlist equity (252-day window,      │    │
│    │   fallback to available, min 63 days)          │    │
│    │ Outputs per stock:                             │    │
│    │   α (ann.), t(α), IR, R², β_MKT/SMB/HML/      │    │
│    │   RMW/CMA, signal (t>1.5→BUY / t<−1.5→SELL)  │    │
│    │ Portfolio aggregate: market-value-weighted β  │    │
│    │ Performance attribution: last 21 trading days │    │
│    │   RF + Σβ·factor + residual α                 │    │
│    └────────────────────────────────────────────────┘    │
│      │                                                   │
│      ▼                                                   │
│  Risk Engine    EWMA VaR (λ=0.94)                       │
│                 Cornish-Fisher VaR (primary alert)      │
│                 EVT/GPD VaR (extreme quantiles)         │
│                 CVaR / Expected Shortfall               │
│                 Sharpe ratio, Beta vs SPY               │
│                 Max drawdown, HHI concentration         │
│      │                                                   │
│      ▼                                                   │
│  Monte Carlo    DCC-GARCH + Hawkes self-exciting jumps  │
│                 50 000 paths × 21 days                  │
│                 Irish CGT 33%, trading costs,           │
│                 IBKR RegT margin monitoring             │
│      │                                                   │
│      ▼                                                   │
│  Stress Tests   2020 COVID crash, 2022 rate shock,      │
│                 2008 GFC, custom scenario                │
│      │                                                   │
│      ▼                                                   │
│  Optimizer      Markowitz efficient frontier            │
│                 FF5-implied μ (same window, no look-    │
│                 ahead); 3-tier rebalancing suggestions  │
│                 with α-based "why" decision trace       │
│                 Marginal impact for bond/gold           │
│      │                                                   │
│      ▼                                                   │
│  Alert System   Threshold checks → 24h-deduped emails   │
│      │                                                   │
│      ▼                                                   │
│  Notify Layer   Bilingual HTML email + embedded         │
│                 risk-return chart PNG (CID inline)      │
└──────────────────────────────────────────────────────────┘
```

---

## Run Modes

Set via the `RUN_MODE` environment variable:

| Mode | GitHub Actions time (UTC) | Behavior |
|---|---|---|
| `full` | 21:30 (16:30 ET, after US close) | Full pipeline: factor analysis for all watchlist equities + portfolio risk report |
| `portfolio` | Manual / local | Portfolio risk report only; skips watchlist factor ranking |
| `alert_check` | 14:00 (09:00 ET, pre-market) | Threshold check only; fires alert emails when a metric is RED; no report |
| `backtest` | Manual | Walk-forward validation — FF5 μ vs historical mean μ in Markowitz |

---

## Modules

### `data/`

| File | Role |
|---|---|
| `fetcher.py` | Downloads OHLCV (`auto_adjust=True`) and current price via yfinance |
| `cleaner.py` | Validates price data — detects gaps > 10 trading days, price spikes > 50%, and **stale last-row** (last close > 4 calendar days old); validates news freshness |
| `storage.py` | SQLite wrapper — stores price history, data warnings, and backtest evaluations |
| `price_loader.py` | Loads equity price history into `PriceData` objects (`ok` / `reduced` / `excluded` / `unavailable`); fetches FX rates with staleness warning; EUR base via inverted `EURUSD=X` / `EURGBP=X` / `EURAUD=X` |
| `fetch_holdings.py` | Aggregates live positions from IBKR (Flex Query XML), T212 (REST API), and eToro; converts all values to EUR |
| `cache.py` | **Atomic** JSON snapshot writes (`.tmp` → rename); corrupt-file recovery; staleness warning when fallback snapshot is > 2 days old; 7-day rolling retention |
| `spy_universe.py` | Scrapes current S&P 500 tickers from Wikipedia; bulk yfinance download for walk-forward universe; 20h price cache |

### `strategy/`

| File | Role |
|---|---|
| `indicators.py` | SMA 20/50/200, EMA 12/26, MACD + signal, RSI (14), Bollinger Bands (20, 2σ), ATR (14), 10-day volume SMA |
| `signals.py` | Rule-based scoring — assigns bullish/bearish/neutral labels; returns composite score |
| `factor_model.py` | **FF5 regression engine**: `run_factor_regression` — OLS of excess returns on 5 Fama-French factors; 252-day window, min 63 days; outputs α, t(α), IR, R², 5 betas, BUY/SELL/HOLD signal. `portfolio_factor_exposure` — market-value-weighted aggregate betas. `compute_attribution` — decomposes last 21-day portfolio return into factor contributions + residual α. `enrich_suggestions` — attaches α-based "why" reason to each rebalancing suggestion. |

### `backtest/`

| File | Role |
|---|---|
| `historical.py` | SMA-20/50 crossover backtest via `vectorbt` (optional); returns annual return, Sharpe, max drawdown, win rate |
| `walk_forward.py` | Rolling walk-forward validation: FF5-implied μ vs historical mean μ in Markowitz across 3-year train / 1-quarter test windows. FF5 factor premiums **restricted to the training window** (no look-ahead). Assets must be present from the start of each training window to prevent covariance truncation. Reports `[!] SURVIVORSHIP BIAS` on every result. |

### `risk/`

| File | Role |
|---|---|
| `risk_engine.py` | 1-day risk metrics: EWMA VaR (λ=0.94), **Cornish-Fisher VaR** (primary alert trigger), **EVT/GPD VaR** (99%, peaks-over-threshold), Historical VaR, CVaR, Sharpe (rf=3.5% ECB), Beta vs SPY, max drawdown, HHI, sector concentration; RED/AMBER/GREEN RAG |
| `mc_engine.py` | **DCC-GARCH + Hawkes** path simulator. Per-asset GARCH(1,1) volatilities; time-varying correlation via DCC recursion; self-exciting Hawkes jump process calibrated from tail frequency. Vectorised: 50 000 paths × 21 days in ~1 s. Fallback: CC-GARCH → GBM. |
| `mc_portfolio.py` | Buy-and-hold simulation over `mc_engine` paths. Applies: Irish CGT 33% on gains > €1 270 exemption; trading costs (commission + half-spread + √ market-impact); IBKR RegT 25% margin breach flag. Returns VaR/CVaR/drawdown/Sharpe/Sortino/Calmar. |
| `stress.py` | Historical scenario stress tests: COVID-19 (Mar 2020, −34%), 2022 rate shock (−25%), 2008 GFC (−57%); EUR loss and % drawdown per scenario |
| `optimizer.py` | Markowitz optimisation with **Fama-French 5-factor implied μ** (factor premiums restricted to training window). `compute_frontier` — MC cloud + SLSQP max-Sharpe; `rebalancing_suggestions` — 3-tier vs optimal; `marginal_impact` — ΔSharpe/ΔVol for bond/gold candidates. |

### `notify/`

| File | Role |
|---|---|
| `report_gen.py` | Renders bilingual portfolio risk HTML report with all sections listed below |
| `chart.py` | Generates the efficient frontier PNG using headless matplotlib (Agg backend, CJK font fallback); returns raw bytes for CID inline embedding |
| `mailer.py` | Sends HTML email via SMTP (primary) or SendGrid (fallback); supports CID-embedded inline images; writes backup to `data/email_backup/` on failure |

### Root files

| File | Role |
|---|---|
| `main.py` | Unified entry point — reads `RUN_MODE`, runs portfolio + factor analysis pipeline |
| `scheduler.py` | APScheduler local runner: full pipeline at 21:30 UTC, alert check every 4h UTC (Mon–Fri) |
| `alert.py` | Fires bilingual alert emails for RED metrics; 24-hour deduplication via `cache/alert_dedup.json` |

---

## FF5 Factor Model

### Per-Stock Regression

`run_factor_regression()` solves via OLS:

```
Rᵢ − RF = α + β_MKT·(Mkt-RF) + β_SMB·SMB + β_HML·HML + β_RMW·RMW + β_CMA·CMA + ε
```

| Output | Description |
|---|---|
| `alpha_ann` | Annualised intercept α = α_daily × 252 |
| `t_alpha` | OLS t-statistic of α; t > 1.5 → BUY, t < −1.5 → SELL |
| `ir` | Information Ratio = (α_daily / σ_residuals) × √252 |
| `r_squared` | Model fit quality |
| `beta_mkt/smb/hml/rmw/cma` | Factor loadings |
| `signal` | BUY / SELL / HOLD — driven by statistical significance of α |

**Window:** last 252 aligned trading days (falls back to available if < 252 but ≥ 63; skips ticker entirely if < 63).

The stock factor table is **sorted by IR** — stocks with the highest risk-adjusted excess return appear first.

### Portfolio Factor Exposure

`portfolio_factor_exposure()` computes market-value-weighted averages of each beta and α across equity holdings. Shown in the report as a single aggregate row: useful for detecting unintended style tilts (e.g., unintended growth/value bet, concentration in small-caps).

### Performance Attribution

`compute_attribution()` decomposes the realized equity portfolio return over the **last 21 trading days** into:

```
R_portfolio = RF + β_MKT·(Mkt-RF) + β_SMB·SMB + β_HML·HML + β_RMW·RMW + β_CMA·CMA + α_residual
```

| Row | Meaning |
|---|---|
| Each factor row | β × realized factor return over the period |
| Factor total | Sum of all factor contributions + RF |
| **Alpha (stock picking)** | Actual return − factor total — tells you whether your holdings outperformed their factor model |

Large positive α: stock selection added value beyond what the factor tilts explain.  
Large negative α: you're being compensated only for beta exposure, not for selection.

### Decision Trace

`enrich_suggestions()` attaches a concise reason to every rebalancing suggestion, cross-referencing the FF5 regression:

- `α=+8.2% t=+2.3 → BUY signal; underweight` — optimizer is increasing weight of a statistically significant outperformer
- `α=−3.8% t=−1.8 → SELL signal; overweight` — optimizer is trimming an underperformer
- `α=+1.4% t=+0.9; trim to reduce concentration` — neutral alpha, purely a concentration reduction

This lets you evaluate each manual trade decision in terms of the underlying factor signal, not just the weight delta.

---

## Portfolio Risk Metrics

### Risk Dashboard

| Metric | Threshold (RED) | Method |
|---|---|---|
| VaR 95% (1-day) | > 5% | **Cornish-Fisher** (EWMA σ adjusted for skewness + kurtosis) |
| CVaR/VaR ratio | > 1.80× | Expected Shortfall / Historical simulation |
| Max Drawdown | > 20% | Rolling peak-to-trough |
| Largest position | > 30% weight | — |
| Single-day loss | > 3% | Previous close vs today |
| HHI concentration | > 0.25 | Herfindahl–Hirschman Index |
| Beta vs SPY | > 1.50 | 252-day rolling regression |
| Sharpe ratio | < 0.50 | rf = 3.5% (ECB) |

RAG thresholds are fully configurable in `config/thresholds.yaml`.

### VaR Methodology

| Key | Formula | Notes |
|---|---|---|
| `var_95_ewma` | `−μ + 1.645 · σ_EWMA` | Normal; underestimates fat tails — reference only |
| `var_95_cf` | `−μ − z_cf · σ_EWMA` where `z_cf = z + (z²−1)S/6 + (z³−3z)K/24 − (2z³−5z)S²/36` | Cornish-Fisher; **primary alert trigger** |
| `var_99_evt` | GPD fit to worst-10% losses via `scipy.stats.genpareto` | EVT peaks-over-threshold; most reliable at 99%+ |

### Monte Carlo (21-day horizon)

`run_mc_portfolio()` runs 50 000 DCC-GARCH + Hawkes paths:

| Output | Description |
|---|---|
| VaR 95% / 99% | Empirical percentile of terminal P&L distribution |
| CVaR 95% | Mean loss in worst 5% of paths |
| Max drawdown (mean / p95) | Peak-to-trough along each path |
| P(loss > 10%) / P(loss > 20%) | Loss-probability ladder |
| Margin call probability | IBKR RegT breach on any day |
| Sharpe / Sortino / Calmar | Median across paths |
| Trading costs | Commission + half-spread + √ market-impact |
| CGT (median) | Irish CGT 33% on realised gains > €1 270 |

Configured in `config/settings.yaml` under `monte_carlo:`.

---

## Markowitz Optimisation

### Efficient Frontier

`compute_frontier()` samples 1 200 random Dirichlet portfolios, then solves for the max-Sharpe portfolio via SLSQP. Expected returns come from FF5-implied μ using the same in-sample window (no look-ahead). The chart is a matplotlib PNG embedded inline (CID) in the email.

### Rebalancing Suggestions (3-tier)

| Tier | Condition | Label |
|---|---|---|
| 1 — Act | \|Δweight\| ≥ 5%, no Irish CGT obstacle | Red — immediate action |
| 2 — Watch | 2–5% delta, or reducing a winner (CGT 33% applies) | Amber — monitor |
| 3 — Hold | \|Δweight\| < 2% | Grey — trade cost exceeds benefit |

Each suggestion includes an α-based **decision trace** explaining the underlying factor signal.

### Diversification Candidates (Bonds & Gold)

`marginal_impact()` evaluates each bond/gold entry from the watchlist: ΔSharpe, ΔVol, correlation to current equity portfolio, suggested weight, and FX rate note.

Current candidates in `config/watchlist.csv`: TLT, IEF, AGG (Treasuries), GLD.

---

## Walk-Forward Backtest

`RUN_MODE=backtest` runs a rolling walk-forward validation:

| Parameter | Default | Description |
|---|---|---|
| Training window | 756 days (~3 yr) | Used to fit betas, covariance, and FF5 premiums |
| Test window | 63 days (~1 quarter) | Out-of-sample Sharpe, max drawdown, turnover |
| Min asset history | 252 days | Asset excluded from a window if data starts mid-window |

**Bias controls:** FF5 factor premiums restricted to training slice; assets must have data from the first 5 days of the training window; test gaps forward-filled then zero-filled.

**Output flags (always shown):**
- `[!] SURVIVORSHIP BIAS` — universe is current S&P 500 only
- `[OK] No look-ahead` — FF5 premiums restricted to training window

---

## Email Report Contents

### Portfolio Risk Report (`RUN_MODE=full` or `portfolio`)

- RAG status badge (RED / AMBER / GREEN) with active alert list
- Snapshot cards: NAV (€), P&L, VaR 95% (Cornish-Fisher), Sharpe, bond exposure %
- Week-on-week delta indicators (▲/▼ pp)
- Stress test table: EUR loss + % drawdown for COVID / rate shock / GFC
- **Portfolio Factor Exposures** — aggregate β_MKT, β_SMB, β_HML, β_RMW, β_CMA and α for current holdings
- **Performance Attribution** — last 21-day return decomposed into factor contributions + residual α (stock-picking contribution highlighted)
- **Stock Factor Rankings** — watchlist equities sorted by IR with α, t(α), β_MKT, R², and BUY/SELL/HOLD signal
- Embedded efficient frontier chart (MC cloud + current + max-Sharpe)
- Rebalancing suggestions with **decision trace** (α-based "why" under each ticker)
- Diversification candidates (bonds + gold, sorted by ΔSharpe, with FX rates)
- Monte Carlo table: 21-day VaR/CVaR/drawdown/loss-probabilities, Sharpe/Sortino, costs
- Correlation breakdown: normal σ vs crisis σ, diversification decay ratio
- Top-10 equity positions
- Bond holdings (coupon, maturity, weight, P&L %)
- Data quality footer

### Alert Emails (`RUN_MODE=alert_check`)

Fires a separate bilingual alert email for each RED metric not already sent in the last 24 hours (`cache/alert_dedup.json`).

---

## Data Quality Checks

| Check | Threshold | Where |
|---|---|---|
| Price staleness | Last close > 4 calendar days old → WARNING | `cleaner.py`, `price_loader.py` |
| Data gaps | > 10 missing trading days in window → WARNING | `cleaner.py` |
| Price spikes | Single-day move > 50% → WARNING | `cleaner.py` |
| FX staleness | FX rate > 4 days old → WARNING (still used) | `price_loader.py` |
| Cache staleness | Snapshot fallback > 2 days old → WARNING | `cache.py` |
| Corrupt cache | Invalid JSON silently skipped; next file tried | `cache.py` |
| Split/dividend adjustment | `auto_adjust=True` explicit on all yfinance calls | `price_loader.py`, `fetcher.py`, `spy_universe.py` |

---

## Watchlist

`config/watchlist.csv` — edit and commit; changes take effect on next run.

```csv
ticker,weight,notes,asset_class,currency
AAPL,1.0,Apple Inc,equity,USD
MSFT,1.0,Microsoft Corp,equity,USD
NVDA,1.0,NVIDIA Corp,equity,USD
TLT,1.0,iShares 20+ Year Treasury Bond ETF,bond,USD
GLD,1.0,SPDR Gold Shares,gold,USD
```

- `asset_class=equity` rows are included in the FF5 factor ranking
- `asset_class=bond` and `gold` rows are evaluated as diversification candidates
- `notes` becomes the display name in the candidates table

---

## Configuration

### `config/settings.yaml`

```yaml
limits:
  history_days: 730

factor_model:
  window_days: 252        # OLS regression window; falls back if fewer days available
  min_window_days: 63     # skip ticker if aligned days < this

monte_carlo:
  n_paths:  50000
  horizon:  21
  model:    hawkes   # hawkes | garch | gbm
```

### `config/thresholds.yaml`

Per-metric alert thresholds, severity levels, and bilingual labels. All configurable without code changes.

### `config/volatility.yaml`

EWMA lambda (0.94), GARCH parameters, lookback windows, ECB risk-free rate (3.5%).

---

## Environment Variables

### Required

| Variable | Purpose |
|---|---|
| `SMTP_HOST` | Outbound mail server (e.g. `smtp.gmail.com`) |
| `SMTP_PORT` | Usually `587` |
| `SMTP_USER` | Sender email address |
| `SMTP_PASS` | Gmail app password or SMTP password |
| `REPORT_TO_EMAIL` | Portfolio report recipient |
| `RECIPIENT_EMAIL` | Additional recipient |

### Recommended

| Variable | Purpose |
|---|---|
| `SENDGRID_API_KEY` | SendGrid fallback if SMTP fails |
| `ALPHA_VANTAGE_KEY` | Optional price data enrichment |

### Broker APIs (at least one needed for live portfolio)

| Variable | Platform |
|---|---|
| `IBKR_FLEX_TOKEN` + `IBKR_QUERY_ID` | Interactive Brokers Flex Query |
| `T212_API_KEY` + `T212_API_SECRET` | Trading 212 REST API |
| `ETORO_API_KEY` | eToro (mock/CSV if absent) |

If all broker APIs fail the pipeline falls back to the most recent local snapshot in `cache/`.

---

## Quick Start (local)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Fill in SMTP_*, REPORT_TO_EMAIL at minimum

# 3. Full pipeline after US market close
RUN_MODE=full python main.py

# 4. Portfolio risk only (skip watchlist factor ranking)
RUN_MODE=portfolio python main.py

# 5. Alert check only (no report)
RUN_MODE=alert_check python main.py

# 6. Walk-forward backtest
RUN_MODE=backtest python main.py

# 7. Local scheduler (full pipeline 21:30 UTC, alerts every 4h)
python scheduler.py
```

---

## GitHub Actions

### Workflows

| File | Trigger | What it does |
|---|---|---|
| `daily_run.yml` | Cron × 2/day + manual dispatch | Resolves `RUN_MODE` from UTC hour; runs `main.py`; sends bilingual report |
| `ci.yml` | Push/PR to main or master | Syntax-checks all Python files; import smoke test including factor model, attribution, decision trace, and report section assertions |

### Cron Schedule

| UTC time | Dublin (IST) | New York (ET) | Mode |
|---|---|---|---|
| 14:00 | 15:00 | 10:00 | `alert_check` |
| 21:30 | 22:30 | 17:30 | `full` |

### Setup

1. Go to **Settings → Secrets and variables → Actions**
2. Add each variable from the environment table as a repository secret
3. Push to `master` — CI runs automatically; daily analysis starts from the next scheduled cron

**Manual trigger:** Actions → Daily Stock & Portfolio Analysis → Run workflow → choose a run mode.

---

## Tech Stack

| Layer | Libraries |
|---|---|
| Data | `yfinance`, `pandas`, `numpy`, `pandas-datareader` (FF5 factors via Ken French library) |
| Factor model | `numpy` (OLS), FF5 regression, performance attribution |
| Indicators | `ta` (0.11+) |
| Risk models | `scipy` (EVT/GPD, SLSQP), `arch` (GARCH) |
| Monte Carlo | `numpy` vectorised DCC-GARCH + Hawkes (50 000 paths in ~1 s) |
| Optimisation | `scipy.optimize.minimize` (SLSQP — Markowitz max-Sharpe) |
| Charts | `matplotlib` (headless Agg, CJK font fallback — CID-embedded PNG) |
| Backtest | `vectorbt` (optional, SMA crossover), walk-forward (built-in) |
| Email | `smtplib` (stdlib), `sendgrid` |
| Scheduling | `APScheduler` / GitHub Actions |
| Config | `PyYAML`, `python-dotenv` |
