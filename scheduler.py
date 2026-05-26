"""
Unified scheduler.
  Full pipeline (factor + portfolio) : 21:30 UTC Mon-Fri (after US close)
  Portfolio alert check              : every 4h  Mon-Fri
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

from main import load_config, run_portfolio_pipeline, setup_logging

logger = logging.getLogger("scheduler")


def main():
    cfg     = load_config()
    log_cfg = cfg.get("logging", {})
    setup_logging(log_cfg.get("level", "INFO"), log_cfg.get("file", "logs/agent.log"))

    scheduler = BlockingScheduler()

    # Full pipeline — 21:30 UTC (after US market close)
    scheduler.add_job(
        func=lambda: run_portfolio_pipeline(run_mode="full", config=cfg),
        trigger=CronTrigger(day_of_week="mon-fri", hour=21, minute=30, timezone="UTC"),
        id="full_pipeline", name="Full Factor + Portfolio Pipeline",
        misfire_grace_time=3600,
    )

    # Portfolio alert check — every 4 hours UTC
    scheduler.add_job(
        func=lambda: run_portfolio_pipeline(run_mode="alert_check", config=cfg),
        trigger=CronTrigger(day_of_week="mon-fri", hour="*/4",
                            minute=0, timezone="UTC"),
        id="portfolio_alerts", name="Portfolio Alert Check",
        misfire_grace_time=1800,
    )

    logger.info("Scheduler started:")
    logger.info("  Full pipeline  : 21:30 UTC Mon-Fri (after US close)")
    logger.info("  Alert checks   : every 4h UTC Mon-Fri")
    print("Press Ctrl+C to stop.")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")


if __name__ == "__main__":
    main()
