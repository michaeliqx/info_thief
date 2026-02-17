from __future__ import annotations

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import load_settings
from app.logging_utils import setup_logging
from app.pipeline import run_daily_pipeline

logger = logging.getLogger(__name__)


def _parse_hhmm(value: str) -> tuple[int, int]:
    hour_str, minute_str = value.split(":", 1)
    return int(hour_str), int(minute_str)


def main() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)

    hour, minute = _parse_hhmm(settings.collector_trigger_time)
    tz = ZoneInfo(settings.timezone)

    scheduler = BlockingScheduler(timezone=tz)
    scheduler.add_job(
        run_daily_pipeline,
        trigger=CronTrigger(hour=hour, minute=minute, timezone=tz),
        kwargs={"settings_path": "config/settings.yaml", "sources_path": "config/sources.yaml", "push": None},
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        id="daily-ai-brief",
        replace_existing=True,
    )

    logger.info(
        "Scheduler started, trigger=%s, timezone=%s, now=%s",
        settings.collector_trigger_time,
        settings.timezone,
        datetime.now(tz).isoformat(),
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")
    finally:
        time.sleep(0.1)


if __name__ == "__main__":
    main()
