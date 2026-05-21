"""
APScheduler entry point.
Runs the daily pipeline at 06:00 ET every weekday.
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

from main import load_config, run_daily_pipeline, setup_logging

logger = logging.getLogger("scheduler")


def main():
    cfg     = load_config()
    log_cfg = cfg.get("logging", {})
    setup_logging(log_cfg.get("level", "INFO"), log_cfg.get("file", "logs/agent.log"))

    sched_cfg = cfg.get("scheduler", {})
    run_time  = sched_cfg.get("daily_run_time", "06:00")
    timezone  = sched_cfg.get("timezone", "America/New_York")
    hour, minute = run_time.split(":")

    scheduler = BlockingScheduler(timezone=timezone)
    scheduler.add_job(
        func=lambda: run_daily_pipeline(cfg),
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=int(hour),
            minute=int(minute),
            timezone=timezone,
        ),
        id="daily_stock_analysis",
        name="Daily Stock Analysis",
        misfire_grace_time=3600,
    )

    logger.info(f"Scheduler started — daily run at {run_time} {timezone} (Mon–Fri)")
    print(f"Scheduler running. Daily report at {run_time} {timezone}. Press Ctrl+C to stop.")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")


if __name__ == "__main__":
    main()
