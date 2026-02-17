from datetime import datetime, timedelta, timezone

from app.models import RawItem
from app.normalizer import normalize_items


def test_normalize_items_filters_time_window() -> None:
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)

    raw_items = [
        RawItem(
            source_name="s1",
            source_weight=1.0,
            url="https://a.com/post1",
            title="AI 大模型发布",
            content="这是一条 AI 新闻",
            published_at=now - timedelta(hours=2),
            discovered_at=now,
        ),
        RawItem(
            source_name="s1",
            source_weight=1.0,
            url="https://a.com/post2",
            title="AI 行业动态",
            content="旧新闻",
            published_at=now - timedelta(hours=30),
            discovered_at=now,
        ),
    ]

    normalized = normalize_items(raw_items, since=since, until=now)
    assert len(normalized) == 1
    assert normalized[0].canonical_url == "https://a.com/post1"


def test_normalize_items_accepts_ai_tag_fallback() -> None:
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    raw_items = [
        RawItem(
            source_name="s1",
            source_weight=1.0,
            url="https://a.com/post3",
            title="生态合作动态",
            content="不包含显式关键词",
            published_at=now - timedelta(hours=1),
            discovered_at=now,
            tags=["ai", "industry"],
        )
    ]

    normalized = normalize_items(raw_items, since=since, until=now)
    assert len(normalized) == 1
    assert normalized[0].item_id
