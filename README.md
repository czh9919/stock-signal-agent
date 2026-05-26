# Stock AI Agent

An automated investment analysis system with two parallel pipelines:

1. **Stock Pipeline** — scans a watchlist of US equities using technical indicators + Claude AI, generating bilingual BUY / HOLD / SELL recommendations
2. **Portfolio Risk Pipeline** — fetches live holdings from IBKR / T212 / eToro, computes a full risk dashboard (VaR, CVaR, Sharpe, Beta, drawdown, concentration), runs Markowitz optimisation, and identifies diversification opportunities in bonds and gold

Both pipelines deliver bilingual (English + Chinese) HTML email reports. The base currency is **EUR** (Ireland / Eurozone). Tax logic uses **Irish CGT (33%)**.

---

## Known Limitations

### Backtest Survivorship Bias

The walk-forward backtest (`RUN_MODE=backtest`) fetches the **current** S&P 500 constituent list from Wikipedia. Companies that were index members but were subsequently removed — through bankruptcy (e.g., SVB, Lehman Brothers), forced acquisition, or index ejection — are **never included** in the historical universe.

This systematically excludes the worst-performing securities, overstating out-of-sample Sharpe ratios and understating drawdowns. Treat reported backtest figures as **upper-bound estimates**, not live-trading forecasts.

Eliminating this bias requires a point-in-time constituent database (CRSP, Compustat, EODHD, or Tiingo historical index composition). The `print_summary()` output flags every backtest result with `[!] SURVIVORSHIP BIAS`.

### VaR Model Risk: Normal Distribution Assumption

The plain EWMA VaR (`var_95_ewma`) multiplies EWMA volatility by the normal quantile (z = 1.645 at 95%). Equity returns are **negatively skewed and fat-tailed** — the normal distribution underestimates tail losses by roughly 15–30% in stress regimes.

Three estimates are computed to bound the true risk:

| Metric | Method | Role |
|---|---|---|
| `var_95_ewma` / `var_99_ewma` | Normal EWMA | Reference only — underestimates in stress |
| `var_95_cf` / `var_99_cf` | **Cornish-Fisher** — adjusts for empirical skewness and excess kurtosis | Primary alert trigger and report display |
| `var_99_evt` | **EVT / Generalized Pareto** (peaks-over-threshold) | Most reliable for extreme quantiles; falls back to historical VaR if < 20 exceedances |

The **alert system and report cards use `var_95_cf`** as the primary threshold trigger.

For 21-day horizon tail risk, the Monte Carlo DCC-GARCH + Hawkes simulation provides the most comprehensive fat-tail coverage, independent of any distributional assumption.

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                     STOCK PIPELINE                       │
│                                                          │
│  watchlist.csv (equity rows)                            │
│       │                                                  │
│       ▼                                                  │
│  Data Layer      yfinance → 2yr OHLCV + current price  │
│                  NewsAPI  → headlines per ticker        │
│                  SQLite   → persists all fetched data   │
│       │                                                  │
│       ▼                                                  │
│  Strategy Layer  SMA/MACD/RSI/BB/ATR indicators        │
│                  Rule-based signal scoring              │
│                  Claude AI → BUY/HOLD/SELL + rationale │
│       │                                                  │
│       ▼                                                  │
│  Backtest Layer  30-day accuracy tracker               │
│       │                                                  │
│       ▼                                                  │
│  Notify Layer    Bilingual HTML email (EN + ZH)        │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│                  PORTFOLIO RISK PIPELINE                 │
│                                                          │
│  Holdings  IBKR Flex Query XML                          │
│  fetched   T212 REST API                                │
│  from:     eToro (mock/CSV)                             │
│            ↓ fallback to local JSON cache               │
│       │                                                  │
│       ▼                                                  │
│  Price Loader   yfinance equity prices + FX rates      │
│                 (EUR base: EURUSD=X, EURGBP=X, etc.)   │
│                 Staleness checks; auto_adjust=True      │
│       │                                                  │
│       ▼                                                  │
│  Risk Engine    EWMA VaR (λ=0.94)                      │
│                 Cornish-Fisher VaR (primary alert)     │
│                 EVT/GPD VaR (extreme quantiles)        │
│                 CVaR / Expected Shortfall              │
│                 Sharpe ratio, Beta vs SPY              │
│                 Max drawdown, HHI concentration        │
│       │                                                  │
│       ▼                                                  │
│  Monte Carlo    DCC-GARCH + Hawkes self-exciting jumps │
│                 50 000 paths × 21 days                 │
│                 Irish CGT 33%, trading costs,          │
│                 IBKR RegT margin monitoring            │
│       │                                                  │
│       ▼                                                  │
│  Stress Tests   2020 COVID crash, 2022 rate shock,     │
│                 2008 GFC, custom scenario               │
│       │                                                  │
│       ▼                                                  │
│  Optimizer      Markowitz efficient frontier           │
│                 FF5-implied μ (Fama-French 5-factor)   │
│                 3-tier rebalancing suggestions         │
│                 Marginal impact for bond/gold          │
│       │                                                  │
│       ▼                                                  │
│  Alert System   Threshold checks → 24h-deduped emails  │
│       │                                                  │
│       ▼                                                  │
│  Notify Layer   Bilingual HTML email + embedded        │
│                 risk-return chart PNG (CID inline)     │
└──────────────────────────────────────────────────────────┘
```

---

## Run Modes

Set via the `RUN_MODE` environment variable:

| Mode | GitHub Actions time | Behavior |
|---|---|---|
| `stock` | 09:00 UTC (Dublin pre-open) | Stock watchlist analysis only; bilingual recommendation report |
| `alert_check` | 14:00 UTC (09:00 ET pre-market) | Portfolio threshold check; fires alert emails only if a metric is RED |
| `portfolio` | Manual / local scheduler 07:00 UTC | Full portfolio risk report; no stock analysis |
| `full` | 21:30 UTC (16:30 ET after close) | Both pipelines: stock report + portfolio risk report |
| `backtest` | Manual | Walk-forward validation of FF5-implied μ vs historical mean μ |

---

## Modules

### `data/`

| File | Role |
|---|---|
| `fetcher.py` | Downloads 2-year OHLCV (`auto_adjust=True`) and current price via yfinance; fetches up to 10 headlines per ticker via NewsAPI |
| `cleaner.py` | Validates price data — detects gaps > 10 trading days, price spikes > 50%, and **stale last-row** (last close > 4 calendar days old, flags possible halt or feed freeze); validates news freshness |
| `storage.py` | SQLite wrapper — stores price history, news, AI suggestions, data warnings, backtest evaluations |
| `price_loader.py` | Loads equity price history (`auto_adjust=True`) into `PriceData` objects (status: `ok` / `reduced` / `excluded` / `unavailable`); fetches FX rates with staleness warning; EUR base via inverted `EURUSD=X` / `EURGBP=X` / `EURAUD=X` |
| `fetch_holdings.py` | Aggregates live positions from IBKR (Flex Query XML), T212 (REST API), and eToro; converts all values to EUR; detects bonds by asset category or ticker pattern |
| `cache.py` | **Atomic** JSON snapshot writes (`.tmp` → rename); corrupt-file recovery; staleness warning when fallback snapshot is > 2 days old; 7-day rolling retention |
| `spy_universe.py` | Scrapes current S&P 500 tickers from Wikipedia; bulk yfinance download for walk-forward universe; 20h price cache |

### `strategy/`

| File | Role |
|---|---|
| `indicators.py` | Computes SMA 20/50/200, EMA 12/26, MACD + signal, RSI (14), Bollinger Bands (20, 2σ), ATR (14), 10-day volume SMA |
| `signals.py` | Rule-based scoring — assigns bullish/bearish/neutral labels; returns composite score |
| `ai_analyst.py` | Sends indicator snapshot + news to Claude (`claude-sonnet-4-6`); returns structured JSON with `recommendation`, `confidence`, `sentiment_score`, `feasibility`, `risk_factors`, `key_levels`, `key_events` |

### `backtest/`

| File | Role |
|---|---|
| `historical.py` | SMA-20/50 crossover backtest via `vectorbt` (optional); returns annual return, Sharpe, max drawdown, win rate |
| `tracker.py` | Evaluates past AI recommendations after 30 days against actual price; aggregates accuracy by recommendation type |
| `walk_forward.py` | Rolling walk-forward validation: FF5-implied μ vs historical mean μ in Markowitz across 3-year train / 1-quarter test windows. FF5 factor premiums are **restricted to the training window** (no look-ahead). Assets must be present from the start of each training window to prevent covariance truncation. Reports `[!] SURVIVORSHIP BIAS` on every result. |

### `risk/`

| File | Role |
|---|---|
| `risk_engine.py` | Core 1-day risk metrics: EWMA VaR (λ=0.94), **Cornish-Fisher VaR** (primary alert trigger — adjusts for skewness and excess kurtosis), **EVT/GPD VaR** (99%, peaks-over-threshold), Historical VaR, CVaR, Sharpe (rf=3.5% ECB), Beta vs SPY, max drawdown, HHI, sector concentration; RED/AMBER/GREEN RAG |
| `mc_engine.py` | **DCC-GARCH + Hawkes** path simulator. Per-asset GARCH(1,1) volatilities; time-varying correlation via DCC recursion fitted by quasi-MLE; self-exciting Hawkes jump process calibrated from empirical tail frequency. Fully vectorised: 50 000 paths × 21 days in ~1 s. Fallback: CC-GARCH → GBM. |
| `mc_portfolio.py` | Buy-and-hold portfolio simulation over `mc_engine` paths. Applies at horizon-end: Irish CGT 33% on realised gains above €1 270 exemption; trading costs (commission + half-spread + √ market-impact); IBKR RegT 25% maintenance margin breach flag. Returns VaR/CVaR/drawdown/Sharpe/Sortino/Calmar metrics. |
| `stress.py` | Historical scenario stress tests: COVID-19 (Mar 2020, –34%), 2022 rate shock (–25%), 2008 GFC (–57%); returns EUR loss and % drawdown per scenario |
| `optimizer.py` | Markowitz optimisation with **Fama-French 5-factor implied μ** (via `pandas-datareader`; factor premiums restricted to training window for backtest integrity). `compute_frontier` — Monte Carlo cloud + SLSQP max-Sharpe; `rebalancing_suggestions` — 3-tier vs optimal; `marginal_impact` — ΔSharpe/ΔVol for bond/gold candidates. |

### `notify/`

| File | Role |
|---|---|
| `renderer.py` | Renders stock analysis HTML reports; bilingual via translation dict |
| `report_gen.py` | Renders portfolio risk HTML report: snapshot cards (Cornish-Fisher VaR), stress table, Monte Carlo tail-risk table, efficient frontier chart, rebalancing suggestions, diversification candidates, top-10 positions; bilingual (EN + ZH) |
| `chart.py` | Generates the risk-return efficient frontier PNG using headless matplotlib (Agg backend, CJK font fallback); returns raw bytes for CID inline embedding |
| `mailer.py` | Sends HTML email via SMTP (primary) or SendGrid (fallback); supports CID-embedded inline images; writes backup to `data/email_backup/` on failure |

### Root files

| File | Role |
|---|---|
| `main.py` | Unified entry point; reads `RUN_MODE`, dispatches to stock / portfolio / backtest pipelines |
| `scheduler.py` | APScheduler local runner: stock pipeline at 06:00 ET, portfolio report at 07:00 UTC, alert check every 4h UTC (Mon–Fri) |
| `alert.py` | Fires bilingual alert emails for RED metrics; 24-hour deduplication via `cache/alert_dedup.json` |

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
| Sector concentration | > 40% | Single-sector weight |
| Beta vs SPY | > 1.50 | 252-day rolling regression |
| Sharpe ratio | < 0.50 | rf = 3.5% (ECB) |

RAG thresholds are fully configurable in `config/thresholds.yaml`.

### VaR Methodology

Three estimates are always computed side-by-side:

| Key | Formula | Notes |
|---|---|---|
| `var_95_ewma` | `−μ + 1.645 · σ_EWMA` | Normal; underestimates fat tails — reference only |
| `var_95_cf` | `−μ − z_cf · σ_EWMA` where `z_cf = z + (z²−1)S/6 + (z³−3z)K/24 − (2z³−5z)S²/36` | Cornish-Fisher; **primary alert trigger** |
| `var_99_evt` | GPD fit to worst-10% losses via `scipy.stats.genpareto` | EVT peaks-over-threshold; most reliable at 99%+ |

### Monte Carlo (21-day horizon)

`run_mc_portfolio()` runs 50 000 DCC-GARCH + Hawkes paths and reports:

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

### Volatility Models (`config/volatility.yaml`)

```yaml
ewma:
  lambda: 0.94          # RiskMetrics standard
  init_window: 21       # days to seed σ²₀

garch:
  omega: 0.000001
  alpha: 0.05
  beta:  0.94

windows:
  fixed:        252     # Sharpe, Beta, MaxDD
  correlation:   63     # rolling Pearson (~1 quarter)
  min_var:       21     # minimum days to include in risk calcs

risk_free_rate: 0.035   # ECB rate (Ireland / Eurozone)
```

---

## Markowitz Optimisation

### Expected Returns: Fama-French 5-Factor μ

`ff_implied_mu()` estimates annualised expected returns using FF5 betas:

```
E[Rᵢ] = rf + αᵢ·252 + βᵢ · E[factors] · 252
```

Betas are estimated via OLS on the training slice. Factor premiums are taken from the **same in-sample window** to prevent look-ahead bias in backtests. Falls back to historical mean when `pandas-datareader` is unavailable or fewer than 60 aligned days exist.

### Efficient Frontier Chart

`compute_frontier()` samples 1 200 Monte Carlo portfolios (random Dirichlet weights), computes each portfolio's annualised return and volatility, then solves for the max-Sharpe portfolio using SLSQP. The chart is a matplotlib PNG embedded inline (CID) in the email.

### Rebalancing Suggestions (3-tier)

`rebalancing_suggestions()` compares current equity weights to max-Sharpe optimal weights:

| Tier | Condition | Label |
|---|---|---|
| 1 — Act | \|Δweight\| ≥ 5%, no Irish CGT obstacle | Red — immediate action |
| 2 — Watch | 2–5% delta, or reducing a position with unrealised gain (Irish CGT 33% applies) | Amber — monitor |
| 3 — Hold | \|Δweight\| < 2% (trade cost exceeds benefit) | Grey — leave unchanged |

### Diversification Candidates (Bonds & Gold)

`marginal_impact()` evaluates each bond/gold entry from the watchlist by comparing the max-Sharpe portfolio *without* vs *with* the candidate:

- **ΔSharpe** — improvement in risk-adjusted return
- **ΔVol** — change in portfolio volatility
- **Correlation** to current equity portfolio (lower = better diversifier)
- **Suggested weight** — optimizer-derived allocation
- **FX note** — displays `1 USD = €x.xxx` for non-EUR assets

Current bond/gold candidates in `config/watchlist.csv`: TLT, IEF, AGG (Treasury bonds), GLD (gold).

---

## Walk-Forward Backtest

`RUN_MODE=backtest` runs a rolling walk-forward validation comparing two expected-return models in Markowitz optimisation:

| Parameter | Default | Description |
|---|---|---|
| Training window | 756 days (~3 yr) | Used to fit betas, covariance, and FF5 premiums |
| Test window | 63 days (~1 quarter) | Out-of-sample Sharpe, max drawdown, turnover |
| Min asset history | 252 days | Asset excluded from a window if data starts mid-window |

**Bias controls:**
- FF5 factor premiums computed from the training slice only (no look-ahead)
- Assets must have data from the first 5 days of each training window (prevents covariance truncation by recently-listed stocks)
- Test-period gaps forward-filled then zero-filled

**Output flags** (always shown in `print_summary()`):
- `[!] SURVIVORSHIP BIAS` — universe is current S&P 500 only
- `[OK] No look-ahead` — FF5 premiums restricted to training window

---

## Email Report Contents

### Stock Report (`RUN_MODE=stock` or `full`)

- Summary table: ticker, price, recommendation badge, confidence bar, signal score, sentiment, feasibility
- Per-stock: all indicator values, bullish/bearish signal list, risk factors, key events, support/resistance levels
- Warning flags: low confidence, repeated BUY signals, incomplete data, missing news, stale price feed
- 30-day AI accuracy summary broken down by BUY / HOLD / SELL

### Portfolio Risk Report (`RUN_MODE=portfolio`, `full`)

- RAG status badge (RED / AMBER / GREEN) with active alert list
- Snapshot cards: NAV (€), total P&L, 1-day VaR 95% (Cornish-Fisher), Sharpe ratio, bond exposure %
- Week-on-week delta indicators (▲/▼ percentage points)
- Stress test table: EUR loss and % drawdown for COVID / rate shock / GFC scenarios
- Monte Carlo table: 21-day VaR/CVaR/drawdown/loss-probabilities, decision metrics (Sharpe/Sortino), costs (trading + CGT)
- Embedded efficient frontier chart (Monte Carlo cloud + current portfolio + max-Sharpe point)
- Rebalancing suggestions table (3-tier, with CGT flags)
- Diversification candidates table (bonds + gold, sorted by ΔSharpe, with FX rates)
- Top-10 positions: market value, cost basis, unrealised P&L, weight

### Alert Emails (`RUN_MODE=alert_check`)

Fires a separate bilingual alert email for each RED metric that hasn't already been sent in the last 24 hours (deduplication via `cache/alert_dedup.json`).

---

## Data Quality Checks

The pipeline applies the following checks at every run:

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
IEF,1.0,iShares 7-10 Year Treasury Bond ETF,bond,USD
GLD,1.0,SPDR Gold Shares,gold,USD
```

- `asset_class`: `equity` rows feed the stock pipeline; `bond` and `gold` rows are used as diversification candidates in the portfolio risk report
- `weight` is the watchlist weight (used for stock pipeline ranking); actual portfolio weights come from broker holdings
- The `notes` field becomes the display name in the diversification candidates table

---

## Configuration

### `config/settings.yaml`

```yaml
limits:
  max_stocks_per_day: 20
  max_claude_calls_per_day: 50
  news_per_stock: 10
  history_days: 730

claude:
  model: claude-sonnet-4-6
  confidence_warning_threshold: 0.5   # orange warning below this
  alert_confidence_threshold: 0.75    # for morning/premarket filter
  consecutive_buy_warning: 3

monte_carlo:
  n_paths:  50000   # simulation paths
  horizon:  21      # trading-day forecast horizon
  model:    hawkes  # hawkes | garch | gbm

scheduler:
  daily_run_time: "06:00"
  timezone: "America/New_York"
```

### `config/thresholds.yaml`

Defines per-metric alert thresholds, severity levels, and bilingual labels. All values are configurable without code changes.

### `config/volatility.yaml`

Controls EWMA lambda, GARCH parameters, lookback windows, and the ECB risk-free rate (3.5%).

---

## Environment Variables

### Required

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude AI (console.anthropic.com) |
| `SMTP_HOST` | Outbound mail server (e.g. `smtp.gmail.com`) |
| `SMTP_PORT` | Usually `587` |
| `SMTP_USER` | Sender email address |
| `SMTP_PASS` | Gmail app password or SMTP password |
| `REPORT_TO_EMAIL` | Portfolio risk report recipient |
| `RECIPIENT_EMAIL` | Stock analysis report recipient |

### Recommended

| Variable | Purpose |
|---|---|
| `NEWS_API_KEY` | NewsAPI headlines (newsapi.org); falls back to technical-only if absent |
| `SENDGRID_API_KEY` | SendGrid fallback if SMTP fails |

### Broker APIs (at least one needed for live portfolio)

| Variable | Platform |
|---|---|
| `IBKR_FLEX_TOKEN` + `IBKR_QUERY_ID` | Interactive Brokers Flex Query |
| `T212_API_KEY` + `T212_API_SECRET` | Trading 212 REST API |
| `ETORO_API_KEY` | eToro (mock/CSV if absent) |

If all broker APIs are absent or fail, the pipeline falls back to the most recent local snapshot in `cache/`.

### Optional

| Variable | Purpose |
|---|---|
| `ALPHA_VANTAGE_KEY` | Additional price data enrichment |

---

## Quick Start (local)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Fill in ANTHROPIC_API_KEY, SMTP_*, RECIPIENT_EMAIL at minimum

# 3. Run the stock pipeline only
RUN_MODE=stock python main.py

# 4. Run the portfolio risk pipeline only
RUN_MODE=portfolio python main.py

# 5. Run both pipelines
RUN_MODE=full python main.py

# 6. Run alert check only (no report)
RUN_MODE=alert_check python main.py

# 7. Run walk-forward backtest
RUN_MODE=backtest python main.py

# 8. Start local scheduler (stock 06:00 ET, portfolio 07:00 UTC, alerts every 4h)
python scheduler.py
```

---

## GitHub Actions

### Workflows

| File | Trigger | What it does |
|---|---|---|
| `daily_run.yml` | Cron × 3/day + manual dispatch | Resolves `RUN_MODE` from UTC hour; runs `main.py`; sends bilingual emails |
| `ci.yml` | Push/PR to main or master | Syntax-checks all Python files; import smoke test with assertions for EUR fields, frontier chart, rebalancing, and diversification candidates |

### Cron Schedule

| UTC time | Dublin (IST) | New York (ET) | Mode |
|---|---|---|---|
| 09:00 | 10:00 | 05:00 | `stock` |
| 14:00 | 15:00 | 10:00 | `alert_check` |
| 21:30 | 22:30 | 17:30 | `full` |

### Setup

1. Go to **Settings → Secrets and variables → Actions**
2. Add each variable from the environment table as a **repository secret**
3. Push to `master` — CI runs automatically; daily analysis starts from the next scheduled cron

**Manual trigger:** Actions → Daily Stock & Portfolio Analysis → Run workflow → choose a run mode.

---

## Tech Stack

| Layer | Libraries |
|---|---|
| Data | `yfinance`, `newsapi-python`, `pandas`, `numpy`, `pandas-datareader` (FF5 factors) |
| Indicators | `ta` (0.11+) |
| AI | `anthropic` (Claude Sonnet 4.6) |
| Risk models | `numpy`, `scipy` (EVT/GPD, SLSQP), `arch` (GARCH) |
| Monte Carlo | `numpy` vectorised DCC-GARCH + Hawkes (50 000 paths in ~1 s) |
| Optimisation | `scipy.optimize.minimize` (SLSQP — Markowitz max-Sharpe) |
| Charts | `matplotlib` (headless Agg, CJK font fallback — CID-embedded PNG) |
| Storage | `sqlite3` (stdlib) |
| Backtest | `vectorbt` (optional), walk-forward (built-in) |
| Email | `smtplib` (stdlib), `sendgrid` |
| Scheduling | `APScheduler` / GitHub Actions |
| Config | `PyYAML`, `python-dotenv` |
