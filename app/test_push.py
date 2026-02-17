from __future__ import annotations

import logging
from datetime import date

from app.config import load_settings
from app.logging_utils import setup_logging
from app.models import BriefItem, DailyBrief, Perspective
from app.publisher import push_markdown, render_markdown


def main() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)

    demo = DailyBrief(
        date=date.today(),
        title="AI 每日情报 | 推送测试",
        intro="这是推送链路测试消息。",
        items=[
            BriefItem(
                perspective=Perspective.PRODUCT,
                title="测试条目",
                key_points=["用于验证企业微信 webhook 可用性", "该消息由 python -m app.test_push 发送"],
                source_name="system",
                url="https://example.com",
                score=0.0,
            )
        ],
        observations=["如果收到该消息，说明推送链路正常。"],
    )

    content = render_markdown(demo)
    ok = push_markdown(settings.wechat_webhook, content, retries=())
    if ok:
        logging.getLogger(__name__).info("Push test succeeded")
    else:
        logging.getLogger(__name__).error("Push test failed")


if __name__ == "__main__":
    main()
