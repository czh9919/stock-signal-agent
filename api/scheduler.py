"""
Background scheduler:
  - Price polling every 5 min during US market hours (weekdays 14:30-21:00 UTC)
  - Full pipeline at 21:30 UTC Mon-Fri
  - Alert checks every 4h UTC Mon-Fri
"""
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from api.ws import manager
from data.storage import Storage

logger = logging.getLogger(__name__)
_db = Storage()


def _is_us_market_hours() -> bool:
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False
    h, m = now.hour, now.minute
    # 14:30 – 21:00 UTC
    return (h == 14 and m >= 30) or (15 <= h <= 20) or (h == 21 and m == 0)


def poll_prices():
    if not _is_us_market_hours():
        return
    try:
        import csv
        from pathlib import Path
        import yfinance as yf

        path = Path("config/watchlist.csv")
        if not path.exists():
            return
        with open(path, newline="", encoding="utf-8") as f:
            tickers = [row["ticker"] for row in csv.DictReader(f)]
        if not tickers:
            return

        raw = yf.download(tickers, period="1d", progress=False, threads=True)
        if raw.empty:
            return

        if hasattr(raw.columns, "levels"):
            close_all = raw["Close"]
        else:
            close_all = raw[["Close"]].rename(columns={"Close": tickers[0]})

        snapshots = []
        for ticker in tickers:
            if ticker not in close_all.columns:
                continue
            series = close_all[ticker].dropna()
            if series.empty:
                continue
            price = float(series.iloc[-1])
            _db.upsert_price_snapshot(ticker, price)
            snapshots.append({"ticker": ticker, "price": price, "currency": "USD",
                               "updated_at": series.index[-1].isoformat()})

        if snapshots:
            manager.send_prices(snapshots)
            logger.info(f"Price poll: updated {len(snapshots)} tickers")
    except Exception as e:
        logger.warning(f"Price poll failed: {e}")


def run_scheduled_pipeline(run_mode: str):
    try:
        from main import load_config, run_portfolio_pipeline
        from data.storage import Storage
        db = Storage()
        config = load_config()
        run_id = db.create_run(run_mode)
        result = run_portfolio_pipeline(run_mode=run_mode, config=config,
                                        on_log=manager.send_log)
        if result:
            db.finish_run(run_id, result.get("metrics", {}),
                          result.get("html_en", ""), result.get("html_zh", ""))
            manager.send_run_status(run_id, "success",
                                    nav_eur=result["metrics"].get("nav_eur"),
                                    rag=result["metrics"].get("overall_rag"))
        else:
            db.fail_run(run_id, "No result returned")
            manager.send_run_status(run_id, "failed")
    except Exception as e:
        logger.exception(f"Scheduled {run_mode} failed: {e}")


def create_scheduler() -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone="UTC")

    # Poll end-of-day prices via yfinance every 5 min during market hours.
    # The Massive WS (minute bars) requires a real-time plan; the Stocks Basic
    # plan is EOD only, so yfinance polling is the reliable dashboard data source.
    sched.add_job(poll_prices, "interval", minutes=5,
                  id="price_poll", name="Price Snapshot Poll")

    sched.add_job(
        lambda: run_scheduled_pipeline("full"),
        CronTrigger(day_of_week="mon-fri", hour=21, minute=30, timezone="UTC"),
        id="full_pipeline", name="Full Pipeline",
        misfire_grace_time=3600,
    )

    sched.add_job(
        lambda: run_scheduled_pipeline("alert_check"),
        CronTrigger(day_of_week="mon-fri", hour="*/4", minute=0, timezone="UTC"),
        id="alert_check", name="Alert Check",
        misfire_grace_time=1800,
    )

    return sched
