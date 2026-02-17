from __future__ import annotations

import logging

from app.config import load_settings
from app.logging_utils import setup_logging
from app.pipeline import run_daily_pipeline


def main() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)

    brief = run_daily_pipeline()
    logging.getLogger(__name__).info("Daily run completed: %s (%d items)", brief.title, len(brief.items))


if __name__ == "__main__":
    main()
