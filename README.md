# Stock AI Agent

AI-driven US stock analysis agent. Fetches market data, calculates technical indicators, calls Claude for investment recommendations, and emails a daily HTML report.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment variables
cp .env.example .env
# Edit .env with your API keys

# 3. Run once manually
python main.py

# 4. Start daily scheduler (06:00 ET, Mon–Fri)
python scheduler.py
```

## Environment Variables

| Variable | Source | Required |
|---|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com | Yes |
| `NEWS_API_KEY` | newsapi.org | Recommended |
| `SENDGRID_API_KEY` | sendgrid.com | Yes (or SMTP) |
| `RECIPIENT_EMAIL` | your config | Yes |
| `ALPHA_VANTAGE_KEY` | alphavantage.co | Optional |
| `SMTP_USER` / `SMTP_PASSWORD` | your SMTP server | If use_smtp: true |

## Project Structure

```
stock-agent/
├── config/
│   ├── settings.yaml     # Config (API keys via env vars)
│   └── watchlist.csv     # Stock pool
├── data/
│   ├── fetcher.py        # yfinance + NewsAPI
│   ├── cleaner.py        # Anomaly detection
│   └── storage.py        # SQLite
├── strategy/
│   ├── indicators.py     # pandas-ta indicators
│   ├── signals.py        # Signal rules
│   └── ai_analyst.py     # Claude API
├── backtest/
│   ├── historical.py     # vectorbt backtest
│   └── tracker.py        # Live accuracy tracking
├── notify/
│   ├── renderer.py       # Jinja2 HTML report
│   ├── mailer.py         # SendGrid / SMTP
│   └── templates/
│       └── report.html
├── scheduler.py          # APScheduler (daily cron)
└── main.py               # Manual trigger
```

## Editing the Watchlist

Edit `config/watchlist.csv` — changes take effect on the next scheduled run.

```csv
ticker,weight,notes
AAPL,1.0,Apple Inc
MSFT,1.0,Microsoft Corp
```

## GitHub Actions (automated daily run)

The repo includes two workflows:

| Workflow | Trigger | What it does |
|---|---|---|
| `daily_run.yml` | 06:00 EST Mon–Fri + manual | Runs `main.py` and sends email report |
| `ci.yml` | Push / PR to main | Syntax-checks all Python files |

**Setup — add secrets to your repo:**

Go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key |
| `NEWS_API_KEY` | newsapi.org key |
| `SENDGRID_API_KEY` | SendGrid API key |
| `RECIPIENT_EMAIL` | Email address to receive reports |
| `ALPHA_VANTAGE_KEY` | Alpha Vantage key (optional) |
| `SMTP_HOST` | e.g. `smtp.gmail.com` (if using SMTP) |
| `SMTP_PORT` | e.g. `587` |
| `SMTP_USER` | Your Gmail address |
| `SMTP_PASS` | Gmail app password |

To trigger a run manually: **Actions → Daily Stock Analysis → Run workflow**.

## Risk Controls (hardcoded)

- Max 20 stocks/day, max 50 Claude API calls/day
- Confidence < 0.5 → orange warning in email
- 3+ consecutive BUY signals → "signal repeated — caution" warning
- Price data gap > 3 days → "data incomplete" label
- News API failure → falls back to technical-only analysis
- Email send failure → saves HTML backup to `data/email_backup/`
