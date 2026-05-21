# Stock Signal Agent

An automated US stock analysis agent that runs three times daily, combining technical indicators with Claude AI to generate buy/hold/sell recommendations — delivered as bilingual (English + Chinese) HTML email reports.

---

## How It Works

Each run executes a sequential pipeline:

```
Watchlist (CSV)
     │
     ▼
Data Layer          yfinance → 2yr price history + current price
                    NewsAPI  → latest headlines per ticker
                    SQLite   → persists all fetched data locally
     │
     ▼
Strategy Layer      Technical indicators (SMA/EMA/MACD/RSI/BB/ATR)
                    Signal rules → bullish / bearish / neutral score
                    Claude AI    → BUY / HOLD / SELL + confidence + rationale
     │
     ▼
Backtest Layer      30-day accuracy tracker (evaluates past recommendations)
     │
     ▼
Notify Layer        Jinja2 HTML renderer → bilingual report (EN + ZH)
                    SendGrid or SMTP → sends two emails per run
```

---

## Run Modes

The agent runs three times per weekday via GitHub Actions, each with different behavior:

| Mode | Time (Dublin) | Time (ET) | Behavior |
|---|---|---|---|
| `morning` | 09:00 | 04:00 AM | Logs analysis; emails only if confidence ≥ 75% |
| `premarket` | 14:00 | 09:00 AM | Logs analysis; emails only if confidence ≥ 75% |
| `full` | 21:30 | 04:30 PM | Always sends complete bilingual report |

**Morning & pre-market alerts** contain only high-confidence picks and include a ticker summary in the subject line, e.g.:

```
[Stock Agent] Pre-Market Alert — AAPL BUY, NVDA BUY
[股票助手] 盘前预警 — AAPL 买入, NVDA 买入
```

**After-close full report** contains every stock in the watchlist with full indicator tables, AI rationale, risk flags, and a 30-day accuracy summary.

The `alert_confidence_threshold` (default `0.75`) is configurable in `config/settings.yaml`.

---

## Modules

### `data/`

| File | Role |
|---|---|
| `fetcher.py` | Downloads 2-year OHLCV history and current price via `yfinance`; fetches up to 10 news articles per ticker via NewsAPI |
| `cleaner.py` | Validates price data — flags gaps > 3 days, removes duplicate dates, detects anomalous price spikes; validates news freshness |
| `storage.py` | SQLite wrapper — persists price history, news, AI suggestions, data warnings, and accuracy evaluations |

### `strategy/`

| File | Role |
|---|---|
| `indicators.py` | Computes SMA 20/50/200, EMA 12/26, MACD + signal line, RSI (14), Bollinger Bands (20, 2σ), ATR (14), 10-day volume SMA using the `ta` library |
| `signals.py` | Rule-based scoring — assigns bullish / bearish / neutral labels based on indicator thresholds; returns a composite score |
| `ai_analyst.py` | Sends indicator snapshot + news headlines to Claude (`claude-sonnet-4-6`); parses structured JSON response containing `recommendation`, `confidence` (0–1), `sentiment_score`, `feasibility`, `risk_factors`, `key_levels`, and `key_events` |

### `backtest/`

| File | Role |
|---|---|
| `historical.py` | SMA-20/50 crossover backtest via `vectorbt` — returns annual return, Sharpe ratio, max drawdown, win rate (optional; skipped gracefully if vectorbt not installed) |
| `tracker.py` | Compares past AI recommendations against actual price movements after 30 days; stores evaluation results and aggregates accuracy by recommendation type |

### `notify/`

| File | Role |
|---|---|
| `renderer.py` | Renders full HTML report or short alert using Jinja2; supports `lang='en'` and `lang='zh'` via a translation dictionary — no duplicated templates |
| `mailer.py` | Sends HTML email via SendGrid or SMTP (Gmail app password supported); on failure writes backup to `data/email_backup/` |
| `templates/report.html` | Responsive HTML email template with confidence color bars, warning badges, per-stock indicator tables, and accuracy section |

### Root files

| File | Role |
|---|---|
| `main.py` | Pipeline entry point — reads `RUN_MODE` env var, runs all layers, sends one English + one Chinese email |
| `scheduler.py` | APScheduler-based local runner (alternative to GitHub Actions; runs at 06:00 ET Mon–Fri) |

---

## Email Report Contents

**Full report (after close):**
- Summary table: ticker, price, recommendation badge, confidence bar, signal score, sentiment, feasibility
- Per-stock detail: all indicator values, bullish/bearish signal list, risk factors, key events, support/resistance levels
- Warning flags: low confidence, repeated BUY signals, incomplete data, missing news
- AI accuracy table: 30-day hit rate broken down by BUY / HOLD / SELL

**Alert email (morning / pre-market):**
- Compact table of high-confidence picks only
- Columns: ticker, recommendation, confidence %, signal score, rationale

Both emails are sent in English and Chinese on every triggered run.

---

## Risk Controls

| Control | Behavior |
|---|---|
| Max 20 stocks/day | Watchlist is capped; edit `limits.max_stocks_per_day` in settings |
| Max 50 Claude calls/day | Hard cap on AI API usage |
| Confidence < 0.5 | Orange "Low Confidence" warning in report |
| 3+ consecutive BUY signals | "Signal repeated — caution" warning |
| Price data gap > 3 days | "Data incomplete" label on that stock |
| NewsAPI failure | Falls back to technical-only analysis; no crash |
| Email send failure | Saves HTML to `data/email_backup/report_YYYY-MM-DD.html` |

---

## Quick Start (local)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Fill in your API keys in .env

# 3. Run once
python main.py

# 4. Run in a specific mode
RUN_MODE=premarket python main.py

# 5. Start the local daily scheduler (06:00 ET, Mon–Fri)
python scheduler.py
```

---

## Watchlist

Edit `config/watchlist.csv` — changes take effect on the next run.

```csv
ticker,weight,notes
AAPL,1.0,Apple Inc
MSFT,1.0,Microsoft Corp
NVDA,1.0,NVIDIA Corp
```

---

## Configuration

All tunable parameters are in `config/settings.yaml`. API keys are injected via `${ENV_VAR}` references — never hardcoded.

```yaml
limits:
  max_stocks_per_day: 20
  max_claude_calls_per_day: 50
  news_per_stock: 10
  history_days: 730

claude:
  model: claude-sonnet-4-6
  confidence_warning_threshold: 0.5   # orange warning below this
  alert_confidence_threshold: 0.75    # morning/premarket only email above this
  consecutive_buy_warning: 3

scheduler:
  daily_run_time: "06:00"
  timezone: "America/New_York"
```

---

## Environment Variables

| Variable | Where to get it | Required |
|---|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com | Yes |
| `NEWS_API_KEY` | newsapi.org | Recommended |
| `SENDGRID_API_KEY` | sendgrid.com | Yes (or use SMTP) |
| `RECIPIENT_EMAIL` | your inbox | Yes |
| `ALPHA_VANTAGE_KEY` | alphavantage.co | Optional |
| `SMTP_HOST` | e.g. `smtp.gmail.com` | If `use_smtp: true` |
| `SMTP_PORT` | e.g. `587` | If `use_smtp: true` |
| `SMTP_USER` | your email address | If `use_smtp: true` |
| `SMTP_PASS` | Gmail app password | If `use_smtp: true` |

---

## GitHub Actions

Two workflows are included:

| Workflow | Trigger | What it does |
|---|---|---|
| `daily_run.yml` | Cron × 3/day + manual dispatch | Detects run mode from UTC hour, runs `main.py`, sends bilingual emails |
| `ci.yml` | Push / PR to main or master | Syntax-checks all Python source files |

**Setup:** Go to **Settings → Secrets and variables → Actions** and add each variable from the table above as a repository secret.

**Manual trigger:** Actions → Daily Stock Analysis → Run workflow → choose `morning`, `premarket`, or `full`.

---

## Tech Stack

| Layer | Libraries |
|---|---|
| Data | `yfinance`, `newsapi-python`, `pandas`, `numpy` |
| Indicators | `ta` (0.11+) |
| AI | `anthropic` (Claude Sonnet) |
| Storage | `sqlite3` (stdlib) |
| Backtest | `vectorbt` (optional) |
| Email | `sendgrid`, `smtplib` (stdlib) |
| Templates | `Jinja2` |
| Scheduling | `APScheduler` / GitHub Actions |
