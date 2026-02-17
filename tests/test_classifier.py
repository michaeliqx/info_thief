from datetime import datetime, timezone

from app.classifier import classify_items
from app.models import NormalizedItem, Perspective


def _item(title: str) -> NormalizedItem:
    now = datetime.now(timezone.utc)
    return NormalizedItem(
        item_id=title,
        source_name="s",
        source_weight=1.0,
        url="https://x.com",
        canonical_url="https://x.com",
        title=title,
        content=title,
        published_at=now,
        discovered_at=now,
        language="zh",
        tags=[],
    )


def test_rule_classification() -> None:
    items = [
        _item("某公司发布 AI 应用新版本"),
        _item("新论文提出高效推理架构"),
        _item("AI 初创公司完成新一轮融资"),
    ]

    out = classify_items(items)
    assert out[0].perspective == Perspective.PRODUCT
    assert out[1].perspective == Perspective.TECHNOLOGY
    assert out[2].perspective == Perspective.INDUSTRY
