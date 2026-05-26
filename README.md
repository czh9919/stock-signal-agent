# Stock AI Agent

An automated investment analysis system with two parallel pipelines:

1. **Stock Pipeline** — scans a watchlist of US equities using technical indicators + Claude AI, generating bilingual BUY / HOLD / SELL recommendations
2. **Portfolio Risk Pipeline** — fetches live holdings from IBKR / T212 / eToro, computes a full risk dashboard (VaR, CVaR, Sharpe, Beta, drawdown, concentration), runs Markowitz optimisation, and identifies diversification opportunities in bonds and gold

Both pipelines deliver bilingual (English + Chinese) HTML email reports. The base currency is **EUR** (Ireland / Eurozone). Tax logic uses **Irish CGT (33%)**.

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
│       │                                                  │
│       ▼                                                  │
│  Risk Engine    EWMA VaR (λ=0.94)                      │
│                 GARCH(1,1) VaR                          │
│                 CVaR / Expected Shortfall              │
│                 Monte Carlo (10 000 paths, 30-day)     │
│                 Sharpe ratio, Beta vs SPY              │
│                 Max drawdown, HHI concentration        │
│                 Sector concentration                   │
│       │                                                  │
│       ▼                                                  │
│  Stress Tests   2020 COVID crash, 2022 rate shock,     │
│                 2008 GFC, custom scenario               │
│       │                                                  │
│       ▼                                                  │
│  Optimizer      Markowitz efficient frontier (Monte    │
│                 Carlo cloud + max-Sharpe portfolio)    │
│                 3-tier rebalancing suggestions         │
│                 Marginal impact for bond/gold candidates│
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

---

## Modules

### `data/`

| File | Role |
|---|---|
| `fetcher.py` | Downloads 2-year OHLCV and current price via `yfinance`; fetches up to 10 headlines per ticker via NewsAPI |
| `cleaner.py` | Validates price data — detects gaps > 3 days, duplicate dates, price spikes; validates news freshness |
| `storage.py` | SQLite wrapper — stores price history, news, AI suggestions, data warnings, backtest evaluations |
| `price_loader.py` | Loads equity price history from yfinance into `PriceData` objects (status: `ok` / `reduced` / `failed`); fetches FX rates with EUR as base (`EURUSD=X` inverted, `EURGBP=X` inverted) |
| `fetch_holdings.py` | Aggregates live positions from IBKR (Flex Query XML), T212 (REST API), and eToro; converts all values to EUR; detects bonds by asset category or ticker pattern |
| `cache.py` | Saves daily portfolio snapshots as `cache/portfolio_YYYY-MM-DD.json`; loads the most recent or week-ago snapshot when live holdings are unavailable |

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

### `risk/`

| File | Role |
|---|---|
| `risk_engine.py` | Core risk metrics: EWMA VaR (λ=0.94), GARCH(1,1) VaR, Historical VaR, CVaR, Monte Carlo simulation (10 000 paths / 30-day horizon), Sharpe ratio (rf=3.5% ECB), Beta vs SPY, max drawdown, HHI concentration, sector concentration; assigns RED/AMBER/GREEN RAG per metric |
| `stress.py` | Historical scenario stress tests: COVID-19 (Mar 2020, –34%), 2022 rate shock (–25%), 2008 GFC (–57%); returns EUR loss and % drawdown per scenario |
| `optimizer.py` | Markowitz mean-variance optimisation: (1) `compute_frontier` — Monte Carlo portfolio cloud + max-Sharpe via SLSQP with 5 multi-start × 3 tolerance levels; (2) `rebalancing_suggestions` — 3-tier suggestions vs optimal weights; (3) `marginal_impact` — ΔSharpe / ΔVol / correlation for adding a candidate asset |

### `notify/`

| File | Role |
|---|---|
| `renderer.py` | Renders stock analysis HTML reports; bilingual via translation dict; no duplicated templates |
| `report_gen.py` | Renders portfolio risk HTML report: snapshot cards, stress table, efficient frontier chart, rebalancing suggestions, diversification candidates, top-10 positions; bilingual (EN + ZH) |
| `chart.py` | Generates the risk-return efficient frontier PNG using headless matplotlib (Agg backend); returns raw bytes for CID inline embedding |
| `mailer.py` | Sends HTML email via SMTP (primary) or SendGrid (fallback); supports CID-embedded inline images (`multipart/related`); writes backup to `data/email_backup/` on failure |

### Root files

| File | Role |
|---|---|
| `main.py` | Unified entry point; reads `RUN_MODE`, dispatches to `run_stock_pipeline` and/or `run_portfolio_pipeline` |
| `scheduler.py` | APScheduler local runner: stock pipeline at 06:00 ET, portfolio report at 07:00 UTC, alert check every 4h UTC (Mon–Fri) |
| `alert.py` | Fires bilingual alert emails for RED metrics; 24-hour deduplication via `cache/alert_dedup.json` |

---

## Portfolio Risk Metrics

### Risk Dashboard

| Metric | Threshold (RED) | Method |
|---|---|---|
| VaR 95% (1-day) | > 5% | EWMA (λ = 0.94) |
| CVaR/VaR ratio | > 1.80× | Expected Shortfall |
| Max Drawdown | > 20% | Rolling peak-to-trough |
| Largest position | > 30% weight | — |
| Single-day loss | > 3% | Previous close vs today |
| HHI concentration | > 0.25 | Herfindahl–Hirschman Index |
| Sector concentration | > 40% | Single-sector weight |
| Beta vs SPY | > 1.50 | 252-day rolling regression |
| Sharpe ratio | < 0.50 | rf = 3.5% (ECB) |

RAG thresholds are fully configurable in `config/thresholds.yaml`.

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
  monte_carlo:   63     # EWMA covariance for MC paths
  min_var:       21     # minimum days to include in risk calcs

monte_carlo:
  paths:        10000
  horizon_days:    30

risk_free_rate: 0.035   # ECB rate (Ireland / Eurozone)
```

---

## Markowitz Optimisation

### Efficient Frontier Chart

`compute_frontier()` samples 1 200 Monte Carlo portfolios (random Dirichlet weights), computes each portfolio's annualised return and volatility, then solves for the max-Sharpe portfolio using SLSQP. The chart is a matplotlib PNG embedded inline (CID) in the email.

### Rebalancing Suggestions (3-tier)

`rebalancing_suggestions()` compares current equity weights to max-Sharpe optimal weights:

| Tier | Condition | Label |
|---|---|---|
| 1 — Act | \|Δweight\| ≥ 5%, no Irish CGT obstacle | Red — immediate action |
| 2 — Watch | 2–5% delta, or reducing a position with unrealised gain (Irish CGT 33% applies) | Amber — monitor |
| 3 — Hold | \|Δweight\| < 2% (trade cost exceeds benefit) | Grey — leave unchanged |

The CGT flag fires when a cut trade would realise a gain, noting the Irish 33% CGT rate.

### Diversification Candidates (Bonds & Gold)

`marginal_impact()` evaluates each bond/gold entry from the watchlist by comparing the max-Sharpe portfolio *without* vs *with* the candidate:

- **ΔSharpe** — improvement in risk-adjusted return
- **ΔVol** — change in portfolio volatility
- **Correlation** to current equity portfolio (lower = better diversifier)
- **Suggested weight** — optimizer-derived allocation
- **FX note** — displays `1 USD = €x.xxx` for non-EUR assets

Current bond/gold candidates in `config/watchlist.csv`: TLT, IEF, AGG (Treasury bonds), GLD (gold).

---

## Email Report Contents

### Stock Report (`RUN_MODE=stock` or `full`)

- Summary table: ticker, price, recommendation badge, confidence bar, signal score, sentiment, feasibility
- Per-stock: all indicator values, bullish/bearish signal list, risk factors, key events, support/resistance levels
- Warning flags: low confidence, repeated BUY signals, incomplete data, missing news
- 30-day AI accuracy summary broken down by BUY / HOLD / SELL

### Portfolio Risk Report (`RUN_MODE=portfolio`, `full`)

- RAG status badge (RED / AMBER / GREEN) with active alert list
- Snapshot cards: NAV (€), total P&L, 1-day VaR 95%, Sharpe ratio, bond exposure %
- Week-on-week delta indicators (▲/▼ percentage points)
- Stress test table: EUR loss and % drawdown for COVID / rate shock / GFC scenarios
- Embedded efficient frontier chart (Monte Carlo cloud + current portfolio + max-Sharpe point)
- Rebalancing suggestions table (3-tier, with CGT flags)
- Diversification candidates table (bonds + gold, sorted by ΔSharpe, with FX rates)
- Top-10 positions: market value, cost basis, unrealised P&L, weight

### Alert Emails (`RUN_MODE=alert_check`)

Fires a separate bilingual alert email for each RED metric that hasn't already been sent in the last 24 hours (deduplication via `cache/alert_dedup.json`).

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

scheduler:
  daily_run_time: "06:00"
  timezone: "America/New_York"
```

### `config/thresholds.yaml`

Defines per-metric alert thresholds, severity levels, and bilingual labels. All values are configurable without code changes.

### `config/volatility.yaml`

Controls EWMA lambda, GARCH parameters, lookback windows, Monte Carlo paths, and the ECB risk-free rate (3.5%).

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

# 7. Start local scheduler (stock 06:00 ET, portfolio 07:00 UTC, alerts every 4h)
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
| Data | `yfinance`, `newsapi-python`, `pandas`, `numpy` |
| Indicators | `ta` (0.11+) |
| AI | `anthropic` (Claude Sonnet 4.6) |
| Risk models | `numpy`, `scipy`, `arch` (GARCH), `pandas` |
| Optimisation | `scipy.optimize.minimize` (SLSQP — Markowitz max-Sharpe) |
| Charts | `matplotlib` (headless Agg — CID-embedded PNG) |
| Storage | `sqlite3` (stdlib) |
| Backtest | `vectorbt` (optional) |
| Email | `smtplib` (stdlib), `sendgrid` |
| Scheduling | `APScheduler` / GitHub Actions |
| Config | `PyYAML`, `python-dotenv` |
