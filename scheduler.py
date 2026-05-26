"""
Unified scheduler.
  Stock pipeline   : 06:00 ET  Mon-Fri
  Portfolio report : 07:00 UTC Mon-Fri  (RUN_MODE=portfolio)
  Portfolio alert  : every 4h  Mon-Fri  (RUN_MODE=alert_check)
"""
import logging
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from main import load_config, run_stock_pipeline, run_portfolio_pipeline, setup_logging

logger = logging.getLogger("scheduler")


def main():
    cfg     = load_config()
    log_cfg = cfg.get("logging", {})
    setup_logging(log_cfg.get("level", "INFO"), log_cfg.get("file", "logs/agent.log"))

    sched_cfg = cfg.get("scheduler", {})
    run_time  = sched_cfg.get("daily_run_time", "06:00")
    tz_stock  = sched_cfg.get("timezone", "America/New_York")
    hour, minute = run_time.split(":")

    scheduler = BlockingScheduler()

    # Stock pipeline — 06:00 ET
    scheduler.add_job(
        func=lambda: run_stock_pipeline(cfg),
        trigger=CronTrigger(day_of_week="mon-fri", hour=int(hour),
                            minute=int(minute), timezone=tz_stock),
        id="stock_analysis", name="Stock Selection Pipeline",
        misfire_grace_time=3600,
    )

    # Portfolio daily report — 07:00 UTC
    scheduler.add_job(
        func=lambda: run_portfolio_pipeline(run_mode="portfolio"),
        trigger=CronTrigger(day_of_week="mon-fri", hour=7,
                            minute=0, timezone="UTC"),
        id="portfolio_report", name="Portfolio Risk Report",
        misfire_grace_time=3600,
    )

    # Portfolio alert check — every 4 hours UTC
    scheduler.add_job(
        func=lambda: run_portfolio_pipeline(run_mode="alert_check"),
        trigger=CronTrigger(day_of_week="mon-fri", hour="*/4",
                            minute=0, timezone="UTC"),
        id="portfolio_alerts", name="Portfolio Alert Check",
        misfire_grace_time=1800,
    )

    logger.info(f"Scheduler started:")
    logger.info(f"  Stock pipeline   : {run_time} {tz_stock} Mon-Fri")
    logger.info(f"  Portfolio report : 07:00 UTC Mon-Fri")
    logger.info(f"  Portfolio alerts : every 4h UTC Mon-Fri")
    print("Press Ctrl+C to stop.")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")


if __name__ == "__main__":
    main()
