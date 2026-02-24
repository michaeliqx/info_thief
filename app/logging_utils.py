from __future__ import annotations

import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    """配置日志输出到 stdout，便于 supervisor 将正常日志写入 .log、异常写入 .err.log"""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        stream=sys.stdout,
    )
